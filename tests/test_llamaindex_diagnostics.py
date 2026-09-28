import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.frameworks.llamaindex import diagnostics as diag


def test_timing_preserves_result_exception_and_context(caplog):
    caplog.set_level(logging.INFO, logger=diag.logger.name)
    state = diag.RequestTiming(enabled=True)
    token = diag.current_request.set(state)
    try:
        with pytest.raises(ValueError, match="test failure"):
            with diag.timed("sql_generation"):
                with diag.timed("llm_call", call="llm"):
                    raise ValueError("test failure")
        assert state.llm_calls == 1
        assert diag.current_stage.get() == "unclassified"
        assert "purpose=sql_generation" in caplog.text
        assert "outcome=error" in caplog.text
        assert "test failure" not in caplog.text
    finally:
        diag.current_request.reset(token)


def test_postgres_cursor_success_and_error_are_logged_without_payloads(caplog):
    caplog.set_level(logging.INFO, logger=diag.logger.name)
    state = diag.RequestTiming(enabled=True)
    token = diag.current_request.set(state)
    connection = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    context = SimpleNamespace()
    try:
        diag.before_cursor_execute(connection, None, "secret SQL", {"secret": 1}, context, False)
        diag.after_cursor_execute(connection, None, "secret SQL", {}, context, False)
        diag.before_cursor_execute(connection, None, "secret SQL", {}, context, False)
        diag.handle_error(SimpleNamespace(execution_context=context))
        assert caplog.text.count("stage=postgres_cursor_execute") == 2
        assert "outcome=error" in caplog.text
        assert "secret" not in caplog.text
        assert not hasattr(context, "_rag_timing")
    finally:
        diag.current_request.reset(token)


