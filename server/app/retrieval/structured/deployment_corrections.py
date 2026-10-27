"""Explicit adjacent-boundary correction; previews never write to the database."""
from datetime import datetime, timedelta, timezone
import hashlib
import json

from psycopg.rows import dict_row
from psycopg import sql
from psycopg.types.json import Jsonb

from app.retrieval.structured.deployments import connection, DeploymentConflict
from app.providers.ollama.config import SNAPSHOT_DATA_TABLE


def normalized(value):
    return json.loads(json.dumps(value, default=str))


def fingerprint(value):
    return hashlib.sha256(json.dumps(normalized(value), sort_keys=True).encode()).hexdigest()


def history(node_id, deployment_id=None):
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute('SELECT * FROM node_deployments WHERE node_id=%s ORDER BY started_at,id', (node_id,))
        rows = cur.fetchall()
        return rows if deployment_id is None else next((r for r in rows if r['id'] == deployment_id), None)


def proposed_history(rows, node_id, old_id, new_id, boundary):
    if boundary.utcoffset() is None:
        raise DeploymentConflict('Boundary must include a timezone')
    if boundary > datetime.now(timezone.utc):
        raise DeploymentConflict('Boundary cannot be in the future')
    ids = [str(r['id']) for r in rows]
    if str(old_id) not in ids or str(new_id) not in ids or old_id == new_id:
        raise DeploymentConflict('Two distinct deployments must belong to the requested node')
    old_index, new_index = ids.index(str(old_id)), ids.index(str(new_id))
    old, new = rows[old_index], rows[new_index]
    if any(r['node_id'] != node_id for r in rows):
        raise DeploymentConflict('Deployment belongs to another node')
    if new_index != old_index + 1 or old['ended_at'] != new['started_at']:
        raise DeploymentConflict('Select an adjacent transition with a shared boundary')
    proposed = [dict(r) for r in rows]
    proposed[old_index]['ended_at'] = boundary
    proposed[new_index]['started_at'] = boundary
    for i, row in enumerate(proposed):
        if row['ended_at'] is not None and row['started_at'] >= row['ended_at']:
            raise DeploymentConflict('Correction would invert or empty a deployment interval')
        if i and (proposed[i-1]['ended_at'] is None or proposed[i-1]['ended_at'] > row['started_at']):
            raise DeploymentConflict('Deployment intervals overlap')
    previous = new['started_at']
    # Moving later transfers only the selected successor's buckets back to its predecessor.
    source, target = (old_id, new_id) if boundary <= previous else (new_id, old_id)
    return proposed, min(boundary, previous), max(boundary, previous), source, target


def _preview(cur, node_id, old_id, new_id, boundary, reason):
    if not reason.strip() or len(reason) > 1000:
        raise DeploymentConflict('Supply a correction reason of 1..1000 characters')
    cur.execute('SELECT * FROM node_deployments WHERE node_id=%s ORDER BY started_at,id', (node_id,))
    rows = cur.fetchall()
    proposed, start, end, source, target = proposed_history(rows, node_id, old_id, new_id, boundary)
    cur.execute('''SELECT signal_id, count(*) AS count, max(received_at) AS latest_received
        FROM signal_buckets WHERE node_id=%s AND deployment_id=%s
        AND bucket_start >= %s AND bucket_start < %s GROUP BY signal_id ORDER BY signal_id''',
        (node_id, source, start, end))
    counts = cur.fetchall()
    # Boundary text is embedded in EVERY document for either deployment, including
    # genuine older history. Event context adds the previous hour and 30 future days.
    cur.execute('''SELECT min(bucket_start) AS first, max(bucket_start) AS last
        FROM signal_buckets WHERE node_id=%s AND deployment_id IN (%s,%s)''', (node_id, old_id, new_id))
    extent = cur.fetchone()
    cur.execute(sql.SQL('''SELECT min(window_start) AS first, max(window_end) AS last
        FROM {} WHERE device_id=%s AND (position(%s in snapshot_text)>0 OR position(%s in snapshot_text)>0)''')
        .format(sql.Identifier(SNAPSHOT_DATA_TABLE)), (node_id, str(old_id), str(new_id)))
    stored = cur.fetchone()
    floor = lambda t: t.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    now = floor(datetime.now(timezone.utc))
    rebuild_start = floor(min(start - timedelta(hours=1), extent['first'] or start, stored['first'] or start))
    # Include the in-progress hour; the worker waits for completion before advancing.
    rebuild_end = min(now + timedelta(hours=1), floor(max(end + timedelta(days=30, hours=1), extent['last'] or end)) + timedelta(hours=1))
    if stored['last'] and stored['last'] > rebuild_end:
        rebuild_end = stored['last']
    cur.execute(sql.SQL('''SELECT window_start,window_end FROM {} WHERE device_id=%s
        AND window_start >= %s AND window_start < %s ORDER BY window_start''')
        .format(sql.Identifier(SNAPSHOT_DATA_TABLE)), (node_id, rebuild_start, rebuild_end))
    windows = cur.fetchall()
    result = dict(node_id=node_id, current=rows, proposed=proposed,
                  old_deployment_id=old_id, new_deployment_id=new_id,
                  boundary=boundary, reason=reason.strip(),
                  interval={'start': start, 'end': end, 'end_exclusive': True},
                  source_deployment_id=source, target_deployment_id=target,
                  counts=counts, validation_errors=[],
                  snapshots={'start': rebuild_start, 'end': rebuild_end,
                             'persisted_windows': windows,
                             'persisted_count': len(windows),
                             'hours': int((rebuild_end-rebuild_start).total_seconds()/3600),
                             'status': 'preview',
                             'scope': 'Both deployment interval texts, adjacent hour, and 30-day event baselines; bounded to existing/current hours.'})
    result = normalized(result)
    result['preview_token'] = fingerprint(result)
    return result


