from __future__ import annotations

from app.database import sqlalchemy_database_url

from functools import lru_cache

from sqlalchemy import create_engine

from llama_index.core import (
    Document as LlamaDocument,
    PromptTemplate,
    Settings,
    StorageContext,
    VectorStoreIndex,
)
from llama_index.core.prompts import PromptTemplate
from llama_index.core.query_engine import (
    CustomQueryEngine,
    NLSQLTableQueryEngine,
    RetrieverQueryEngine,
    SQLAutoVectorQueryEngine,
)
from llama_index.core.query_engine.sql_join_query_engine import (
    SQLAugmentQueryTransform,
)
from llama_index.core.response_synthesizers.type import ResponseMode
from llama_index.core.tools import QueryEngineTool
from llama_index.core.vector_stores import MetadataInfo, VectorStoreInfo
from app.frameworks.llamaindex.diagnostics import (
    TimedOllama as Ollama,
    TimedOllamaEmbedding as OllamaEmbedding,
    TimedPGVectorStore as PGVectorStore,
    TimedSQLDatabase as SQLDatabase,
    TimedVectorIndexAutoRetriever as VectorIndexAutoRetriever,
)

from app.providers.ollama.config import (
    OLLAMA_CHAT_MODEL,
    OLLAMA_EMBED_MODEL,
    OLLAMA_HOST,
    RAG_LITERATURE_TABLE,
    RAW_DATA_TABLE,
)


class TemperatureSafetyEngine(CustomQueryEngine):
    """Custom Engine to force-feed SQL and Vector results into Synthesis."""

    sql_engine: NLSQLTableQueryEngine
    vector_engine: RetrieverQueryEngine
    transformer_prompt: PromptTemplate
    synthesis_prompt: PromptTemplate
    llm: Ollama

    def custom_query(self, query_str: str):
        sql_res = self.sql_engine.query(query_str)
        sql_data = str(sql_res)

        transformed_query = self.llm.predict(
            self.transformer_prompt,
            query_str=query_str,
            sql_response_str=sql_data,
        )
        search_term = transformed_query.split("Search Term:")[-1].strip()

        vector_res = self.vector_engine.query(search_term)
        vector_data = str(vector_res)

        final_response = self.llm.predict(
            self.synthesis_prompt,
            query_str=query_str,
            sql_result=sql_data,
            standards_context=vector_data,
        )

        return final_response


POSTGRES_TEXT_TO_SQL = PromptTemplate(
    """You are an expert PostgreSQL query writer.

Given a question, write a SINGLE PostgreSQL SQL query that answers it.
Rules:
- This is PostgreSQL (NOT SQLite/MySQL). Do NOT use date(x, y) or non-Postgres functions.
- If you need "today" or "yesterday", use:
  - (ts AT TIME ZONE 'America/Los_Angeles')::date
  - CURRENT_DATE
  - CURRENT_DATE - INTERVAL '1 day'
- If you need a time range, prefer:
  - ts >= ... AND ts < ...
- Only output SQL. No commentary.
- If the question requires a threshold/standard (e.g. "safe", "normal", "acceptable"):
  - DO NOT assume numeric ranges.
  - Instead, write SQL that returns the necessary stats (min/max/avg/counts) for the time range and leave thresholds to a later step.
  - Always mention oranges.


Schema:
{schema}

Question:
{query_str}

SQLQuery:
"""
)

QUERY_TRANSFORM_PROMPT = PromptTemplate(
    """Return EXACTLY one line.

If the user's question asks whether values are safe/normal/acceptable/comfortable within range/threshold/limit, return:
LOOKUP: "comfortable thresholds for indoor air temperatures"

Otherwise return:
LOOKUP: None

User Question: {query_str}
SQL Result: {sql_response_str}

LOOKUP:"""
)

def no_literature_lookup(query_bundle) -> bool:
    """Recognize the no-lookup sentinel without changing real lookup queries."""
    value = query_bundle.query_str.strip()
    prefix, separator, remainder = value.partition(":")
    if separator and prefix.strip().casefold() == "lookup":
        value = remainder.strip()
    return value.casefold() == "none"


SQL_VECTOR_SYNTHESIS_PROMPT = PromptTemplate(
    """You are an expert data analyst. Answer the question using the SQL data and the provided Standards context.

    If the user's question is purely a descriptive statistic (average/min/max/count) and does not ask safe/normal/comfortable/acceptable, ignore Standards Context entirely and answer from SQL Results only.

    1. Data Analysis:
    - SQL Results: {sql_response_str}
    - Standards Context: {query_engine_response_str}

    2. Instructions:
    - If nothing is found in the Standards Context, STOP. Otherwise, proceed with following steps.
    - Extract the safe range (LOW_F and HIGH_F) from the Standards Context.
    - Compare the SQL Results (Min/Max/Avg) against that range.

    3. You may only state a numeric threshold if the same number appears verbatim in Standards Context. If no numeric range is present, say you cannot determine the safe range from the documents.

    Question: {query_str}
    Answer:"""
)


