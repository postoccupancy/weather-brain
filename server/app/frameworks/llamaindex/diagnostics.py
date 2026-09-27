"""Request-local timings for the synchronous LlamaIndex RAG path.

All durations are inclusive wall times and may overlap. No payloads are logged.
"""
from contextlib import contextmanager
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
import logging
from time import perf_counter
from uuid import uuid4

from llama_index.core import SQLDatabase
from llama_index.core.retrievers import VectorIndexAutoRetriever
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.llms.ollama import Ollama
from llama_index.vector_stores.postgres import PGVectorStore
from sqlalchemy import event
from sqlalchemy.engine import Engine


logger = logging.getLogger("uvicorn.error.rag_timing")


@dataclass
class RequestTiming:
    request_id: str = field(default_factory=lambda: uuid4().hex[:12])
    enabled: bool = False
    llm_calls: int = 0
    embedding_calls: int = 0


current_request: ContextVar[RequestTiming | None] = ContextVar("rag_timing", default=None)
current_stage: ContextVar[str] = ContextVar("rag_stage", default="unclassified")


def log_duration(state, stage, started, outcome="ok", **fields):
    logger.info(
        "rag_timing request_id=%s stage=%s duration_ms=%.3f outcome=%s%s",
        state.request_id, stage, (perf_counter() - started) * 1000, outcome,
        "".join(f" {key}={value}" for key, value in fields.items()),
    )


@contextmanager
def timed(stage, *, call=None, metrics=None):
    state = current_request.get()
    if state is None or not state.enabled:
        yield
        return
    started = perf_counter()
    purpose = current_stage.get()
    token = current_stage.set(stage)
    fields = {}
    if call is not None:
        attribute = f"{call}_calls"
        setattr(state, attribute, getattr(state, attribute) + 1)
        fields = {"call": getattr(state, attribute), "purpose": purpose}
    outcome = "ok"
    try:
        yield
    except BaseException:
        outcome = "error"
        raise
    finally:
        if metrics:
            fields.update(metrics)
        log_duration(state, stage, started, outcome, **fields)
        current_stage.reset(token)


class RagTimingMiddleware:
    """Measure through the final ASGI send, including the sync worker-thread wait."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != "/rag/query":
            return await self.app(scope, receive, send)
        state = RequestTiming()
        token = current_request.set(state)
        started = perf_counter()
        status = None
        outcome = "ok"

        async def timed_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, timed_send)
        except BaseException:
            outcome = "error"
            raise
        finally:
            if state.enabled:
                log_duration(
                    state, "request_total", started,
                    "error" if status is not None and status >= 400 else outcome,
                    status=status, llm_calls=state.llm_calls,
                    embedding_calls=state.embedding_calls,
                )
            current_request.reset(token)


def prediction_purpose(kwargs):
    if "query_engine_response_str" in kwargs:
        return "final_answer_synthesis"
    if "dialect" in kwargs and "schema" in kwargs:
        return "sql_generation"
    if "sql_response_str" in kwargs:
        return "query_transformation"
    if "schema_str" in kwargs and "info_str" in kwargs:
        return "retrieval_planning"
    if "num_choices" in kwargs:
        return "route_selection"
    if "context_str" in kwargs:
        return "literature_answer_synthesis"
    return "llm_prediction"


def ollama_metrics(response):
    """Read optional server metrics without logging content or estimating counts."""
    raw = getattr(response, "raw", None)
    if not isinstance(raw, Mapping):
        return {}
    fields = {}
    for source, target, divisor in (
        ("prompt_eval_count", "prompt_tokens", 1),
        ("eval_count", "output_tokens", 1),
        ("prompt_eval_duration", "prompt_eval_ms", 1_000_000),
        ("eval_duration", "generation_ms", 1_000_000),
    ):
        value = raw.get(source)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            fields[target] = value if divisor == 1 else round(value / divisor, 3)
    return fields


class TimedOllama(Ollama):
    def predict(self, prompt, **kwargs):
        with timed(prediction_purpose(kwargs)):
            return super().predict(prompt, **kwargs)

    def chat(self, messages, **kwargs):
        # The default Pydantic selector bypasses predict and calls chat with tools.
        tools = kwargs.get("tools") or []
        selection = any(
            tool.get("function", {}).get("name") == "SingleSelection"
            for tool in tools
        )
        if selection:
            with timed("route_selection"):
                return self._timed_chat(messages, **kwargs)
        return self._timed_chat(messages, **kwargs)

    def _timed_chat(self, messages, **kwargs):
        metrics = {}
        with timed("llm_call", call="llm", metrics=metrics):
            response = super().chat(messages, **kwargs)
            metrics.update(ollama_metrics(response))
            return response


class TimedOllamaEmbedding(OllamaEmbedding):
    def get_general_text_embedding(self, text):
        with timed("embedding_call", call="embedding"):
            return super().get_general_text_embedding(text)

    def get_general_text_embeddings(self, texts):
        with timed("embedding_call", call="embedding"):
            return super().get_general_text_embeddings(texts)


class TimedSQLDatabase(SQLDatabase):
    def run_sql(self, command):
        # Includes connection acquisition, execution, fetch and formatting.
        with timed("postgres_query_and_fetch"):
            return super().run_sql(command)


class TimedVectorIndexAutoRetriever(VectorIndexAutoRetriever):
    def retrieve(self, str_or_query_bundle):
        # Includes retrieval planning, query embedding and vector store access.
        with timed("vector_literature_retrieval"):
            return super().retrieve(str_or_query_bundle)


class TimedPGVectorStore(PGVectorStore):
    def query(self, query, **kwargs):
        with timed("vector_store_query_and_fetch"):
            return super().query(query, **kwargs)


@event.listens_for(Engine, "before_cursor_execute")
def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    state = current_request.get()
    if state is not None and state.enabled and conn.dialect.name == "postgresql":
        context._rag_timing = (state, perf_counter(), current_stage.get())


def finish_cursor(context, outcome):
    timing = getattr(context, "_rag_timing", None)
    if timing is not None:
        state, started, purpose = timing
        del context._rag_timing
        log_duration(state, "postgres_cursor_execute", started, outcome, purpose=purpose)


@event.listens_for(Engine, "after_cursor_execute")
def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    finish_cursor(context, "ok")


@event.listens_for(Engine, "handle_error")
def handle_error(exception_context):
    finish_cursor(exception_context.execution_context, "error")
