"""Durable correction progress consumed by the existing hourly indexing loop."""
from datetime import datetime, timedelta, timezone
import logging

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from app.database import DATABASE_URL
from app.providers.ollama.config import SNAPSHOT_DATA_TABLE, RAG_SNAPSHOT_TABLE
from app.retrieval.vector.electric_sea_snapshots import build_electric_sea_snapshot_records, store_electric_sea_snapshots


def process_correction_rebuilds(*, db_url=DATABASE_URL, build=build_electric_sea_snapshot_records,
                                store=store_electric_sea_snapshots, node_id=None):
    # Session lock allows embeddings outside a transaction and recovers on process exit.
    with psycopg.connect(db_url, autocommit=True, row_factory=dict_row, connect_timeout=5) as conn:
        if not conn.execute('SELECT pg_try_advisory_lock(781904322) AS acquired').fetchone()['acquired']:
            return
        try:
            jobs = conn.execute("SELECT * FROM deployment_corrections WHERE rebuild_status <> 'complete' AND (%s::text IS NULL OR node_id=%s) ORDER BY corrected_at", (node_id,node_id)).fetchall()
            for job in jobs:
                try:
                    start = job['rebuild_next']
                    completed = (datetime.now(timezone.utc) - timedelta(seconds=30)).replace(minute=0, second=0, microsecond=0)
                    end = min(start + timedelta(days=1), job['rebuild_end'], completed)
                    if start >= end:
                        continue
                    records = build(node_id=job['node_id'], start_time=start, end_time=end, db_url=db_url)
                    existing = conn.execute(sql.SQL('SELECT window_start FROM {} WHERE device_id=%s AND window_start >= %s AND window_start < %s')
                        .format(sql.Identifier(SNAPSHOT_DATA_TABLE)), (job['node_id'],start,end)).fetchall()
                    produced = {datetime.fromisoformat(r.metadata['window_start']) for r in records}
                    if any(r['window_start'] not in produced for r in existing):
                        raise ValueError('An invalidated snapshot has no source buckets; cannot mark it rebuilt')
                    count = store(records, db_url=db_url, archive=SNAPSHOT_DATA_TABLE, collection=RAG_SNAPSHOT_TABLE)
                    conn.execute('''UPDATE deployment_corrections SET rebuild_next=%s,
                        rebuild_status=%s, rebuild_error=NULL, rebuilt_windows=rebuilt_windows+%s,
                        rebuilt_at=clock_timestamp() WHERE id=%s''',
                        (end, 'complete' if end == job['rebuild_end'] else 'pending', count, job['id']))
                except Exception:
                    logging.getLogger(__name__).exception('Correction snapshot rebuild failed')
                    conn.execute("UPDATE deployment_corrections SET rebuild_status='failed', rebuild_error=%s WHERE id=%s",
                                 ('Snapshot build/store failed; check server logs. Automatically retried next indexing pass.', job['id']))
        finally:
            conn.execute('SELECT pg_advisory_unlock(781904322)')