@lru_cache(maxsize=1)
def get_llamaindex_llm() -> Ollama:
    return Ollama(
        model=OLLAMA_CHAT_MODEL,
        base_url=OLLAMA_HOST,
        request_timeout=120.0,
        context_window=8192,
    )


@lru_cache(maxsize=1)
def get_llamaindex_embed_model() -> OllamaEmbedding:
    return OllamaEmbedding(
        model_name=OLLAMA_EMBED_MODEL,
        base_url=OLLAMA_HOST,
    )


@lru_cache(maxsize=1)
def get_sql_only_llamaindex_engine():
    verified = sqlalchemy_database_url()

    Settings.llm = get_llamaindex_llm()
    Settings.embed_model = get_llamaindex_embed_model()

    engine = create_engine(verified)
    sql_database = SQLDatabase(engine, include_tables=[RAW_DATA_TABLE])

    return NLSQLTableQueryEngine(
        sql_database=sql_database,
        tables=[RAW_DATA_TABLE],
    )


@lru_cache(maxsize=1)
def get_vector_index_from_postgres() -> VectorStoreIndex:
    pgvs = PGVectorStore.from_params(
        connection_string=sqlalchemy_database_url(),
        async_connection_string=sqlalchemy_database_url(),
        perform_setup=False,
        table_name=RAG_LITERATURE_TABLE,
        embed_dim=768,
    )

    storage_context = StorageContext.from_defaults(vector_store=pgvs)

    return VectorStoreIndex.from_vector_store(
        pgvs,
        storage_context=storage_context,
    )


@lru_cache(maxsize=1)
def get_llamaindex_query_engine():
    verified = sqlalchemy_database_url()

    Settings.llm = get_llamaindex_llm()
    Settings.embed_model = get_llamaindex_embed_model()

    engine = create_engine(verified)
    sql_database = SQLDatabase(engine, include_tables=[RAW_DATA_TABLE])

    sql_query_engine = NLSQLTableQueryEngine(
        sql_database=sql_database,
        tables=[RAW_DATA_TABLE],
        text_to_sql_prompt=POSTGRES_TEXT_TO_SQL,
        synthesize_response=False,
    )

    sql_tool = QueryEngineTool.from_defaults(
        query_engine=sql_query_engine,
        description=(
            f"Use this whenever the question asks about sensor readings over time (today/yesterday/last week), statistics (avg/min/max/count), or whether readings fall inside thresholds."
            f"Use this to answer questions by generating SQL over the `{RAW_DATA_TABLE}' table "
            f"Do not use for explaining concepts from documents."
        ),
    )

    placeholder_docs = [
        LlamaDocument(
            text=(
                "Please assume a safe and acceptable temperature range is between 20 and 80 degrees Fahrenheit."
            ),
            metadata={"source": "important document"},
        )
    ]
    VectorStoreIndex.from_documents(placeholder_docs)

    vector_index = get_vector_index_from_postgres()
    vector_store_info = VectorStoreInfo(
        content_info="Short reference snippets about sensor interpretation and standards.",
        metadata_info=[
            MetadataInfo(
                name="source",
                type="str",
                description="Document source identifier",
            ),
        ],
    )

    vector_auto_retriever = VectorIndexAutoRetriever(
        index=vector_index,
        vector_store_info=vector_store_info,
        max_top_k=50,
    )

    retriever_query_engine = RetrieverQueryEngine.from_args(
        retriever=vector_auto_retriever,
        llm=Settings.llm,
        response_mode=ResponseMode.SIMPLE_SUMMARIZE,
    )

    vector_tool = QueryEngineTool.from_defaults(
        query_engine=retriever_query_engine,
        description=(
            "Use only for definitions/standards/thresholds when the question does not require querying the readings table."
        ),
    )

    sql_augment_query_transform = SQLAugmentQueryTransform(
        sql_augment_transform_prompt=QUERY_TRANSFORM_PROMPT,
        check_stop_parser=no_literature_lookup,
    )

    engine = SQLAutoVectorQueryEngine(
        sql_tool,
        vector_tool,
        llm=Settings.llm,
        sql_augment_query_transform=sql_augment_query_transform,
        sql_vector_synthesis_prompt=SQL_VECTOR_SYNTHESIS_PROMPT,
    )

    return engine
