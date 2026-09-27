from __future__ import annotations

from app.database import sqlalchemy_database_url

from functools import lru_cache

from langchain_core.vectorstores import VectorStoreRetriever
from langchain_ollama import ChatOllama, OllamaEmbeddings
from app.frameworks.langchain.postgres import ExistingPGVector

from app.providers.ollama.config import (
    OLLAMA_CHAT_MODEL,
    OLLAMA_EMBED_MODEL,
    OLLAMA_HOST,
    RAG_K,
    RAG_SNAPSHOT_TABLE,
)


@lru_cache(maxsize=1)
def get_embeddings() -> OllamaEmbeddings:
    return OllamaEmbeddings(
        model=OLLAMA_EMBED_MODEL,
        base_url=OLLAMA_HOST,
    )


@lru_cache(maxsize=1)
def get_llm() -> ChatOllama:
    return ChatOllama(
        model=OLLAMA_CHAT_MODEL,
        temperature=0.9,
        base_url=OLLAMA_HOST,
    )


@lru_cache(maxsize=1)
def get_vectorstore() -> ExistingPGVector:
    verified = sqlalchemy_database_url()

    return ExistingPGVector(
        embeddings=get_embeddings(),
        collection_name=RAG_SNAPSHOT_TABLE,
        connection=verified,
        create_extension=False,
    )


@lru_cache(maxsize=1)
def get_retriever() -> VectorStoreRetriever:
    vs = get_vectorstore()
    return vs.as_retriever(search_kwargs={"k": RAG_K})
