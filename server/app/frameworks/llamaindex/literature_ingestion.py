from __future__ import annotations

from app.database import sqlalchemy_database_url

from llama_index.core import StorageContext, VectorStoreIndex
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import TextNode
from llama_index.vector_stores.postgres import PGVectorStore

from app.frameworks.llamaindex.runtime import get_llamaindex_embed_model
from app.providers.ollama.config import (
    RAG_LITERATURE_TABLE,
)
from app.retrieval.vector.ingest_docs import build_raw_docs, clean_text


def ingest_literature_with_llamaindex() -> dict[str, object]:
    raw_docs = build_raw_docs()
    splitter = SentenceSplitter(chunk_size=256, chunk_overlap=50)
    nodes = splitter.get_nodes_from_documents(raw_docs)

    for node in nodes:
        if isinstance(node, TextNode):
            node.text = clean_text(node.text)

    if not nodes:
        return {"chunks_indexed": 0, "note": "No literature documents were loaded."}

    vector_store = PGVectorStore.from_params(
        connection_string=sqlalchemy_database_url(),
        async_connection_string=sqlalchemy_database_url(),
        perform_setup=False,
        table_name=RAG_LITERATURE_TABLE,
        embed_dim=768,
    )
    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    VectorStoreIndex(
        nodes,
        storage_context=storage_context,
        embed_model=get_llamaindex_embed_model(),
    )
    return {"chunks_indexed": len(nodes), "sources_loaded": len(raw_docs)}
