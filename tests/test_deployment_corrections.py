from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

from test_deployments import client, database, READ, WRITE
from app.retrieval.structured import deployment_corrections as dc
from app.retrieval.structured.deployments import DeploymentConflict
from app.retrieval.vector.correction_rebuilds import process_correction_rebuilds

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
BOUNDARY = START + timedelta(days=2)
ADMIN = START + timedelta(days=4)
NODE = 'indoor-sky'
PATH = '/nodes/indoor-sky/deployment/'


@pytest.fixture
def seeded(database):
    with database() as c:
        migration = (Path(__file__).resolve().parents[1] / 'migrations/20261026_deployment_corrections.sql').read_text()
        c.execute(migration.strip().removeprefix('begin;').removesuffix('commit;'))
        c.execute('CREATE TABLE snapshots(device_id text,window_start timestamptz,window_end timestamptz,snapshot_text text)')
        c.execute("INSERT INTO nodes(id) VALUES ('indoor-sky'),('other')")
        old = c.execute('INSERT INTO node_deployments(node_id,name,started_at,ended_at) VALUES (%s,%s,%s,%s) RETURNING id',
                        (NODE, 'Home Office', START, ADMIN)).fetchone()[0]
        new = c.execute('INSERT INTO node_deployments(node_id,name,started_at) VALUES (%s,%s,%s) RETURNING id',
                        (NODE, 'Gallery', ADMIN)).fetchone()[0]
        for node, dep, when, signal in [
            (NODE, old, BOUNDARY-timedelta(seconds=1), 'temperature'),
            (NODE, old, BOUNDARY, 'temperature'),
            (NODE, old, ADMIN-timedelta(microseconds=1), 'temperature'),
            (NODE, old, ADMIN, 'temperature'),
            (NODE, new, BOUNDARY+timedelta(seconds=1), 'temperature'),
            (NODE, None, BOUNDARY+timedelta(seconds=2), 'temperature'),
            ('other', old, BOUNDARY, 'temperature'),
            (NODE, old, BOUNDARY, 'humidity')]:
            c.execute('''INSERT INTO signal_buckets(node_id,deployment_id,bucket_start,signal_id,mean,min,max,stddev,sample_count)
                VALUES (%s,%s,%s,%s,1,1,1,0,1)''', (node,dep,when,signal))
    return database, old, new


def args(seeded):
    _, old, new = seeded
    return NODE, old, new, BOUNDARY, 'Physical move recorded late'


def test_explicit_scope_atomic_audit_and_repeat(seeded):
    connect, old, new = seeded
    preview = dc.correct(*args(seeded))
    assert [(c['signal_id'],c['count']) for c in preview['counts']] == [('humidity',1),('temperature',2)]
    assert preview['validation_errors'] == []
    result = dc.correct(*args(seeded), preview_token=preview['preview_token'])
    assert result['rebuild_status'] == 'pending' and result['operator_identity'] is None
    assert dc.correct(*args(seeded), preview_token=preview['preview_token'])['id'] == result['id']
    with connect() as c:
        rows = c.execute("SELECT deployment_id FROM signal_buckets WHERE node_id=%s AND signal_id='temperature' ORDER BY bucket_start", (NODE,)).fetchall()
        assert [r[0] for r in rows] == [old,new,new,None,new,old]
        assert c.execute("SELECT deployment_id FROM signal_buckets WHERE node_id='other'").fetchone()[0] == old
        assert c.execute('SELECT ended_at FROM node_deployments WHERE id=%s',(old,)).fetchone()[0] == BOUNDARY
        assert c.execute('SELECT started_at FROM node_deployments WHERE id=%s',(new,)).fetchone()[0] == BOUNDARY
        assert c.execute('SELECT count(*) FROM deployment_corrections').fetchone()[0] == 1


def test_stale_history_and_measurements(seeded):
    connect, old, new = seeded
    p = dc.correct(*args(seeded))
    with connect() as c:
        c.execute('UPDATE node_deployments SET notes=%s WHERE id=%s', ('Edited concurrently',new))
    with pytest.raises(DeploymentConflict, match='stale'):
        dc.correct(*args(seeded),preview_token=p['preview_token'])
    p = dc.correct(*args(seeded))
    with connect() as c:
        c.execute("DELETE FROM signal_buckets WHERE node_id=%s AND signal_id='humidity'", (NODE,))
    with pytest.raises(DeploymentConflict, match='stale'):
        dc.correct(*args(seeded),preview_token=p['preview_token'])
    assert dc.history(NODE)[0]['ended_at'] == ADMIN


def test_rollback_after_boundary_and_bucket_updates(seeded):
    connect, old, new = seeded
    p = dc.correct(*args(seeded))
    with connect() as c:
        c.execute("ALTER TABLE deployment_corrections ADD CONSTRAINT injected_failure CHECK (reason = 'never')")
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        dc.correct(*args(seeded),preview_token=p['preview_token'])
    assert dc.correct(*args(seeded))['counts'] == p['counts']
    assert dc.history(NODE)[0]['ended_at'] == ADMIN


def test_validation_and_overlap(seeded):
    connect, old, new = seeded
    for node,a,b,t in [('other',old,new,BOUNDARY),(NODE,old,new,START),(NODE,new,old,BOUNDARY),
                       (NODE,old,new,BOUNDARY.replace(tzinfo=None))]:
        with pytest.raises(DeploymentConflict): dc.correct(node,a,b,t,'Reason')
    with connect() as c:
        c.execute('INSERT INTO node_deployments(node_id,name,started_at,ended_at) VALUES (%s,%s,%s,%s)',
                  (NODE,'Overlap', START-timedelta(days=1), START+timedelta(hours=1)))
    with pytest.raises(DeploymentConflict,match='overlap'): dc.correct(*args(seeded))


