"""Transactional deployment history and explicitly scoped archive backfill."""
from contextlib import contextmanager
from datetime import datetime

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.database import DATABASE_URL


class DeploymentConflict(ValueError):
    pass


@contextmanager
def connection():
    if not DATABASE_URL:
        raise RuntimeError("Database is not configured")
    with psycopg.connect(DATABASE_URL, connect_timeout=5) as conn:
        conn.execute("SET LOCAL statement_timeout = '15s'")
        conn.execute("SET LOCAL lock_timeout = '5s'")
        yield conn


def current_deployment(node_id):
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM node_deployments WHERE node_id = %s AND ended_at IS NULL", (node_id,))
        return cur.fetchone()


def manage_deployment(node_id, action, values):
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("INSERT INTO nodes (id) VALUES (%s) ON CONFLICT DO NOTHING", (node_id,))
        # Serialize management requests on the permanent node, including no-open states.
        cur.execute("SELECT id FROM nodes WHERE id = %s FOR UPDATE", (node_id,))
        cur.execute("SELECT * FROM node_deployments WHERE node_id = %s AND ended_at IS NULL", (node_id,))
        active = cur.fetchone()
        if action == "start" and active:
            raise DeploymentConflict("End or change the current deployment first")
        if action != "start" and (not active or active['id'] != values['expected_deployment_id']):
            raise DeploymentConflict("Current deployment changed; refresh before retrying")
        cur.execute("SELECT clock_timestamp() AS now")
        now = cur.fetchone()['now']
        timestamp = values.get('ended_at' if action == 'end' else 'started_at') or now
        if timestamp > now:
            raise DeploymentConflict("Future deployment times are not supported")
        if active and timestamp < active['started_at']:
            raise DeploymentConflict("End/change time precedes the current deployment start")
        cur.execute("SELECT max(ended_at) AS latest_end FROM node_deployments WHERE node_id = %s", (node_id,))
        latest_end = cur.fetchone()['latest_end']
        if latest_end and timestamp < latest_end:
            raise DeploymentConflict("Deployment time overlaps existing history")
        if active:
            cur.execute("UPDATE node_deployments SET ended_at = %s WHERE id = %s RETURNING *", (timestamp, active['id']))
            ended = cur.fetchone()
            if action == 'end':
                return {'deployment': None, 'ended_deployment': ended}
        params = {key: values.get(key) for key in (
            'name', 'location_label', 'latitude', 'longitude', 'altitude_m', 'notes')}
        params.update(node_id=node_id, started_at=timestamp, metadata=Jsonb(values.get('metadata', {})))
        cur.execute("""INSERT INTO node_deployments
            (node_id, name, location_label, latitude, longitude, altitude_m, notes, started_at, metadata)
            VALUES (%(node_id)s, %(name)s, %(location_label)s, %(latitude)s, %(longitude)s,
                    %(altitude_m)s, %(notes)s, %(started_at)s, %(metadata)s) RETURNING *""", params)
        result = {'deployment': cur.fetchone()}
        if active:
            result['ended_deployment'] = ended
        return result


def backfill(node_id, deployment_id, start: datetime, end: datetime, *, apply=False):
    if start.utcoffset() is None or end.utcoffset() is None or start >= end:
        raise ValueError("Require timezone-aware start < end")
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT id FROM node_deployments WHERE id = %s AND node_id = %s FOR SHARE", (deployment_id, node_id))
        if not cur.fetchone():
            raise ValueError("Deployment does not belong to the requested node")
        params = (deployment_id, node_id, start, end)
        where = "node_id = %s AND deployment_id IS NULL AND bucket_start >= %s AND bucket_start < %s"
        if apply:
            cur.execute("UPDATE signal_buckets SET deployment_id = %s WHERE " + where, params)
            return cur.rowcount
        cur.execute("SELECT count(*) AS count FROM signal_buckets WHERE " + where, params[1:])
        return cur.fetchone()['count']
