from __future__ import annotations

from app.database import sqlalchemy_database_url

from llama_index.core import StorageContext, VectorStoreIndex
from llama_index.core.schema import TextNode
from llama_index.vector_stores.postgres import PGVectorStore

from app.frameworks.llamaindex.runtime import get_llamaindex_embed_model
from app.providers.ollama.config import (
    RAG_SNAPSHOT_TABLE,
    SNAPSHOT_DATA_TABLE,
)
from app.retrieval.vector.indexing import (
    archive_documents_in_database,
    stable_snapshot_id,
)
from app.retrieval.vector.snapshots import build_snapshot_records


def _snapshot_vector_store() -> PGVectorStore:
    return PGVectorStore.from_params(
        connection_string=sqlalchemy_database_url(),
        async_connection_string=sqlalchemy_database_url(),
        perform_setup=False,
        table_name=RAG_SNAPSHOT_TABLE,
        embed_dim=768,
    )


def index_snapshots_with_llamaindex(
    *,
    db_url: str,
    archive: str = SNAPSHOT_DATA_TABLE,
    lookback_hours: int | None = None,
) -> dict[str, object]:
    records = build_snapshot_records(lookback_hours=lookback_hours)
    if not records:
        note = (
            "No data to index in the last hour."
            if lookback_hours
            else "No data to index from all time."
        )
        return {"snapshots_indexed": 0, "note": note}

    nodes = [
        TextNode(
            text=record.text,
            metadata=dict(record.metadata),
            id_=stable_snapshot_id(record.metadata),
        )
        for record in records
    ]

    vector_store = _snapshot_vector_store()
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    VectorStoreIndex(
        nodes,
        storage_context=storage_context,
        embed_model=get_llamaindex_embed_model(),
    )

    archive_documents_in_database(
        db_url=db_url,
        snapshot_rows=[(record.text, record.metadata) for record in records],
        archive=archive,
    )
    return {"snapshots_indexed": len(records)}
