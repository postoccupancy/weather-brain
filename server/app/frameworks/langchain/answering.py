from __future__ import annotations

from typing import Any
from datetime import datetime
from fastapi import HTTPException

from langchain_core.documents import Document

from app.execution.answering.shared import (
    AnswerContext,
    build_answer_prompt,
    list_snapshots_used,
)
from app.frameworks.langchain.runtime import get_llm, get_retriever
from app.planning.planner import plan_query, resolve_time_range
from app.retrieval.vector.snapshots import SnapshotRecord, fetch_snapshots_between
from app.retrieval.structured.deployment_corrections import snapshot_warnings


AGG_METRICS = {
    "temperature_avg",
    "temperature_min",
    "temperature_max",
    "humidity_avg",
    "coverage_gaps",
    "latest_snapshot",
}


def _snapshot_records_to_documents(records: list[SnapshotRecord]) -> list[Document]:
    return [
        Document(
            page_content=record.text,
            metadata=dict(record.metadata),
        )
        for record in records
    ]


def _documents_to_answer_contexts(documents: list[Document]) -> list[AnswerContext]:
    warnings = snapshot_warnings() if documents else []
    for document in documents:
        md = document.metadata or {}
        node = md.get('node_id') or md.get('device_id')
        start = md.get('window_start')
        if isinstance(start, str):
            start = datetime.fromisoformat(start)
        if any(w['node_id'] == node and (start is None or w['start'] <= start < w['end']) for w in warnings):
            raise HTTPException(status_code=409, detail='Snapshot deployment context is being rebuilt. Inspect the node deployment correction-status endpoint before retrying.')
    return [
        AnswerContext(
            text=document.page_content,
            metadata=dict(document.metadata or {}),
        )
        for document in documents
    ]


def _response_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return str(content)


def answer_with_langchain(question: str) -> dict[str, Any]:
    llm = get_llm()
    plan = plan_query(question)
    metric = plan.get("metric", "none")

    if metric in AGG_METRICS:
        start_utc, end_utc = resolve_time_range(plan)
        records = fetch_snapshots_between(start_utc, end_utc)
        documents = _snapshot_records_to_documents(records)
        contexts = _documents_to_answer_contexts(documents)

        if not documents:
            return {
                "question": question,
                "answer": f"No snapshots found between {start_utc.isoformat()} and {end_utc.isoformat()}.",
                "snapshots": [],
                "retrieved": 0,
                "plan": plan,
                "time_range_utc": {
                    "start": start_utc.isoformat(),
                    "end": end_utc.isoformat(),
                },
            }

        if metric == "latest_snapshot":
            md = documents[-1].metadata
            answer = (
                f"Most recent snapshot in range: "
                f"{md.get('window_start')} - {md.get('window_end')}."
            )
            return {
                "question": question,
                "answer": answer,
                "snapshots": list_snapshots_used([contexts[-1]]),
                "retrieved": len(documents),
                "plan": plan,
                "time_range_utc": {
                    "start": start_utc.isoformat(),
                    "end": end_utc.isoformat(),
                },
            }

        prompt = build_answer_prompt(question, contexts)
        response = llm.invoke(prompt)

        return {
            "question": question,
            "answer": _response_content_to_text(response.content),
            "snapshots": list_snapshots_used(contexts),
            "retrieved": len(documents),
            "plan": plan,
            "time_range_utc": {
                "start": start_utc.isoformat(),
                "end": end_utc.isoformat(),
            },
        }

    retriever = get_retriever()
    documents = retriever.invoke(question)
    contexts = _documents_to_answer_contexts(documents)
    prompt = build_answer_prompt(question, contexts)
    response = llm.invoke(prompt)

    return {
        "question": question,
        "answer": _response_content_to_text(response.content),
        "snapshots": list_snapshots_used(contexts),
        "retrieved": len(documents),
    }
