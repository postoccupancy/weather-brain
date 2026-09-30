from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import math
import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row, tuple_row
import pytest

from app.retrieval.vector import electric_sea_snapshots as es
from app.retrieval.vector.snapshots import build_snapshot_records, SnapshotRecord

START = datetime(2026, 1, 5, tzinfo=timezone.utc)
END = START + timedelta(hours=1)


@pytest.fixture
def database(monkeypatch):
    url = os.getenv('TEST_DATABASE_URL')
    if not url:
        pytest.skip('Set TEST_DATABASE_URL for temporary-table PostgreSQL integration')
    conn = psycopg.connect(url)
    migration = (Path(__file__).resolve().parents[1] / 'migrations/20260928_electric_sea_signal_buckets.sql').read_text()
    migration = migration.strip().removeprefix('begin;').removesuffix('commit;').replace('create table if not exists', 'create temp table')
    conn.execute(migration)
    conn.execute('''CREATE TEMP TABLE snapshots (device_id text,window_start timestamptz,window_end timestamptz,snapshot_text text,
        UNIQUE(device_id,window_start,window_end))''')
    conn.execute('''CREATE TEMP TABLE langchain_pg_collection(uuid uuid PRIMARY KEY DEFAULT gen_random_uuid(),name text)''')
    conn.execute('''CREATE TEMP TABLE langchain_pg_embedding(id text PRIMARY KEY,collection_id uuid,document text,
        cmetadata jsonb,embedding extensions.vector)''')
    cid = conn.execute("INSERT INTO langchain_pg_collection(name) VALUES ('esp32_rag') RETURNING uuid").fetchone()[0]
    conn.execute("INSERT INTO langchain_pg_embedding VALUES ('legacy',%s,'untouched','{}','[1,2,3]'::extensions.vector)", (cid,))
    conn.execute("INSERT INTO nodes(id) VALUES ('electric-sky'),('indoor-sky')")
    dep = conn.execute("INSERT INTO node_deployments(node_id,name,started_at) VALUES ('electric-sky','Office',%s) RETURNING id", (START-timedelta(days=20),)).fetchone()[0]
    conn.commit()

    @contextmanager
    def connect(*args, **kwargs):
        previous = conn.row_factory
        conn.row_factory = kwargs.get('row_factory', tuple_row)
        try:
            with conn.transaction():
                yield conn
        finally:
            conn.row_factory = previous

    monkeypatch.setattr(es.psycopg, 'connect', connect)
    try:
        yield conn, dep
    finally:
        conn.rollback()
        conn.close() # temporary tables disappear


def add(conn, dep, t, mean=1, sd=1, n=2, signal='temperature', unit='celsius', node='electric-sky'):
    conn.execute('''INSERT INTO signal_buckets(node_id,deployment_id,signal_id,unit,bucket_start,mean,min,max,stddev,sample_count)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''', (node,dep,signal,unit,t,mean,mean-sd,mean+sd,sd,n))


def build():
    return build_snapshot_records(source='signal_buckets', start_time=START, end_time=END)


def test_weighted_statistics_gaps_and_independent_nodes(database):
    conn, dep = database
    add(conn, dep, START, mean=1, sd=1, n=2)
    add(conn, dep, START+timedelta(seconds=2), mean=10, sd=2, n=4)
    add(conn, None, START, mean=90, sd=0, n=1, node='indoor-sky')
    conn.commit()
    records = build()
    assert len(records) == 2
    metric = records[0].metadata['deployments'][0]['signals'][0]
    assert metric['mean'] == 7 and metric['samples'] == 6
    assert metric['stddev'] == pytest.approx(math.sqrt(21))
    assert metric['min'] == 0 and metric['max'] == 12
    assert metric['seconds_present'] == 2
    assert '0/12 sufficiently covered' in records[0].text
    assert records[1].metadata['node_id'] == 'indoor-sky'


def test_deployment_sections_and_units_do_not_mix(database):
    conn, dep = database
    conn.execute('UPDATE node_deployments SET ended_at=%s WHERE id=%s', (START+timedelta(minutes=30), dep))
    new = conn.execute("INSERT INTO node_deployments(node_id,name,started_at) VALUES ('electric-sky','Studio',%s) RETURNING id", (START+timedelta(minutes=30),)).fetchone()[0]
    add(conn, dep, START, mean=1)
    add(conn, new, START+timedelta(minutes=30), mean=20)
    add(conn, new, START+timedelta(minutes=31), mean=68, signal='temperature', unit='fahrenheit')
    conn.commit()
    record = build()[0]
    sections = record.metadata['deployments']
    assert len(sections) == 2
    assert {s['deployment']['name'] for s in sections} == {'Office', 'Studio'}
    studio = next(s for s in sections if s['deployment']['name'] == 'Studio')
    assert {s['mean'] for s in studio['signals']} == {20,68}


