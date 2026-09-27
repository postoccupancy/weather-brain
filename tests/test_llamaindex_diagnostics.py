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
    from app.frameworks.llamaindex import runtime

    saved_llm, saved_embedding = Settings._llm, Settings._embed_model
    cached = [runtime.get_llamaindex_llm, runtime.get_llamaindex_embed_model,
              runtime.get_llamaindex_query_engine, runtime.get_vector_index_from_postgres]
    for factory in cached:
        factory.cache_clear()
    database = create_engine("sqlite://", poolclass=StaticPool,
                             connect_args={"check_same_thread": False})
    with database.begin() as conn:
        conn.execute(text("CREATE TABLE readings (temp_c FLOAT)"))
        conn.execute(text("INSERT INTO readings VALUES (23.5)"))
    monkeypatch.setattr(runtime, "create_engine", lambda *a, **k: database)
    monkeypatch.setattr(runtime, "sqlalchemy_database_url", lambda: "postgresql+psycopg://test@localhost/test")
    monkeypatch.setattr(runtime, "RAW_DATA_TABLE", "readings")
    monkeypatch.setattr(runtime, "RAG_LITERATURE_TABLE", "rag_literature_chunks")
    monkeypatch.setattr(runtime, "OLLAMA_CHAT_MODEL", "qwen2.5:latest")
    monkeypatch.setattr(runtime, "OLLAMA_EMBED_MODEL", "nomic-embed-text")
    monkeypatch.setenv("RAG_TOKEN", "test-token")

    embeddings = MagicMock(side_effect=lambda **kw: SimpleNamespace(
        embeddings=[[0.1] * 768 for _ in (kw["input"] if isinstance(kw["input"], list) else [kw["input"]])]))
    monkeypatch.setattr(Client, "embed", embeddings)
    node = TextNode(text="Reference material.")
    monkeypatch.setattr(diag.PGVectorStore, "query", lambda *a, **k: VectorStoreQueryResult(
        nodes=[node], similarities=[1.0], ids=[node.node_id]))
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


def scripted_responses(transform='LOOKUP: "comfortable temperature thresholds"'):
    selection = {"message": {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "SingleSelection", "arguments": {
            "index": 1, "reason": "Needs sensor statistics"}}}]}}
    responses = [selection]
    for content in ["SELECT AVG(temp_c) FROM readings", transform,
                    '{"query": "temperature reference", "filters": [], "top_k": 1}',
                    "Literature summary.", "Final answer."]:
        responses.append({"message": {"role": "assistant", "content": content}})
    for response in responses:
        response.update(prompt_eval_count=120, eval_count=24,
                        prompt_eval_duration=125_000_000, eval_duration=900_000_000)
    return responses


def test_real_pipeline_counts_six_calls_and_cold_embedding(offline_rag, caplog):
    caplog.set_level(logging.INFO, logger=diag.logger.name)
    client, chat, embeddings = offline_rag
    for cold in (True, False):
        caplog.clear()
        chat.reset_mock()
        embeddings.reset_mock()
        chat.side_effect = scripted_responses()
        response = client.post("/rag/query?framework=llamaindex",
                               json={"question": "What is the average temperature?"},
                               headers={"X-RAG-Token": "test-token"})
        assert response.status_code == 200, response.text
        assert response.json()["answer"] == "Final answer."
        assert set(response.json()) == {"framework", "question", "response", "answer", "metadata"}
        assert chat.call_count == 6
        assert all(call.kwargs["options"]["num_ctx"] == 8192 for call in chat.call_args_list)
        assert embeddings.call_count == (2 if cold else 1)
        messages = [r.getMessage() for r in caplog.records if r.name == diag.logger.name]
        assert sum("stage=llm_call " in m for m in messages) == 6
        for message in messages:
            if "stage=llm_call " in message:
                assert "prompt_tokens=120 output_tokens=24 prompt_eval_ms=125.0 generation_ms=900.0" in message
        assert "llm_calls=6" in messages[-1]
        assert f"embedding_calls={2 if cold else 1}" in messages[-1]
        assert "stage=request_total" in messages[-1]
        for stage in ("route_selection", "sql_generation", "query_transformation",
                      "retrieval_planning", "postgres_query_and_fetch",
                      "vector_literature_retrieval", "vector_store_query_and_fetch", "literature_answer_synthesis",
                      "final_answer_synthesis", "engine_initialization"):
            assert any(f"stage={stage} " in m for m in messages), stage
        assert not any("Final answer." in m or "SELECT" in m for m in messages)


@pytest.mark.parametrize("value", ["None", " none \n", "LOOKUP: None", " lookup : nOnE \n"])
def test_no_lookup_sentinel(value):
    from llama_index.core.schema import QueryBundle
    from app.frameworks.llamaindex.runtime import no_literature_lookup

    assert no_literature_lookup(QueryBundle(value))


@pytest.mark.parametrize("value", [
    'LOOKUP: "comfortable temperature thresholds"', "temperature thresholds",
    "LOOKUP: None of these temperatures are safe", "LOOKUP:", "", "Other: None",
])
def test_real_or_unrecognized_lookup_is_not_skipped(value):
    from llama_index.core.schema import QueryBundle
    from app.frameworks.llamaindex.runtime import no_literature_lookup

    bundle = QueryBundle(value)
    assert not no_literature_lookup(bundle)
    assert bundle.query_str == value


def test_no_lookup_skips_literature_branch(offline_rag, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger=diag.logger.name)
    client, chat, embeddings = offline_rag
    vector_query = MagicMock(side_effect=AssertionError("Literature branch must be skipped"))
    monkeypatch.setattr(diag.PGVectorStore, "query", vector_query)
    for cold in (True, False):
        caplog.clear()
        chat.reset_mock()
        embeddings.reset_mock()
        chat.side_effect = scripted_responses("LOOKUP: None")[:3]
        response = client.post("/rag/query?framework=llamaindex",
                               json={"question": "What is the average temperature?"},
                               headers={"X-RAG-Token": "test-token"})
        assert response.status_code == 200
        assert "23.5" in response.json()["answer"]
        assert chat.call_count == 3
        assert embeddings.call_count == int(cold)
        vector_query.assert_not_called()
        assert "llm_calls=3" in caplog.text
        for stage in ("retrieval_planning", "vector_literature_retrieval",
                      "literature_answer_synthesis", "final_answer_synthesis"):
            assert f"stage={stage} " not in caplog.text


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
