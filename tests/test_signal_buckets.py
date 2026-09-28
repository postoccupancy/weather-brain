from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.retrieval.structured import sql_queries as db


@pytest.fixture
def cursor(monkeypatch):
    cursor = MagicMock()
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor
    monkeypatch.setattr(db, "DATABASE_URL", "postgresql://test@localhost/test")
    monkeypatch.setattr(db.psycopg, "connect", MagicMock(return_value=connection))
    return cursor


def bucket(signal_id: str = "temperature") -> dict[str, object]:
    return {
        "bucket_start": "2026-09-28T12:00:00Z",
        "signal_id": signal_id,
        "unit": "celsius",
        "mean": 21.5,
        "min": 20.0,
        "max": 23.0,
        "stddev": 0.75,
        "sample_count": 100,
    }


def test_signal_bucket_insert_uses_active_deployment_and_conflict_key(cursor):
    deployment_id = UUID("12345678-1234-5678-1234-567812345678")
    cursor.fetchone.side_effect = [{"id": deployment_id}, {"node_id": "electric-sky"}, None]

    result = db.insert_signal_buckets(
        node_id="electric-sky",
        buckets=[
            {**bucket("temperature"), "bucket_start": datetime(2026, 9, 28, 12, tzinfo=timezone.utc), "metadata": {}},
            {**bucket("humidity"), "bucket_start": datetime(2026, 9, 28, 12, tzinfo=timezone.utc), "metadata": {}},
        ],
    )

    assert result == db.SignalBucketInsertResult(accepted=2, inserted=1, deployment_id=str(deployment_id))
    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert "INSERT INTO nodes" in statements[0]
    assert "ended_at IS NULL" in statements[1]
    assert "ON CONFLICT (node_id, signal_id, bucket_start) DO NOTHING" in statements[2]
    assert cursor.execute.call_args_list[2].args[1]["deployment_id"] == deployment_id


def test_signal_bucket_api_reports_duplicate_retry(monkeypatch):
    from app import main
    from app.api import signal_buckets_router

    monkeypatch.setenv("INGEST_TOKEN", "test-token")
    insert = MagicMock(return_value=db.SignalBucketInsertResult(accepted=2, inserted=0, deployment_id=None))
    monkeypatch.setattr(signal_buckets_router, "insert_signal_buckets", insert)

    response = TestClient(main.app).post(
        "/ingest/signal-buckets",
        headers={"X-Ingest-Token": "test-token"},
        json={"node_id": "electric-sky", "buckets": [bucket("temperature"), bucket("humidity")]},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "accepted": 2, "inserted": 0, "duplicates": 2, "deployment_id": None}
    assert insert.call_args.kwargs["node_id"] == "electric-sky"


def test_signal_bucket_api_rejects_invalid_aggregates_and_raw_payload(monkeypatch):
    from app import main

    monkeypatch.setenv("INGEST_TOKEN", "test-token")
    client = TestClient(main.app)
    invalid_bounds = {**bucket(), "min": 24.0}
    response = client.post("/ingest/signal-buckets", headers={"X-Ingest-Token": "test-token"}, json={"node_id": "electric-sky", "buckets": [invalid_bounds]})
    assert response.status_code == 422

    raw_payload = {**bucket(), "samples": [1, 2, 3]}
    response = client.post("/ingest/signal-buckets", headers={"X-Ingest-Token": "test-token"}, json={"node_id": "electric-sky", "buckets": [raw_payload]})
    assert response.status_code == 422
