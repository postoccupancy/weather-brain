from __future__ import annotations

from typing import Any

from app.frameworks.llamaindex.runtime import get_llamaindex_query_engine
from app.frameworks.llamaindex.diagnostics import current_request, timed


def answer_with_llamaindex(question: str) -> dict[str, Any]:
    state = current_request.get()
    if state is not None:
        state.enabled = True
    with timed("engine_initialization"):
        query_engine = get_llamaindex_query_engine()
    with timed("query_execution"):
        response = query_engine.query(question)
    return {
        "question": question,
        "response": str(response),
        "answer": getattr(response, "response", str(response)),
        "metadata": getattr(response, "metadata", None),
    }
