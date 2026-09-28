from unittest.mock import MagicMock

import pytest
from llama_index.core import Document

from app.frameworks.llamaindex import literature_ingestion as ingestion


def documents():
    return [Document(text=("Indoor humidity varies with ventilation. " * 100),
                     metadata={"source": "test.pdf", "file_name": "test.pdf", "page_label": "14"})]


def test_preparation_keeps_all_overlapping_chunks(monkeypatch):
    docs = documents()
    splitter = ingestion.SentenceSplitter(chunk_size=256, chunk_overlap=50)
    expected = splitter.get_nodes_from_documents(docs)
    embed = MagicMock()
    embed.get_text_embedding_batch.side_effect = lambda texts: [[0.1] * 768 for _ in texts]
    monkeypatch.setattr(ingestion, "get_llamaindex_embed_model", lambda: embed)
    rows, sources = ingestion.prepare_literature(docs)
    assert sources == ["test.pdf"]
    assert len(rows) == len(expected) > 1
    assert [r[1] for r in rows] == [ingestion.clean_text(n.text) for n in expected]
    assert len({r[0] for r in rows}) == len(rows)
    assert any(a.end_char_idx > b.start_char_idx for a, b in zip(expected, expected[1:]))


@pytest.mark.parametrize("failure", [RuntimeError("Ollama failed"), [[1.0]]])
def test_embedding_failure_never_replaces(monkeypatch, failure):
    monkeypatch.setattr(ingestion, "load_literature_documents", lambda paths: documents())
    embed = MagicMock()
    if isinstance(failure, Exception):
        embed.get_text_embedding_batch.side_effect = failure
    else:
        embed.get_text_embedding_batch.return_value = failure
    monkeypatch.setattr(ingestion, "get_llamaindex_embed_model", lambda: embed)
    replace = MagicMock()
    monkeypatch.setattr(ingestion, "replace_literature_sources", replace)
    with pytest.raises((ValueError, RuntimeError)):
        ingestion.ingest_literature_with_llamaindex()
    replace.assert_not_called()


def test_replacement_locks_and_uses_one_transaction(monkeypatch):
    connect = MagicMock()
    monkeypatch.setattr(ingestion.psycopg, "connect", connect)
    monkeypatch.setattr(ingestion, "RAG_LITERATURE_TABLE", "rag_literature_chunks")
    connection = connect.return_value.__enter__.return_value
    cursor = connection.cursor.return_value.__enter__.return_value
    rows = [("node", "text", {}, "[0.1]")]
    ingestion.replace_literature_sources(rows, ["test.pdf"])
    calls = cursor.execute.call_args_list
    assert "SHARE ROW EXCLUSIVE" in calls[1].args[0].as_string()
    assert calls[2].args[1] == (["test.pdf"],)
    assert '"public"."data_rag_literature_chunks"' in calls[2].args[0].as_string()
    cursor.executemany.assert_called_once()
    connect.return_value.__exit__.assert_called_once_with(None, None, None)


def test_insert_failure_propagates_for_transaction_rollback(monkeypatch):
    connect = MagicMock()
    monkeypatch.setattr(ingestion.psycopg, "connect", connect)
    monkeypatch.setattr(ingestion, "RAG_LITERATURE_TABLE", "rag_literature_chunks")
    connection = connect.return_value.__enter__.return_value
    connection.cursor.return_value.__enter__.return_value.executemany.side_effect = RuntimeError("insert failed")
    with pytest.raises(RuntimeError, match="insert failed"):
        ingestion.replace_literature_sources([("node", "text", {}, "[0.1]")], ["test.pdf"])
    assert connect.return_value.__exit__.call_args.args[0] is RuntimeError


def test_failed_pdf_read_does_not_replace(monkeypatch, tmp_path):
    pdf = tmp_path / "bad.pdf"
    pdf.write_bytes(b"invalid")
    reader = MagicMock()
    reader.return_value.load_data.side_effect = RuntimeError("parse failed")
    monkeypatch.setattr(ingestion, "SimpleDirectoryReader", reader)
    replace = MagicMock()
    monkeypatch.setattr(ingestion, "replace_literature_sources", replace)
    with pytest.raises(RuntimeError, match="parse failed"):
        ingestion.ingest_literature_with_llamaindex(pdf_paths=[pdf])
    assert reader.call_args.kwargs["raise_on_error"] is True
    replace.assert_not_called()