def correct(node_id, old_id, new_id, boundary, reason, *, preview_token=None):
    request = normalized(dict(node_id=node_id, old_id=old_id, new_id=new_id, boundary=boundary, reason=reason.strip()))
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        if preview_token is None:
            cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            return _preview(cur, node_id, old_id, new_id, boundary, reason)
        # Same permanent-node lock as ordinary deployment management and ingestion.
        cur.execute('SELECT id FROM nodes WHERE id=%s FOR UPDATE', (node_id,))
        cur.execute('SELECT id FROM node_deployments WHERE node_id=%s ORDER BY id FOR UPDATE', (node_id,))
        cur.execute('SELECT * FROM deployment_corrections WHERE preview_token=%s', (preview_token,))
        previous = cur.fetchone()
        if previous:
            if previous['request'] != request:
                raise DeploymentConflict('Preview belongs to another correction')
            return previous
        preview = _preview(cur, node_id, old_id, new_id, boundary, reason)
        if preview['preview_token'] != preview_token:
            raise DeploymentConflict('Preview is stale; refresh history and preview again')
        cur.execute('UPDATE node_deployments SET ended_at=%s WHERE node_id=%s AND id=%s', (boundary, node_id, old_id))
        cur.execute('UPDATE node_deployments SET started_at=%s WHERE node_id=%s AND id=%s', (boundary, node_id, new_id))
        cur.execute('''WITH changed AS (
            UPDATE signal_buckets SET deployment_id=%s WHERE node_id=%s AND deployment_id=%s
            AND bucket_start >= %s AND bucket_start < %s RETURNING signal_id)
            SELECT signal_id,count(*) AS count FROM changed GROUP BY signal_id ORDER BY signal_id''',
            (preview['target_deployment_id'], node_id, preview['source_deployment_id'],
             preview['interval']['start'], preview['interval']['end']))
        changed = cur.fetchall()
        if changed != [{'signal_id': r['signal_id'], 'count': r['count']} for r in preview['counts']]:
            raise DeploymentConflict('Measurements changed during correction; preview again')
        scope = preview['snapshots']
        # Shared-token authentication supplies no personal identity. Do not invent one.
        cur.execute('''INSERT INTO deployment_corrections
            (node_id,preview_token,reason,request,preview,changed_counts,rebuild_start,rebuild_end,rebuild_next)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
            (node_id, preview_token, reason.strip(), Jsonb(request), Jsonb(preview), Jsonb(changed),
             scope['start'], scope['end'], scope['start']))
        return cur.fetchone()


def correction_status(node_id):
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute('SELECT * FROM deployment_corrections WHERE node_id=%s ORDER BY corrected_at DESC', (node_id,))
        return cur.fetchall()


def snapshot_warnings(node_id=None):
    """Persisted invalidation ranges, kept visible until both stores are rebuilt."""
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute('''SELECT id,node_id,rebuild_next AS start,rebuild_end AS end,
            rebuild_status,rebuild_error FROM deployment_corrections
            WHERE rebuild_status <> 'complete' AND (%s::text IS NULL OR node_id=%s)
            ORDER BY corrected_at''', (node_id,node_id))
        return cur.fetchall()