@pytest.fixture
def offline_rag(monkeypatch):
    """Run the real installed query engine with only external I/O replaced."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from llama_index.core import Settings
    from llama_index.core.schema import TextNode
    from llama_index.core.vector_stores.types import VectorStoreQueryResult
    from ollama import Client
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import StaticPool
    from app.api.rag_router import router
    from app.frameworks.llamaindex import runtime, answering

    saved_llm, saved_embedding = Settings._llm, Settings._embed_model
    cached = [runtime.get_llamaindex_llm, runtime.get_llamaindex_embed_model,
              runtime.get_sql_database, runtime.get_literature_store, runtime.get_vector_index_from_postgres]
    for factory in cached:
        factory.cache_clear()
    database = create_engine("sqlite://", poolclass=StaticPool,
                             connect_args={"check_same_thread": False})
    with database.begin() as conn:
        conn.execute(text("CREATE TABLE readings (temp_c FLOAT)"))
        conn.execute(text("INSERT INTO readings VALUES (23.5)"))
    monkeypatch.setattr(runtime, "create_engine", lambda *a, **k: database)
    def execute(statement):
        with database.connect() as connection:
            return [dict(row) for row in connection.execute(text(statement)).mappings()]
    monkeypatch.setattr(answering, "execute_select", execute)
    monkeypatch.setattr(runtime, "sqlalchemy_database_url", lambda: "postgresql+psycopg://test@localhost/test")
    monkeypatch.setattr(runtime, "RAW_DATA_TABLE", "readings")
    monkeypatch.setattr(runtime, "RAG_LITERATURE_TABLE", "rag_literature_chunks")
    monkeypatch.setattr(runtime, "OLLAMA_CHAT_MODEL", "qwen2.5:latest")
    monkeypatch.setattr(runtime, "OLLAMA_EMBED_MODEL", "nomic-embed-text")
    monkeypatch.setenv("RAG_TOKEN", "test-token")

    embeddings = MagicMock(side_effect=lambda **kw: SimpleNamespace(
        embeddings=[[0.1] * 768 for _ in (kw["input"] if isinstance(kw["input"], list) else [kw["input"]])]))
    monkeypatch.setattr(Client, "embed", embeddings)
    node = TextNode(text="Reference material.", metadata={
        "source": "reference.pdf", "page": 3, "category": "standard",
        "organization": "Test Standards", "file_path": "C:\\private\\reference.pdf",
        "file_size": 1234, "creation_date": "private-created",
        "last_modified_date": "private-modified", "document_id": "private-id",
    })
    def vector_query(store, query, **kwargs):
        from app.providers.ollama.config import RAG_K
        assert query.query_embedding == [0.1] * 768
        assert query.similarity_top_k == RAG_K
        assert query.filters is None
        assert store.table_name == "rag_literature_chunks"
        return VectorStoreQueryResult(nodes=[node], similarities=[1.0], ids=[node.node_id])
    monkeypatch.setattr(diag.PGVectorStore, "query", vector_query)
    chat = MagicMock()
    monkeypatch.setattr(Client, "chat", chat)
    app = FastAPI()
    app.add_middleware(diag.RagTimingMiddleware)
    app.include_router(router, prefix="/rag")
    try:
        yield TestClient(app), chat, embeddings
    finally:
        for factory in cached:
            factory.cache_clear()
        Settings._llm, Settings._embed_model = saved_llm, saved_embedding
        database.dispose()


def scripted_responses(contents):
    return [{"message": {"role": "assistant", "content": content},
             "prompt_eval_count": 120, "eval_count": 24,
             "prompt_eval_duration": 125_000_000, "eval_duration": 900_000_000}
            for content in contents]


@pytest.mark.parametrize("question,route,contents,embeddings_expected", [
    ("What was the average temperature yesterday?", "sql", ["SELECT AVG(temp_c) FROM readings"], 0),
    ("What temperature is comfortable?", "literature", ["Literature answer."], 1),
    ("Was my temperature safe yesterday?", "sql+literature", ["SELECT AVG(temp_c) FROM readings", "Combined answer."], 1),
])
def test_explicit_pipeline_calls_and_metadata(offline_rag, caplog, question, route, contents, embeddings_expected):
    caplog.set_level(logging.INFO, logger=diag.logger.name)
    client, chat, embeddings = offline_rag
    for _ in range(2):
        caplog.clear()
        chat.reset_mock()
        embeddings.reset_mock()
        chat.side_effect = scripted_responses(contents)
        response = client.post("/rag/query?framework=llamaindex",
                               json={"question": question},
                               headers={"X-RAG-Token": "test-token"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"framework", "question", "response", "answer", "metadata"}
        md = body["metadata"]
        assert md["route"] == route
        assert md["selected_knowledge_paths"] == route.split("+")
        assert md["llm_call_count"] == chat.call_count == len(contents)
        assert md["embedding_call_count"] == embeddings.call_count == embeddings_expected
        assert all(call.kwargs["options"]["num_ctx"] == 8192 for call in chat.call_args_list)
        assert all(call.kwargs["tools"] is None for call in chat.call_args_list)
        assert all(value >= 0 for value in md["timings_ms"].values())
        assert md["timings_ms"]["total_request"] >= md["timings_ms"]["llm"]
        if "sql" in route:
            assert md["generated_sql"] == contents[0]
            assert list(md["sql_result"][0].values()) == [23.5]
        else:
            assert md["generated_sql"] is None
        if "literature" in route:
            assert body["answer"] == contents[-1]
            assert md["literature_sources"] == [{
                "citation": 1, "source": "reference.pdf", "page": 3,
                "category": "standard", "organization": "Test Standards", "score": 1.0,
            }]
            for internal in ("node_id", "file_path", "file_size", "creation_date",
                             "last_modified_date", "document_id", "metadata", "text"):
                assert internal not in md["literature_sources"][0]
        else:
            assert "23.5" in body["answer"]
            assert md["literature_sources"] == []
        messages = [r.getMessage() for r in caplog.records if r.name == diag.logger.name]
        assert sum("stage=llm_call " in m for m in messages) == len(contents)
        for message in messages:
            if "stage=llm_call " in message:
                assert "prompt_tokens=120 output_tokens=24 prompt_eval_ms=125.0 generation_ms=900.0" in message
        assert "stage=request_total" in messages[-1]
        assert not any("stage=route_selection " in m or "stage=retrieval_planning " in m for m in messages)


@pytest.mark.parametrize("value", ["None", " none \n", "LOOKUP: None", " lookup : nOnE \n"])
def test_no_lookup_sentinel(value):
    from llama_index.core.schema import QueryBundle
    from app.frameworks.llamaindex.experimental import no_literature_lookup

    assert no_literature_lookup(QueryBundle(value))


@pytest.mark.parametrize("value", [
    'LOOKUP: "comfortable temperature thresholds"', "temperature thresholds",
    "LOOKUP: None of these temperatures are safe", "LOOKUP:", "", "Other: None",
])
def test_real_or_unrecognized_lookup_is_not_skipped(value):
    from llama_index.core.schema import QueryBundle
    from app.frameworks.llamaindex.experimental import no_literature_lookup

    bundle = QueryBundle(value)
    assert not no_literature_lookup(bundle)
    assert bundle.query_str == value


@pytest.mark.parametrize("raw,expected", [
    (None, {}), ({}, {}),
    ({"prompt_eval_count": 0, "eval_count": None}, {"prompt_tokens": 0}),
    ({"eval_count": 5, "eval_duration": 1_234_567}, {"output_tokens": 5, "generation_ms": 1.235}),
    ({"prompt_eval_count": "bad", "eval_count": True, "eval_duration": -1}, {}),
])
def test_optional_ollama_metrics(raw, expected):
    assert diag.ollama_metrics(SimpleNamespace(raw=raw)) == expected


def test_request_failure_logs_total_and_cleans_context(offline_rag, caplog):
    caplog.set_level(logging.INFO, logger=diag.logger.name)
    client, chat, _ = offline_rag
    chat.side_effect = RuntimeError("simulated Ollama failure")
    with pytest.raises(RuntimeError, match="simulated Ollama failure"):
        client.get("/rag/query", params={"framework": "llamaindex", "question": "test"},
                   headers={"X-RAG-Token": "test-token"})
    assert "stage=request_total" in caplog.text
    assert "outcome=error" in caplog.text
    assert "llm_calls=1" in caplog.text
    assert diag.current_request.get() is None


def test_invalid_generated_sql_never_executes(offline_rag, monkeypatch):
    from app.frameworks.llamaindex import answering
    client, chat, embeddings = offline_rag
    execute = MagicMock(side_effect=AssertionError("Unsafe SQL must not execute"))
    monkeypatch.setattr(answering, "execute_select", execute)
    chat.side_effect = scripted_responses(["DELETE FROM readings"])
    response = client.post("/rag/query", json={"question": "Show latest readings"},
                           headers={"X-RAG-Token": "test-token"})
    assert response.status_code == 422
    execute.assert_not_called()
    embeddings.assert_not_called()
    assert chat.call_count == 1


def test_postgres_execution_failure_returns_controlled_answer(offline_rag, monkeypatch, caplog):
    import psycopg
    from app.frameworks.llamaindex import answering

    client, chat, embeddings = offline_rag
    generated = (
        "SELECT AVG(temp_c) FROM readings WHERE "
        "ts AT TIME ZONE 'America/Los_Angeles'::DATE >= '2026-01-01'::date"
    )
    database_detail = 'invalid input syntax for type date: "private-database-detail"'
    monkeypatch.setattr(
        answering, "execute_select",
        MagicMock(side_effect=psycopg.errors.InvalidDatetimeFormat(database_detail)),
    )
    chat.side_effect = scripted_responses([generated])
    caplog.set_level(logging.ERROR, logger=answering.logger.name)

    response = client.post(
        "/rag/query?framework=llamaindex",
        json={"question": "How did humidity from January through March 2026 compare with literature recommendations?"},
        headers={"X-RAG-Token": "test-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "could not be translated into a valid database query" in body["answer"]
    assert database_detail not in response.text
    assert body["metadata"]["route"] == "sql+literature"
    assert body["metadata"]["selected_knowledge_paths"] == ["sql", "literature"]
    assert body["metadata"]["sql_status"] == "error"
    assert body["metadata"]["generated_sql"] == generated
    assert body["metadata"]["sql_result"] is None
    assert body["metadata"]["llm_call_count"] == 1
    assert body["metadata"]["embedding_call_count"] == 0
    assert generated in caplog.text
    assert database_detail in caplog.text
    assert chat.call_count == 1
    embeddings.assert_not_called()


def test_empty_literature_still_uses_one_grounded_synthesis(offline_rag, monkeypatch):
    from llama_index.core.vector_stores.types import VectorStoreQueryResult
    client, chat, embeddings = offline_rag
    monkeypatch.setattr(diag.PGVectorStore, "query", lambda *a, **k: VectorStoreQueryResult(nodes=[]))
    chat.side_effect = scripted_responses(["No relevant literature was found."])
    response = client.get("/rag/query", params={"question": "What humidity is recommended?"},
                          headers={"X-RAG-Token": "test-token"})
    assert response.status_code == 200
    assert response.json()["metadata"]["literature_sources"] == []
    assert chat.call_count == embeddings.call_count == 1


def test_public_literature_source_omits_unavailable_optional_metadata():
    from app.frameworks.llamaindex.answering import (
        _public_literature_source, _synthesis_literature_passage,
    )

    passage = {
        "citation": 2, "node_id": "internal-node", "score": 0.75,
        "text": "Public evidence.",
        "metadata": {"source": "paper.pdf", "page_label": "24", "file_path": "private"},
    }
    expected = {"citation": 2, "source": "paper.pdf", "page": "24", "score": 0.75}
    assert _public_literature_source(passage) == expected
    assert _synthesis_literature_passage(passage) == {
        **expected, "text": "Public evidence.",
    }


def test_query_authentication_is_unchanged(offline_rag):
    client, chat, embeddings = offline_rag
    response = client.post("/rag/query", json={"question": "Latest readings"})
    assert response.status_code == 401
    chat.assert_not_called()
    embeddings.assert_not_called()


def test_concurrent_requests_keep_separate_counts_and_ids(caplog):
    import asyncio

    caplog.set_level(logging.INFO, logger=diag.logger.name)

    async def app(scope, receive, send):
        state = diag.current_request.get()
        state.enabled = True
        for _ in range(scope["calls"]):
            with diag.timed("llm_call", call="llm"):
                await asyncio.sleep(0)
        await send({"type": "http.response.start", "status": 200})
        await send({"type": "http.response.body", "body": b"unchanged"})

    middleware = diag.RagTimingMiddleware(app)

    async def run():
        async def send(message):
            pass

        await asyncio.gather(*[
            middleware({"type": "http", "path": "/rag/query", "calls": count}, None, send)
            for count in (1, 3)
        ])

    asyncio.run(run())
    totals = [r.getMessage() for r in caplog.records if "stage=request_total" in r.getMessage()]
    assert len(totals) == 2
    assert len({m.split("request_id=")[1].split()[0] for m in totals}) == 2
    assert any("llm_calls=1 " in m for m in totals)
    assert any("llm_calls=3 " in m for m in totals)
    assert diag.current_request.get() is None


def test_unscoped_calls_do_not_log(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger=diag.logger.name)
    original = MagicMock(return_value=[0.1])
    monkeypatch.setattr(diag.OllamaEmbedding, "get_general_text_embedding", original)
    embedding = diag.TimedOllamaEmbedding(model_name="test", base_url="http://localhost:11434")
    assert embedding.get_general_text_embedding("private text") == [0.1]
    original.assert_called_once_with("private text")
    assert not caplog.records
