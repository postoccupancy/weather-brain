from __future__ import annotations

from collections import Counter
import math
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from llama_index.core import SimpleDirectoryReader
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import MetadataMode, TextNode
from llama_index.core.vector_stores.utils import node_to_metadata_dict

from app.database import DATABASE_URL
from app.frameworks.llamaindex.runtime import get_llamaindex_embed_model
from app.providers.ollama.config import RAG_LITERATURE_TABLE
from app.retrieval.vector.ingest_docs import (
    PDF_DIR, clean_text, load_web_docs, normalize_metadata,
)


def load_literature_documents(pdf_paths: list[Path] | None = None):
    """Explicit paths ingest only those PDFs; default retains PDF + web ingestion."""
    paths = sorted(Path(p).resolve() for p in (
        pdf_paths if pdf_paths is not None else PDF_DIR.rglob("*.pdf")
    ))
    if not paths or len({p.name for p in paths}) != len(paths):
        raise ValueError("Supply PDFs with distinct filenames (the existing source identity)")
    if any(not p.is_file() or p.suffix.lower() != ".pdf" for p in paths):
        raise ValueError("Every requested PDF must exist")
    # Never replace a source after a reader silently skipped a failed file.
    documents = SimpleDirectoryReader(
        input_files=[str(p) for p in paths], raise_on_error=True,
    ).load_data()
    loaded = {d.metadata.get("file_name") for d in documents}
    if loaded != {p.name for p in paths}:
        raise ValueError("Not all requested PDFs produced documents")
    if pdf_paths is None:
        documents.extend(load_web_docs())
    return [normalize_metadata(d) for d in documents]


def prepare_literature(documents):
    """Finish all parsing, cleaning, embedding and serialization before any DELETE."""
    sources = {d.metadata.get("source") for d in documents}
    if not sources or any(not isinstance(s, str) or s == "unknown" or not s for s in sources):
        raise ValueError("Every document must have a stable source")
    splitter = SentenceSplitter(chunk_size=256, chunk_overlap=50)
    nodes = splitter.get_nodes_from_documents(documents)
    for node in nodes:
        if isinstance(node, TextNode):
            node.text = clean_text(node.text)
    if {n.metadata.get("source") for n in nodes if n.get_content().strip()} != sources:
        raise ValueError("Every source must produce nonempty chunks before replacement")
    embeddings = get_llamaindex_embed_model().get_text_embedding_batch(
        [n.get_content(metadata_mode=MetadataMode.EMBED) for n in nodes],
    )
    if len(embeddings) != len(nodes):
        raise ValueError("Embedding count does not match chunk count")
    rows = []
    for node, embedding in zip(nodes, embeddings):
        if len(embedding) != 768 or not all(math.isfinite(v) for v in embedding):
            raise ValueError("Expected finite 768-dimensional embeddings")
        node.embedding = embedding
        metadata = node_to_metadata_dict(node, remove_text=True)
        rows.append((node.node_id, node.get_content(metadata_mode=MetadataMode.NONE),
                     Jsonb(metadata), str(list(embedding))))
    return rows, sorted(sources)


def _insert_rows(cursor, query, rows):
    cursor.executemany(query, rows)


def replace_literature_sources(rows, sources) -> None:
    if not rows or not sources or not RAG_LITERATURE_TABLE:
        raise ValueError("Refusing an empty or unconfigured replacement")
    table = sql.Identifier("public", "data_" + RAG_LITERATURE_TABLE.lower())
    insert = sql.SQL(
        "INSERT INTO {} (node_id, text, metadata_, embedding) "
        "VALUES (%s, %s, %s, %s::extensions.vector)"
    ).format(table)
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '30s'")
            # Serialize replacements (including concurrent first ingestion); readers continue.
            cursor.execute(sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(table))
            cursor.execute(sql.SQL("DELETE FROM {} WHERE metadata_->>'source' = ANY(%s)").format(table), (sources,))
            _insert_rows(cursor, insert, rows)
    # The connection context commits only after every insert succeeds; otherwise rolls back.


def ingest_literature_with_llamaindex(*, pdf_paths: list[Path] | None = None) -> dict[str, object]:
    documents = load_literature_documents(pdf_paths)
    rows, sources = prepare_literature(documents)
    replace_literature_sources(rows, sources)
    counts = Counter(row[2].obj["source"] for row in rows)
    return {"chunks_indexed": len(rows), "sources_loaded": len(documents),
            "source_chunk_counts": dict(sorted(counts.items()))}
