from __future__ import annotations

import os
from fastapi import HTTPException
from app.retrieval.structured.signal_timeseries import get_signal_timeseries

from app.retrieval.structured.sql_queries import (
    get_postgres,
    get_postgres_aggregated,
    get_postgres_summary,
)


RAW_DATA_TABLE = os.getenv("RAW_DATA_TABLE", "readings")
SNAPSHOT_DATA_TABLE = os.getenv("SNAPSHOT_DATA_TABLE", "snapshots")
ALLOWED_TABLES = {RAW_DATA_TABLE, SNAPSHOT_DATA_TABLE}


def fetch_timeseries(
    *,
    limit: int = 100,
    offset: int = 0,
    table: str = RAW_DATA_TABLE,
    start_ts: str | None = None,
    end_ts: str | None = None,
    device_id: str | None = None,
    order_desc: bool = True,
    bucket: int | None = None,
    aggregate_mode: str = "full",
    signal_id: str | None = None,
) -> dict[str, object]:
    if table == "signal_buckets":
        if not device_id or not signal_id:
            raise HTTPException(status_code=400, detail="signal_buckets requires device_id (node ID) and signal_id")
        rows = get_signal_timeseries(
            node_id=device_id, signal_id=signal_id, start_ts=start_ts,
            end_ts=end_ts, bucket_seconds=bucket, limit=limit, offset=offset,
            order_desc=order_desc, aggregate_mode=aggregate_mode,
        )
        if bucket is None:
            return {"ok": True, table: rows}
        return {"ok": True, "bucket": bucket, "aggregate_mode": aggregate_mode, "aggregates": rows}
    if signal_id is not None:
        raise HTTPException(status_code=400, detail="signal_id requires table='signal_buckets'")
    if table not in ALLOWED_TABLES:
        raise HTTPException(status_code=400, detail="Invalid table")

    if bucket is not None:
        if table != RAW_DATA_TABLE:
            raise HTTPException(
                status_code=400,
                detail=f"Aggregation currently supports only table='{RAW_DATA_TABLE}'",
            )
        aggregates = get_postgres_aggregated(
            table=table,
            bucket_seconds=bucket,
            start_ts=start_ts,
            end_ts=end_ts,
            device_id=device_id,
            limit=limit,
            offset=offset,
            order_desc=order_desc,
            aggregate_mode="lite" if aggregate_mode == "lite" else "full",
        )
        return {
            "ok": True,
            "bucket": bucket,
            "aggregate_mode": aggregate_mode,
            "aggregates": aggregates,
        }

    rows = get_postgres(
        table=table,
        limit=limit,
        offset=offset,
        start_ts=start_ts,
        end_ts=end_ts,
        device_id=device_id,
        order_desc=order_desc,
    )
    return {"ok": True, table: rows}


def fetch_timeseries_summary(
    *,
    table: str = RAW_DATA_TABLE,
    start_ts: str | None = None,
    end_ts: str | None = None,
    device_id: str | None = None,
) -> dict[str, object]:
    if table not in ALLOWED_TABLES:
        raise HTTPException(status_code=400, detail="Invalid table")

    if table != RAW_DATA_TABLE:
        raise HTTPException(
            status_code=400,
            detail=f"Summary currently supports only table='{RAW_DATA_TABLE}'",
        )

    summary = get_postgres_summary(
        table=table,
        start_ts=start_ts,
        end_ts=end_ts,
        device_id=device_id,
    )
    return {"ok": True, "summary": summary}

