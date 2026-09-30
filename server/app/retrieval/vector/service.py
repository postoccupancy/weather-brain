from __future__ import annotations

from fastapi import HTTPException

from app.config.rag import (
    DEFAULT_RAG_INDEX_FRAMEWORK,
    DEFAULT_RAG_LITERATURE_FRAMEWORK,
    choose_framework,
)
from app.frameworks.langchain.literature_ingestion import (
    ingest_literature_with_langchain,
)
from app.frameworks.langchain.vector_indexing import index_snapshots_with_langchain
from app.frameworks.llamaindex.literature_ingestion import (
    ingest_literature_with_llamaindex,
)
from app.frameworks.llamaindex.vector_indexing import index_snapshots_with_llamaindex
from app.providers.ollama.config import SNAPSHOT_DATA_TABLE


SUPPORTED_INDEX_FRAMEWORKS = {"langchain", "llamaindex"}
SUPPORTED_LITERATURE_FRAMEWORKS = {"langchain", "llamaindex"}


def index_snapshots(
    *,
    db_url: str,
    framework: str | None = None,
    archive: str = SNAPSHOT_DATA_TABLE,
    lookback_hours: int | None = None,
    source: str = "readings",
) -> dict[str, object]:
    selected = choose_framework(framework, default=DEFAULT_RAG_INDEX_FRAMEWORK)

    if source == "signal_buckets":
        if selected != "langchain":
            raise HTTPException(status_code=400, detail="Electric Sea snapshots use the existing LangChain collection")
        if lookback_hours is None:
            raise HTTPException(status_code=400, detail="Use the bounded Electric Sea snapshot CLI for historical rebuilds")
        from app.retrieval.vector.snapshots import build_snapshot_records
        from app.retrieval.vector.electric_sea_snapshots import store_electric_sea_snapshots
        from app.providers.ollama.config import RAG_SNAPSHOT_TABLE
        records = build_snapshot_records(source=source, lookback_hours=lookback_hours, db_url=db_url)
        count = store_electric_sea_snapshots(records, db_url=db_url, archive=archive, collection=RAG_SNAPSHOT_TABLE)
        return {"ok": True, "source": source, "framework": selected, "snapshots_indexed": count}
    if source != "readings":
        raise HTTPException(status_code=400, detail="Unsupported snapshot source")

    if selected == "langchain":
        result = index_snapshots_with_langchain(
            db_url=db_url,
            archive=archive,
            lookback_hours=lookback_hours,
        )
    elif selected == "llamaindex":
        result = index_snapshots_with_llamaindex(
            db_url=db_url,
            archive=archive,
            lookback_hours=lookback_hours,
        )
    else:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported snapshot indexing framework '{selected}'. "
                f"Supported frameworks: {sorted(SUPPORTED_INDEX_FRAMEWORKS)}"
            ),
        )

    return {"ok": True, "framework": selected, **result}


def ingest_literature(framework: str | None = None) -> dict[str, object]:
    selected = choose_framework(framework, default=DEFAULT_RAG_LITERATURE_FRAMEWORK)

    if selected == "langchain":
        result = ingest_literature_with_langchain()
    elif selected == "llamaindex":
        result = ingest_literature_with_llamaindex()
    else:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported literature ingestion framework '{selected}'. "
                f"Supported frameworks: {sorted(SUPPORTED_LITERATURE_FRAMEWORKS)}"
            ),
        )

    return {"ok": True, "framework": selected, "message": "Ingestion complete.", **result}
