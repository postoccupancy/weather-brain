"""Deployment API integration; TEST_DATABASE_URL uses disposable isolated schemas."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4
import os

from fastapi import FastAPI
from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
import pytest

from app.api.deployments_router import router
from app.api.signal_buckets_router import router as ingest_router
from app.retrieval.structured import deployments, sql_queries


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('STATUS_TOKEN', 'status-test')
    monkeypatch.setenv('INGEST_TOKEN', 'ingest-test')
    app = FastAPI()
    app.include_router(router)
    app.include_router(ingest_router)
    return TestClient(app)


@pytest.fixture
def database(monkeypatch):
    url = os.getenv('TEST_DATABASE_URL')
    if not url:
        pytest.skip('Set TEST_DATABASE_URL to run isolated PostgreSQL integration tests')
    connect = psycopg.connect
    schema = 'test_deployments_' + uuid4().hex
    migration = (Path(__file__).resolve().parents[1] / 'migrations/20260928_electric_sea_signal_buckets.sql').read_text()
    with connect(url) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        conn.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
        conn.execute(migration.strip().removeprefix('begin;').removesuffix('commit;'))

    def isolated_connect(*args, **kwargs):
        kwargs['options'] = f'-csearch_path={schema} -cstatement_timeout=10000'
        return connect(url, **kwargs)

    monkeypatch.setattr(deployments, 'DATABASE_URL', url)
    monkeypatch.setattr(sql_queries, 'DATABASE_URL', url)
    monkeypatch.setattr(psycopg, 'connect', isolated_connect)
    try:
        yield isolated_connect
    finally:
        # Only this generated test schema is ever removed, never public tables.
        assert schema.startswith('test_deployments_') and len(schema) == 49
        with connect(url) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


READ = {'X-Status-Token': 'status-test'}
WRITE = {'X-Ingest-Token': 'ingest-test'}
PATH = '/nodes/electric-sky/deployment'


def start(client, **values):
    return client.post(PATH + '/start', headers=WRITE, json={'name': 'Home Office', **values})


def test_auth_and_validation(client):
    assert client.get(PATH).status_code == 401
    assert client.post(PATH + '/start', json={'name': 'Office'}).status_code == 401
    assert client.post(PATH + '/start', headers=READ, json={'name': 'Office'}).status_code == 401
    for values in ({'name': ' '}, {'latitude': 91}, {'longitude': -181}, {'started_at': '2026-01-01T00:00:00'}, {'extra': 1}):
        assert start(client, **values).status_code == 422


def test_no_active_deployment_is_clean_and_does_not_create_node(client, database):
    assert client.get(PATH, headers=READ).json() == {'ok': True, 'node_id': 'electric-sky', 'deployment': None}
    with database() as conn:
        assert conn.execute('SELECT count(*) FROM nodes').fetchone()[0] == 0


def test_start_retrieve_end_and_history(client, database):
    response = start(client, location_label='Seattle', latitude=47.6, longitude=-122.3,
                     altitude_m=12, notes='Desk', metadata={'floor': 2}, started_at='2026-01-01T00:00:00Z')
    assert response.status_code == 200, response.text
    active = response.json()['deployment']
    assert active['name'] == 'Home Office' and active['ended_at'] is None
    assert active['metadata'] == {'floor': 2} and active['altitude_m'] == 12
    assert client.get(PATH, headers=READ).json()['deployment'] == active
    assert start(client).status_code == 409
    response = client.post(PATH + '/end', headers=WRITE, json={
        'expected_deployment_id': active['id'], 'ended_at': '2026-01-02T00:00:00Z'})
    assert response.status_code == 200
    assert response.json()['deployment'] is None
    assert response.json()['ended_deployment']['ended_at'] is not None
    assert client.get(PATH, headers=READ).json()['deployment'] is None
    with database() as conn:
        assert conn.execute('SELECT count(*) FROM node_deployments').fetchone()[0] == 1


def test_change_is_atomic_and_stale_end_does_not_close_new_deployment(client, database):
    old = start(client, started_at='2026-01-01T00:00:00Z').json()['deployment']
    response = client.post(PATH + '/change', headers=WRITE, json={
        'name': 'Studio', 'expected_deployment_id': old['id'], 'started_at': '2026-01-02T00:00:00Z'})
    assert response.status_code == 200, response.text
    new = response.json()['deployment']
    assert new['id'] != old['id']
    assert response.json()['ended_deployment']['ended_at'] == new['started_at']
    assert client.post(PATH + '/end', headers=WRITE, json={'expected_deployment_id': old['id']}).status_code == 409
    # A rejected temporal change must leave the active row untouched.
    assert client.post(PATH + '/change', headers=WRITE, json={
        'name': 'Bad', 'expected_deployment_id': new['id'], 'started_at': '2025-01-01T00:00:00Z'}).status_code == 409
    assert client.get(PATH, headers=READ).json()['deployment']['id'] == new['id']
    with database() as conn:
        assert conn.execute('SELECT count(*) FROM node_deployments').fetchone()[0] == 2


def test_simultaneous_starts_have_exactly_one_winner(client, database):
    barrier = Barrier(2)
    def attempt():
        barrier.wait()
        return start(client).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(outcomes) == [200, 409]
    with database() as conn:
        assert conn.execute('SELECT count(*) FROM node_deployments WHERE ended_at IS NULL').fetchone()[0] == 1
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("INSERT INTO node_deployments (node_id,name) VALUES ('electric-sky','Bypass')")


def ingest(client, second):
    return client.post('/ingest/signal-buckets', headers=WRITE, json={
        'node_id': 'electric-sky', 'buckets': [{'signal_id': 'temperature',
            'bucket_start': f'2026-01-01T00:00:{second:02d}Z', 'mean': 10, 'min': 9, 'max': 11,
            'stddev': 1, 'sample_count': 8}]})


def test_ingestion_follows_current_deployment_and_retries_preserve_history(client, database):
    assert ingest(client, 0).json()['deployment_id'] is None
    old = start(client).json()['deployment']
    assert ingest(client, 1).json()['deployment_id'] == old['id']
    assert client.post(PATH + '/end', headers=WRITE, json={'expected_deployment_id': old['id']}).status_code == 200
    assert ingest(client, 2).json()['deployment_id'] is None
    new = start(client, name='Studio').json()['deployment']
    assert ingest(client, 3).json()['deployment_id'] == new['id']
    assert ingest(client, 1).json()['duplicates'] == 1
    with database() as conn:
        ids = [row[0] for row in conn.execute('SELECT deployment_id FROM signal_buckets ORDER BY bucket_start')]
        assert ids == [None, UUID(old['id']), None, UUID(new['id'])]


def test_backfill_node_target_null_and_half_open_range(client, database):
    for second in (0, 1, 2, 3):
        assert ingest(client, second).status_code == 200
    active = start(client).json()['deployment']
    # Already assigned record inside the range must remain unchanged.
    with database() as conn:
        other = conn.execute("INSERT INTO node_deployments(node_id,name,ended_at) VALUES ('electric-sky','Older',now()) RETURNING id").fetchone()[0]
        conn.execute("UPDATE signal_buckets SET deployment_id = %s WHERE bucket_start = '2026-01-01T00:00:02Z'", (other,))
        conn.execute("INSERT INTO nodes(id) VALUES ('indoor-sky')")
        conn.execute("INSERT INTO signal_buckets(node_id,signal_id,bucket_start,mean,min,max,stddev,sample_count) VALUES ('indoor-sky','temperature','2026-01-01T00:00:01Z',1,1,1,0,1)")
    start_at = datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
    end_at = datetime(2026, 1, 1, 0, 0, 3, tzinfo=timezone.utc)
    args = ('electric-sky', UUID(active['id']), start_at, end_at)
    assert deployments.backfill(*args) == 1
    assert deployments.backfill(*args) == 1 # dry-run did not mutate
    assert deployments.backfill(*args, apply=True) == 1
    assert deployments.backfill(*args, apply=True) == 0 # safely repeatable
    with pytest.raises(ValueError, match='does not belong'):
        deployments.backfill('indoor-sky', UUID(active['id']), start_at, end_at, apply=True)
    with database() as conn:
        rows = conn.execute('SELECT node_id, deployment_id FROM signal_buckets ORDER BY node_id,bucket_start').fetchall()
        assert rows == [('electric-sky', None), ('electric-sky', UUID(active['id'])),
                        ('electric-sky', other), ('electric-sky', None), ('indoor-sky', None)]
