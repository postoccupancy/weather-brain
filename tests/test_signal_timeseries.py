"""SQL integration tests use only transaction-local temporary tables.

Set TEST_DATABASE_URL to a PostgreSQL instance to run these tests.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import math
import os
import statistics

from fastapi import FastAPI
from fastapi.testclient import TestClient
import psycopg
import pytest

from app.api.auth import require_status_token
from app.api.timeseries_router import router
from app.retrieval.structured import signal_timeseries as db
from app.retrieval.structured import sql_queries as legacy


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_status_token] = lambda: None
    return TestClient(app)


@pytest.mark.parametrize("params", [
    {"table": "signal_buckets"},
    {"table": "signal_buckets", "device_id": "a"},
    {"table": "signal_buckets", "signal_id": "temperature"},
    {"signal_id": "temperature"},
])
def test_selector_validation(client, params):
    assert client.get('/timeseries', params=params).status_code == 400


@pytest.fixture
def archive(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL for temporary-table PostgreSQL tests")
    with psycopg.connect(url) as conn:
        conn.execute("SET LOCAL statement_timeout = '10s'")
        conn.execute('''CREATE TEMP TABLE signal_buckets (
            node_id text, signal_id text, bucket_start timestamptz, unit text,
            mean double precision, min double precision, max double precision,
            stddev double precision, sample_count integer) ON COMMIT DROP''')
        conn.execute('''CREATE TEMP TABLE readings (
            device_id text, ts timestamptz, temp_c double precision,
            temp_f double precision, rh double precision) ON COMMIT DROP''')
        # Source samples: [0, 2] and [8, 8, 12, 12]. Missing second 01.
        for node, signal, second, mean, low, high, sd, count in [
            ('electric-sky', 'temperature', 0, 1, 0, 2, 1, 2),
            ('electric-sky', 'temperature', 2, 10, 8, 12, 2, 4),
            ('electric-sky', 'temperature', 60, 50, 50, 50, 0, 1),
            ('other', 'temperature', 0, 90, 90, 90, 0, 3),
            ('electric-sky', 'humidity', 0, 30, 30, 30, 0, 5),
        ]:
            conn.execute('''INSERT INTO signal_buckets VALUES (%s, %s,
                '2026-09-28T00:00:00Z'::timestamptz + %s * interval '1 second',
                NULL, %s, %s, %s, %s, %s)''',
                (node, signal, second, mean, low, high, sd, count))
        conn.execute("INSERT INTO readings VALUES ('old', '2026-09-28T00:00:00Z', 10, 50, 40), ('old', '2026-09-28T00:00:02Z', 20, 68, 60)")

        @contextmanager
        def connection(*args, **kwargs):
            yield conn

        monkeypatch.setattr(db, 'DATABASE_URL', url)
        monkeypatch.setattr(legacy, 'DATABASE_URL', url)
        monkeypatch.setattr(db.psycopg, 'connect', connection)
        yield conn
        conn.rollback()


def query(client, **kwargs):
    params = dict(table='signal_buckets', device_id='electric-sky',
                  signal_id='temperature', order_desc='false')
    params.update(kwargs)
    response = client.get('/timeseries', params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_stored_seconds_and_one_second_resolution(client, archive):
    rows = query(client)['signal_buckets']
    assert [r['sample_count'] for r in rows] == [2, 4, 1]
    assert [r['temperature'] for r in rows] == [1, 10, 50]
    assert rows[0]['ts'] == rows[0]['bucket_start']
    aggregated = query(client, bucket=1)['aggregates']
    assert [r['mean'] for r in aggregated] == [1, 10, 50]
    assert [r['stddev'] for r in aggregated] == [1, 2, 0]


@pytest.mark.parametrize('resolution', [60, 600, 900, 3600])
def test_weighted_population_statistics(client, archive, resolution):
    rows = query(client, bucket=resolution, start_ts='2026-09-28T00:00:00Z',
                 end_ts='2026-09-28T00:00:59Z')['aggregates']
    assert len(rows) == 1
    row = rows[0]
    original = [0, 2, 8, 8, 12, 12]
    assert row['sample_count'] == row['count'] == len(original)
    assert row['bucket_count'] == 2
    assert row['mean'] == row['temperature_avg'] == statistics.mean(original)
    assert row['min'] == 0 and row['max'] == 12
    assert row['stddev'] == pytest.approx(statistics.pstdev(original))
    assert row['stddev'] == pytest.approx(math.sqrt(21))
    assert row['unit'] is None
    assert datetime.fromisoformat(row['first_ts']) == datetime(2026, 9, 28, tzinfo=timezone.utc)
    assert datetime.fromisoformat(row['last_ts']) == datetime(2026, 9, 28, 0, 0, 2, tzinfo=timezone.utc)
    assert (datetime.fromisoformat(row['bucket_end']) -
            datetime.fromisoformat(row['bucket_start'])).total_seconds() == resolution


@pytest.mark.parametrize('bucket', [None, 60])
def test_node_signal_selection(client, archive, bucket):
    options = {} if bucket is None else {'bucket': bucket}
    key = 'signal_buckets' if bucket is None else 'aggregates'
    assert query(client, device_id='other', **options)[key][0]['mean'] == 90
    assert query(client, signal_id='humidity', **options)[key][0]['mean'] == 30
    assert query(client, device_id="other' OR true --", **options)[key] == []
    assert query(client, signal_id='missing', **options)[key] == []


def test_pagination_boundaries_lite_and_empty(client, archive):
    assert query(client, bucket=60, order_desc='true', limit=1)['aggregates'][0]['mean'] == 50
    assert query(client, bucket=60, offset=1, limit=1)['aggregates'][0]['mean'] == 50
    row = query(client, bucket=60, aggregate_mode='lite', limit=1)['aggregates'][0]
    assert row['temperature_avg'] == 7 and row['sample_count'] == 6
    assert 'stddev' not in row and 'temperature_min' not in row
    assert query(client, bucket=60, start_ts='2026-09-28T00:00:03Z',
                 end_ts='2026-09-28T00:00:59Z')['aggregates'] == []
    assert query(client, start_ts='2026-09-28T00:00:02Z',
                 end_ts='2026-09-28T00:00:02Z')['signal_buckets'][0]['mean'] == 10


def test_null_summary_is_not_zero_filled(client, archive):
    archive.execute("UPDATE signal_buckets SET stddev = NULL WHERE node_id = 'electric-sky' AND mean = 10")
    row = query(client, bucket=60)['aggregates'][0]
    assert row['sample_count'] == 2 and row['mean'] == 1 and row['stddev'] == 1


def test_centered_variance_preserves_small_spread(client, archive):
    archive.execute("UPDATE signal_buckets SET mean = mean + 1e12, min = min + 1e12, max = max + 1e12")
    row = query(client, bucket=60)['aggregates'][0]
    assert row['mean'] == 1e12 + 7
    assert row['stddev'] == pytest.approx(math.sqrt(21))


def test_legacy_api_unchanged(client, archive):
    response = client.get('/timeseries', params={'device_id': 'old'})
    assert response.status_code == 200
    assert len(response.json()['readings']) == 2
    for mode in ('full', 'lite'):
        response = client.get('/timeseries', params=dict(device_id='old', bucket=60, aggregate_mode=mode))
        assert response.status_code == 200
        result = response.json()
        assert set(result) == {'ok', 'bucket', 'aggregate_mode', 'aggregates'}
        assert result['aggregates'][0]['temp_f_avg'] == 59
        assert result['aggregates'][0]['count'] == 2
