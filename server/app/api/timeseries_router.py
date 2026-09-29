from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Query

from app.api.auth import require_status_token
from app.retrieval.structured.timeseries import (
    fetch_timeseries,
    fetch_timeseries_summary,
)


router = APIRouter()

RAW_DATA_TABLE = os.getenv("RAW_DATA_TABLE", "readings")


@router.get("/timeseries", dependencies=[Depends(require_status_token)])
def get_readings(
    limit: int = Query(default=100, lte=10000),
    offset: int = Query(default=0, ge=0),
    table: str = Query(default=RAW_DATA_TABLE),
    start_ts: str | None = Query(default=None),
    end_ts: str | None = Query(default=None),
    device_id: str | None = Query(default=None),
    order_desc: bool = Query(default=True),
    bucket: int | None = Query(default=None, ge=1),
    aggregate_mode: str = Query(default="full"),
    signal_id: str | None = Query(default=None, min_length=1, max_length=255),
):
    return fetch_timeseries(
        limit=limit,
        offset=offset,
        table=table,
        start_ts=start_ts,
        end_ts=end_ts,
        device_id=device_id,
        order_desc=order_desc,
        bucket=bucket,
        aggregate_mode=aggregate_mode,
        signal_id=signal_id,
    )


@router.get("/timeseries/summary", dependencies=[Depends(require_status_token)])
def get_readings_summary(
    table: str = Query(default=RAW_DATA_TABLE),
    start_ts: str | None = Query(default=None),
    end_ts: str | None = Query(default=None),
    device_id: str | None = Query(default=None),
):
    return fetch_timeseries_summary(
        table=table,
        start_ts=start_ts,
        end_ts=end_ts,
        device_id=device_id,
    )
