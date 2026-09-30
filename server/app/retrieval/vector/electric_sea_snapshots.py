"""Electric Sea input adapter for hourly snapshot documents and their existing PG store."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.database import DATABASE_URL
from app.retrieval.vector.snapshot_events import Bucket, EventSettings, describe_events
from app.retrieval.vector.snapshots import SnapshotRecord


def _rollups(conn, node, start, end, seconds, events_only=False):
    # Two-pass centered population variance; no averages of means/stddevs.
    return conn.execute("""
        WITH source AS (
          SELECT *, date_bin(%(seconds)s * interval '1 second', bucket_start,
                             timestamptz '2000-01-01 00:00:00+00') AS period
          FROM signal_buckets WHERE node_id = %(node)s
            AND bucket_start >= %(start)s AND bucket_start < %(end)s
            AND (NOT %(events_only)s OR (signal_id='temperature' AND unit='celsius')
                 OR (signal_id='humidity' AND unit='percent'))
            AND sample_count > 0 AND mean IS NOT NULL AND stddev IS NOT NULL
            AND min IS NOT NULL AND max IS NOT NULL
        ), centered AS (
          SELECT *, sum(mean * sample_count) OVER w / sum(sample_count) OVER w AS pooled_mean
          FROM source WINDOW w AS (PARTITION BY period, deployment_id, signal_id, unit)
        )
        SELECT period, deployment_id, signal_id, unit,
          sum(sample_count) AS samples,
          count(DISTINCT date_trunc('second', bucket_start)) AS seconds_present,
          max(pooled_mean) AS mean, min(min) AS min, max(max) AS max,
          sqrt(greatest(0::double precision,
            sum(sample_count * (stddev * stddev + (mean-pooled_mean)*(mean-pooled_mean)))
            / sum(sample_count))) AS stddev
        FROM centered GROUP BY period, deployment_id, signal_id, unit
        ORDER BY period, deployment_id NULLS FIRST, signal_id, unit
    """, dict(node=node, start=start, end=end, seconds=seconds, events_only=events_only)).fetchall()


def _record(node, start, stats, history, deployments, settings):
    end = start + timedelta(hours=1)
    sections = []
    lines = [f"Electric Sea hourly snapshot for node '{node}'",
             f"Window (UTC): {start.isoformat()} to {end.isoformat()}",
             "Source: signal_buckets; population statistics weighted by original sample counts.",
             "Coverage counts stored seconds out of the full hour, not expected high-rate samples.",
             "Deployment sections are separate; missing seconds are not filled."]
    for dep_id in dict.fromkeys(r['deployment_id'] for r in stats):
        deployment = deployments.get(dep_id)
        rows = [r for r in stats if r['deployment_id'] == dep_id]
        lines += ['', f"Deployment: {deployment['name'] if deployment else 'Unassigned'} (ID: {dep_id or 'none'})"]
        if deployment:
            lines.append(f"Location: {deployment['location_label'] or 'unspecified'}; "
                         f"latitude={deployment['latitude']}, longitude={deployment['longitude']}, "
                         f"altitude_m={deployment['altitude_m']}")
            lines.append(f"Deployment interval: {deployment['started_at']} to {deployment['ended_at'] or 'open'}")
            if deployment['notes']:
                lines.append(f"Deployment notes: {deployment['notes']}")
        signals = []
        for r in rows:
            signal = {key: r[key] for key in ('signal_id', 'unit', 'samples', 'seconds_present', 'mean', 'min', 'max', 'stddev')}
            signals.append(signal)
            lines.append(f"{r['signal_id']} [{r['unit'] or 'unknown unit'}]: mean={r['mean']:.3f}, "
                         f"min={r['min']:.3f}, max={r['max']:.3f}, population stddev={r['stddev']:.3f}; "
                         f"samples={r['samples']}; stored seconds={r['seconds_present']}/3600 "
                         f"({r['seconds_present']/36:.2f}% of hour).")
        points = {}
        for r in history:
            if r['deployment_id'] != dep_id:
                continue
            # Unknown placement is not a defensible historical baseline.
            if dep_id is None and r['period'] < start - timedelta(hours=1):
                continue
            metric = {('temperature', 'celsius'): 0, ('humidity', 'percent'): 1}.get((r['signal_id'], r['unit']))
            if metric is None:
                continue
            values = points.setdefault(r['period'], [None, None, 0, 0])
            values[metric], values[metric + 2] = r['mean'], r['seconds_present']
        event_text = describe_events([Bucket(t, *values) for t, values in points.items()], start, end, settings)
        lines += ['Event scope: temperature/celsius and humidity/percent only; other signals not assessed.',
                  'Event coverage gate uses stored seconds; deployment boundaries interrupt evidence.', event_text]
        sections.append(dict(deployment_id=str(dep_id) if dep_id else None,
                             deployment=deployment, signals=signals))
    metadata = dict(kind='hourly_snapshot', source='signal_buckets', node_id=node, device_id=node,
                    window_start=start.isoformat(), window_end=end.isoformat(),
                    event_analysis_version=1, snapshot_version=2, deployments=sections)
    # JSON-normalize timestamps/UUIDs for vector metadata.
    return SnapshotRecord('\n'.join(lines), json.loads(json.dumps(metadata, default=str)))


def build_electric_sea_snapshot_records(*, lookback_hours=3, start_time=None, end_time=None,
                                    node_id=None, db_url=DATABASE_URL):
    """Completed UTC hours only. Explicit historical bounds avoid accidental all-time rebuilds."""
    end = end_time or datetime.now(timezone.utc) - timedelta(seconds=30)
    if end.utcoffset() is None or (start_time is not None and start_time.utcoffset() is None):
        raise ValueError('Snapshot bounds must include a timezone')
    end = end.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    if end > datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0):
        raise ValueError('Only completed hours can be snapshotted')
    if start_time is None:
        if lookback_hours is None or not 1 <= lookback_hours <= 168:
            raise ValueError('Use explicit start/end for historical rebuilds; lookback must be 1..168 hours')
        start = end - timedelta(hours=lookback_hours)
    else:
        start = start_time.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    if start >= end:
        raise ValueError('Snapshot start must precede end')
    settings = EventSettings(
        timezone=os.getenv('SNAPSHOT_EVENT_TIMEZONE', 'America/Los_Angeles'),
        temperature_delta_c=float(os.getenv('SNAPSHOT_EVENT_TEMPERATURE_DELTA_C', '2')),
        humidity_delta_pp=float(os.getenv('SNAPSHOT_EVENT_HUMIDITY_DELTA_PP', '5')),
        sample_period_seconds=1,
    )
    records = []
    with psycopg.connect(db_url, row_factory=dict_row, connect_timeout=5) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        conn.execute("SET LOCAL statement_timeout = '60s'")
        nodes = conn.execute('''SELECT DISTINCT node_id FROM signal_buckets
            WHERE bucket_start >= %s AND bucket_start < %s AND (%s::text IS NULL OR node_id=%s)
            ORDER BY node_id''', (start, end, node_id, node_id)).fetchall()
        for node in nodes:
            name = node['node_id']
            deployments = {d['id']: d for d in conn.execute('SELECT * FROM node_deployments WHERE node_id=%s', (name,)).fetchall()}
            day = start
            while day < end:
                until = min(day + timedelta(days=1), end)
                stats = _rollups(conn, name, day, until, 3600)
                history = _rollups(conn, name, day-timedelta(days=30), until+timedelta(hours=1), 300, events_only=True)
                for hour in dict.fromkeys(r['period'] for r in stats):
                    hour = hour.astimezone(timezone.utc)
                    records.append(_record(name, hour, [r for r in stats if r['period'] == hour], history, deployments, settings))
                day = until
    return records


def electric_sea_snapshot_id(metadata):
    identity = json.dumps([metadata['node_id'], metadata['window_start']], separators=(',', ':'))
    return 'electric-sea:hourly:' + hashlib.sha256(identity.encode()).hexdigest()


def store_electric_sea_snapshots(records, *, db_url=DATABASE_URL, archive='snapshots', collection='esp32_rag', embed=None):
    """Prepare embeddings first, then atomically upsert archive and existing LangChain collection."""
    if not records:
        return 0
    if embed is None:
        from app.frameworks.langchain.runtime import get_embeddings
        embed = get_embeddings().embed_documents
    vectors = embed([r.text for r in records])
    if len(vectors) != len(records) or any(not v or any(not math.isfinite(x) for x in v) for v in vectors):
        raise ValueError('Invalid embedding response')
    with psycopg.connect(db_url, connect_timeout=5) as conn:
        conn.execute("SET LOCAL statement_timeout = '60s'")
        conn.execute("SET LOCAL lock_timeout = '10s'")
        conn.execute("SET LOCAL search_path = public, extensions")
        # Serialize all Electric Sea writers, including manual rebuilds and the scheduler.
        conn.execute("SELECT pg_advisory_xact_lock(781904321)")
        row = conn.execute('SELECT uuid FROM langchain_pg_collection WHERE name=%s', (collection,)).fetchone()
        if not row:
            raise ValueError('Existing snapshot vector collection not found')
        collection_id = row[0]
        dimension = conn.execute('SELECT vector_dims(embedding) FROM langchain_pg_embedding WHERE collection_id=%s AND embedding IS NOT NULL LIMIT 1', (collection_id,)).fetchone()
        expected_dim = dimension[0] if dimension else 768
        if any(len(v) != expected_dim for v in vectors):
            raise ValueError('Embedding dimension differs from existing collection')
        for record, vector in zip(records, vectors):
            m = record.metadata
            snapshot_id = electric_sea_snapshot_id(m)
            existing = conn.execute(sql.SQL('SELECT snapshot_text FROM {} WHERE device_id=%s AND window_start=%s AND window_end=%s FOR UPDATE').format(sql.Identifier(archive)),
                                    (m['node_id'], m['window_start'], m['window_end'])).fetchone()
            if existing and not existing[0].startswith('Electric Sea hourly snapshot for node '):
                raise ValueError('Refusing to overwrite a legacy archive snapshot')
            conn.execute(sql.SQL('''INSERT INTO {} (device_id,window_start,window_end,snapshot_text)
                VALUES (%s,%s,%s,%s) ON CONFLICT(device_id,window_start,window_end)
                DO UPDATE SET snapshot_text=excluded.snapshot_text''').format(sql.Identifier(archive)),
                (m['node_id'], m['window_start'], m['window_end'], record.text))
            result = conn.execute('''INSERT INTO langchain_pg_embedding (id,collection_id,document,cmetadata,embedding)
                VALUES (%s,%s,%s,%s,%s::vector) ON CONFLICT(id) DO UPDATE SET
                document=excluded.document,cmetadata=excluded.cmetadata,embedding=excluded.embedding
                WHERE langchain_pg_embedding.collection_id=excluded.collection_id''',
                (snapshot_id, collection_id, record.text, Jsonb(m), str(vector)))
            if result.rowcount != 1:
                raise ValueError('Snapshot vector ID belongs to a different collection')
    return len(records)
