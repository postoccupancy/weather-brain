from __future__ import annotations

from app.database import sqlalchemy_database_url

import hashlib

from langchain_core.documents import Document
from app.frameworks.langchain.postgres import ExistingPGVector

from app.frameworks.langchain.runtime import get_embeddings
from app.providers.ollama.config import (
    RAG_LITERATURE_TABLE,
)
from app.retrieval.vector.ingest_docs import build_raw_docs, clean_text


def _chunk_text(text: str, *, chunk_size: int = 1200, overlap: int = 150) -> list[str]:
    cleaned = clean_text(text)
    if not cleaned:
        return []

    chunks: list[str] = []
    start = 0
    step = max(chunk_size - overlap, 1)
    while start < len(cleaned):
        chunk = cleaned[start : start + chunk_size].strip()
        if chunk:
            chunks.append(chunk)
        start += step
    return chunks


def _literature_vectorstore() -> ExistingPGVector:
    verified = sqlalchemy_database_url()
    return ExistingPGVector(
        embeddings=get_embeddings(),
        collection_name=RAG_LITERATURE_TABLE,
        connection=verified,
        create_extension=False,
    )


def ingest_literature_with_langchain() -> dict[str, object]:
    raw_docs = build_raw_docs()
    chunks: list[Document] = []
    ids: list[str] = []

    for raw_doc in raw_docs:
        source = raw_doc.metadata.get("source", "unknown")
        for index, chunk in enumerate(_chunk_text(raw_doc.text)):
            content_hash = hashlib.sha1(chunk.encode("utf-8")).hexdigest()[:12]
            metadata = dict(raw_doc.metadata)
            metadata["chunk_index"] = index
            chunks.append(Document(page_content=chunk, metadata=metadata))
            ids.append(f"{source}:{index}:{content_hash}")

    if not chunks:
        return {"chunks_indexed": 0, "note": "No literature documents were loaded."}

    vectorstore = _literature_vectorstore()
    vectorstore.add_documents(chunks, ids=ids)
    return {"chunks_indexed": len(chunks), "sources_loaded": len(raw_docs)}