def test_repeat_write_late_data_and_transaction_rollback(database):
    conn, dep = database
    add(conn, dep, START); conn.commit()
    records = build()
    embed = lambda texts: [[1.,2.,3.] for _ in texts]
    assert es.store_electric_sea_snapshots(records, embed=embed) == 1
    assert es.store_electric_sea_snapshots(records, embed=embed) == 1
    add(conn, dep, START+timedelta(seconds=1), mean=9); conn.commit()
    newer = build()
    es.store_electric_sea_snapshots(newer, embed=embed)
    assert conn.execute('SELECT count(*) FROM snapshots').fetchone()[0] == 1
    assert conn.execute('SELECT count(*) FROM langchain_pg_embedding').fetchone()[0] == 2
    assert conn.execute("SELECT document FROM langchain_pg_embedding WHERE id='legacy'").fetchone()[0] == 'untouched'
    assert conn.execute('SELECT snapshot_text FROM snapshots').fetchone()[0] == newer[0].text
    conn.commit()
    # Inject vector write failure after the archive update; archive must roll back too.
    conn.execute("ALTER TABLE langchain_pg_embedding ADD CONSTRAINT reject_failure CHECK(document != 'failure')")
    conn.commit()
    with pytest.raises(psycopg.errors.CheckViolation):
        es.store_electric_sea_snapshots([SnapshotRecord('failure', newer[0].metadata)], embed=embed)
    assert conn.execute('SELECT snapshot_text FROM snapshots').fetchone()[0] == newer[0].text
    conn.commit()
    with pytest.raises(ValueError, match='dimension'):
        es.store_electric_sea_snapshots(newer, embed=lambda _: [[1.]])


def test_baselines_are_deployment_scoped_and_events_reach_document(database):
    conn, dep = database
    # Full seconds for three historical days and current hour.
    for day in (1,2,3):
        conn.execute('''INSERT INTO signal_buckets(node_id,deployment_id,signal_id,unit,bucket_start,mean,min,max,stddev,sample_count)
            SELECT 'electric-sky',%s,'temperature','celsius',t,20,20,20,0,7
            FROM generate_series(%s::timestamptz,%s::timestamptz,interval '1 second') t''',
            (dep,START-timedelta(days=day),END-timedelta(days=day,seconds=1)))
    conn.execute('''INSERT INTO signal_buckets(node_id,deployment_id,signal_id,unit,bucket_start,mean,min,max,stddev,sample_count)
        SELECT 'electric-sky',%s,'temperature','celsius',t,24,24,24,0,3
        FROM generate_series(%s::timestamptz,%s::timestamptz,interval '1 second') t''', (dep,START,END-timedelta(seconds=1)))
    conn.commit()
    assert 'sustained Temperature high' in build()[0].text
    conn.execute('UPDATE signal_buckets SET deployment_id=NULL WHERE bucket_start < %s', (START,))
    conn.commit()
    assert 'sustained Temperature high' not in build()[0].text
    assert '0/12 baseline-assessable' in build()[0].text


def test_bounds_and_empty_hour(database):
    assert build() == []
    with pytest.raises(ValueError):
        es.build_electric_sea_snapshot_records(lookback_hours=None)
    with pytest.raises(ValueError):
        es.build_electric_sea_snapshot_records(start_time=START.replace(tzinfo=None),end_time=END)


def test_service_dispatch_and_no_implicit_historical_rebuild(monkeypatch):
    from fastapi import HTTPException
    from app.retrieval.vector import service, snapshots
    calls = []
    monkeypatch.setattr(snapshots, 'build_snapshot_records', lambda **kwargs: calls.append(kwargs) or [])
    result = service.index_snapshots(db_url='unused', source='signal_buckets', framework='langchain', lookback_hours=3)
    assert result['snapshots_indexed'] == 0
    assert calls[0]['source'] == 'signal_buckets' and calls[0]['lookback_hours'] == 3
    with pytest.raises(HTTPException) as error:
        service.index_snapshots(db_url='unused', source='signal_buckets', framework='langchain')
    assert error.value.status_code == 400
    with pytest.raises(HTTPException):
        service.index_snapshots(db_url='unused', source='signal_buckets', framework='llamaindex', lookback_hours=1)
    monkeypatch.setattr(service, 'index_snapshots_with_langchain', lambda **kwargs: {'snapshots_indexed': 9})
    assert service.index_snapshots(db_url='unused', framework='langchain')['snapshots_indexed'] == 9