def test_reverse_transition_and_snapshot_scope(seeded):
    _, old, new = seeded
    p = dc.correct(NODE,old,new,ADMIN+timedelta(hours=1),'Actually later')
    assert p['source_deployment_id'] == str(new) and p['target_deployment_id'] == str(old)
    p = dc.correct(*args(seeded))
    assert datetime.fromisoformat(p['snapshots']['start']) <= BOUNDARY-timedelta(hours=1)
    assert datetime.fromisoformat(p['snapshots']['end']) >= ADMIN+timedelta(days=30)


def test_auth_history_and_status(client, seeded):
    _, old, new = seeded
    body = dict(old_deployment_id=str(old),new_deployment_id=str(new),boundary=BOUNDARY.isoformat(),reason='Late update')
    assert client.get('/nodes/indoor-sky/deployments').status_code == 401
    assert client.get('/nodes/indoor-sky/deployments',headers=READ).status_code == 200
    assert client.get(f'/nodes/other/deployments/{old}',headers=READ).status_code == 404
    assert client.get(f'/nodes/indoor-sky/deployments/{old}',headers=READ).status_code == 200
    assert client.post(PATH+'correction-preview',json=body).status_code == 401
    p = client.post(PATH+'correction-preview',json=body,headers=READ).json()
    body['preview_token'] = p['preview_token']
    assert client.post(PATH+'correction-apply',json=body,headers=READ).status_code == 401
    assert client.post(PATH+'correction-apply',json=body,headers=WRITE).status_code == 200
    assert client.get(PATH+'correction-status',headers=READ).json()['corrections'][0]['rebuild_status'] == 'pending'


def test_failed_rebuild_is_visible_retryable_and_progress_is_durable(seeded):
    connect, old, new = seeded
    p = dc.correct(*args(seeded))
    dc.correct(*args(seeded),preview_token=p['preview_token'])
    def failure(**kwargs): raise RuntimeError('private provider details')
    process_correction_rebuilds(db_url='ignored',build=failure)
    status = dc.correction_status(NODE)[0]
    assert status['rebuild_status'] == 'failed'
    assert 'private provider' not in status['rebuild_error']
    assert status['rebuild_next'] == status['rebuild_start']
    with connect() as c:
        c.execute('UPDATE deployment_corrections SET rebuild_end=rebuild_start + interval \'1 hour\'')
    calls = []
    from app.retrieval.vector.snapshots import SnapshotRecord
    record = SnapshotRecord('rebuilt', {'window_start':status['rebuild_start'].isoformat()})
    def build(**kwargs): calls.append(kwargs); return [record]
    def store(records,**kwargs): assert records == [record]; return 1
    process_correction_rebuilds(db_url='ignored',build=build,store=store)
    status = dc.correction_status(NODE)[0]
    assert status['rebuild_status'] == 'complete' and status['rebuilt_windows'] == 1
    assert calls[0]['node_id'] == NODE


def test_archive_warnings_and_stale_rag_exclusion(seeded, monkeypatch):
    from app.retrieval.structured import timeseries
    from app.frameworks.langchain.answering import _documents_to_answer_contexts
    from langchain_core.documents import Document
    from fastapi import HTTPException
    p = dc.correct(*args(seeded))
    dc.correct(*args(seeded),preview_token=p['preview_token'])
    monkeypatch.setattr(timeseries,'get_postgres',lambda **kw: [])
    response = timeseries.fetch_timeseries(table='snapshots',device_id=NODE)
    assert response['snapshot_corrections'][0]['rebuild_status'] == 'pending'
    with pytest.raises(HTTPException) as error:
        _documents_to_answer_contexts([Document(page_content='old',metadata={'node_id':NODE,'window_start':BOUNDARY.isoformat()})])
    assert error.value.status_code == 409
    assert len(_documents_to_answer_contexts([Document(page_content='other',metadata={'node_id':'other','window_start':BOUNDARY.isoformat()})])) == 1


def test_prior_snapshot_text_extends_rebuild_scope(seeded):
    connect,old,new = seeded
    earlier = START-timedelta(days=2)
    with connect() as c:
        c.execute('INSERT INTO snapshots VALUES (%s,%s,%s,%s)', (NODE,earlier,earlier+timedelta(hours=1),f'Electric Sea deployment {old}'))
    p = dc.correct(*args(seeded))
    assert datetime.fromisoformat(p['snapshots']['start']) == earlier
    assert p['snapshots']['persisted_count'] == 1


def test_concurrent_apply_is_one_audit_and_changed_request_is_rejected(seeded):
    from concurrent.futures import ThreadPoolExecutor
    p = dc.correct(*args(seeded))
    def apply(_): return dc.correct(*args(seeded),preview_token=p['preview_token'])['id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(apply,range(2)))
    assert ids[0] == ids[1]
    node,old,new,boundary,reason = args(seeded)
    with pytest.raises(DeploymentConflict,match='another correction'):
        dc.correct(node,old,new,boundary,'Different reason',preview_token=p['preview_token'])


def test_missing_source_cannot_silently_complete_rebuild(seeded):
    connect,old,new = seeded
    p = dc.correct(*args(seeded))
    dc.correct(*args(seeded),preview_token=p['preview_token'])
    with connect() as c:
        c.execute('INSERT INTO snapshots VALUES (%s,%s,%s,%s)',
                  (NODE,p['snapshots']['start'],BOUNDARY,f'Old document {old}'))
    def never_store(*a,**kw): pytest.fail('Missing source must stop before store')
    process_correction_rebuilds(db_url='ignored',build=lambda **kw: [],store=never_store)
    assert dc.correction_status(NODE)[0]['rebuild_status'] == 'failed'
