"""Shared factories; no query orchestration or indexing at initialization."""
from functools import lru_cache

from llama_index.core import PromptTemplate, StorageContext, VectorStoreIndex
from sqlalchemy import create_engine

from app.database import sqlalchemy_database_url
from app.frameworks.llamaindex.diagnostics import (
    TimedOllama as Ollama,
    TimedOllamaEmbedding as OllamaEmbedding,
    TimedPGVectorStore as PGVectorStore,
    TimedSQLDatabase as SQLDatabase,
)
from app.providers.ollama.config import (
    OLLAMA_CHAT_MODEL, OLLAMA_EMBED_MODEL, OLLAMA_HOST,
    RAG_LITERATURE_TABLE, RAW_DATA_TABLE,
)


POSTGRES_TEXT_TO_SQL = PromptTemplate(
    """Write one read-only PostgreSQL SELECT statement answering the question.
Use only the provided schema. No commentary, code fences, CTEs or multiple statements.
For calendar date/time ranges, interpret dates in America/Los_Angeles. Compare ts
directly against an inclusive start timestamp and an exclusive end timestamp.
Do not cast ts to DATE or use BETWEEN for calendar periods.
For example, January through March 2026 uses:
ts >= '2026-01-01'::timestamp AT TIME ZONE 'America/Los_Angeles'
AND ts < '2026-04-01'::timestamp AT TIME ZONE 'America/Los_Angeles'
Apply the same boundary rule to other dates/ranges, including today/yesterday
using the current calendar date in America/Los_Angeles.
For safety/comfort questions return relevant measured statistics; do not invent
thresholds. Reference literature will be consulted separately.
Schema:
{schema}
Question: {query_str}
SQL:"""
)


@lru_cache(maxsize=1)
def get_llamaindex_llm() -> Ollama:
    return Ollama(model=OLLAMA_CHAT_MODEL, base_url=OLLAMA_HOST,
                  request_timeout=120.0, context_window=8192)


@lru_cache(maxsize=1)
def get_llamaindex_embed_model() -> OllamaEmbedding:
    return OllamaEmbedding(model_name=OLLAMA_EMBED_MODEL, base_url=OLLAMA_HOST)


@lru_cache(maxsize=1)
def get_sql_database() -> SQLDatabase:
    return SQLDatabase(create_engine(sqlalchemy_database_url()), include_tables=[RAW_DATA_TABLE])


@lru_cache(maxsize=1)
def get_literature_store() -> PGVectorStore:
    return PGVectorStore.from_params(
        connection_string=sqlalchemy_database_url(),
        async_connection_string=sqlalchemy_database_url(),
        perform_setup=False, table_name=RAG_LITERATURE_TABLE, embed_dim=768,
    )


@lru_cache(maxsize=1)
def get_vector_index_from_postgres() -> VectorStoreIndex:
    """Retained for experiments; unused by normal answering."""
    store = get_literature_store()
    return VectorStoreIndex.from_vector_store(
        store, storage_context=StorageContext.from_defaults(vector_store=store),
        embed_model=get_llamaindex_embed_model(),
    )
