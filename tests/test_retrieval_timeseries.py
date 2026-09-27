from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.retrieval.structured import timeseries


def test_fetch_timeseries_rejects_invalid_table() -> None:
    with pytest.raises(HTTPException) as exc_info:
        timeseries.fetch_timeseries(table="not_a_real_table")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid table"


def test_fetch_timeseries_rejects_bucketing_snapshots() -> None:
    with pytest.raises(HTTPException) as exc_info:
        timeseries.fetch_timeseries(table=timeseries.SNAPSHOT_DATA_TABLE, bucket=60)

    assert exc_info.value.status_code == 400
    assert "Aggregation currently supports only" in exc_info.value.detail


def test_fetch_timeseries_uses_aggregated_query_for_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_get_postgres_aggregated(**kwargs: object) -> list[dict[str, object]]:
        captured.update(kwargs)
        return [{"bucket_start": "2026-04-07T00:00:00Z", "count": 4}]

    monkeypatch.setattr(timeseries, "get_postgres_aggregated", fake_get_postgres_aggregated)

    result = timeseries.fetch_timeseries(
        table=timeseries.RAW_DATA_TABLE,
        bucket=300,
        aggregate_mode="lite",
        device_id="esp32-s3-devkit-001",
        limit=25,
        offset=5,
        order_desc=False,
    )

    assert result == {
        "ok": True,
        "bucket": 300,
        "aggregate_mode": "lite",
        "aggregates": [{"bucket_start": "2026-04-07T00:00:00Z", "count": 4}],
    }
    assert captured["table"] == timeseries.RAW_DATA_TABLE
    assert captured["bucket_seconds"] == 300
    assert captured["aggregate_mode"] == "lite"
    assert captured["device_id"] == "esp32-s3-devkit-001"
    assert captured["limit"] == 25
    assert captured["offset"] == 5
    assert captured["order_desc"] is False


def test_fetch_timeseries_uses_row_query_without_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_get_postgres(**kwargs: object) -> list[dict[str, object]]:
        captured.update(kwargs)
        return [{"ts": "2026-04-07T00:00:00Z", "temp_f": 72.0}]

    monkeypatch.setattr(timeseries, "get_postgres", fake_get_postgres)

    result = timeseries.fetch_timeseries(
        table=timeseries.RAW_DATA_TABLE,
        start_ts="2026-04-07T00:00:00Z",
        end_ts="2026-04-07T01:00:00Z",
        device_id="esp32-s3-devkit-001",
    )

    assert result == {
        "ok": True,
        timeseries.RAW_DATA_TABLE: [{"ts": "2026-04-07T00:00:00Z", "temp_f": 72.0}],
    }
    assert captured["table"] == timeseries.RAW_DATA_TABLE
    assert captured["start_ts"] == "2026-04-07T00:00:00Z"
    assert captured["end_ts"] == "2026-04-07T01:00:00Z"
    assert captured["device_id"] == "esp32-s3-devkit-001"


def test_fetch_timeseries_summary_rejects_snapshot_table() -> None:
    with pytest.raises(HTTPException) as exc_info:
        timeseries.fetch_timeseries_summary(table=timeseries.SNAPSHOT_DATA_TABLE)

    assert exc_info.value.status_code == 400
    assert "Summary currently supports only" in exc_info.value.detail


def test_fetch_timeseries_summary_returns_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_postgres_summary(**kwargs: object) -> dict[str, object]:
        assert kwargs["table"] == timeseries.RAW_DATA_TABLE
        return {"count": 12, "temp_f_avg": 68.4}

    monkeypatch.setattr(timeseries, "get_postgres_summary", fake_get_postgres_summary)

    result = timeseries.fetch_timeseries_summary(
        table=timeseries.RAW_DATA_TABLE,
        device_id="esp32-s3-devkit-001",
    )

    assert result == {"ok": True, "summary": {"count": 12, "temp_f_avg": 68.4}}
