"""Read Electric Sea sufficient statistics without touching legacy readings.

Producer contract: each stddev is population stddev (divisor n, not n-1).
See docs/signal-timeseries.md for the API and pooled variance convention.
"""
from __future__ import annotations

import psycopg
from fastapi import HTTPException
from psycopg import sql
from psycopg.rows import dict_row

from app.database import DATABASE_URL


def get_signal_timeseries(
    *, node_id: str, signal_id: str, start_ts: str | None = None,
    end_ts: str | None = None, bucket_seconds: int | None = None,
    limit: int = 100, offset: int = 0, order_desc: bool = True,
    aggregate_mode: str = "full",
) -> list[dict]:
    if bucket_seconds is not None and bucket_seconds < 1:
        raise ValueError("bucket_seconds must be >= 1")
    if not DATABASE_URL:
        raise HTTPException(status_code=503, detail="Database query unavailable")
    params = dict(node_id=node_id, signal_id=signal_id, bucket_seconds=bucket_seconds,
                  limit=limit, offset=offset)
    filters = [sql.SQL("node_id = %(node_id)s"), sql.SQL("signal_id = %(signal_id)s")]
    for name, operator, value in (("start_ts", ">=", start_ts), ("end_ts", "<=", end_ts)):
        if value is not None:
            filters.append(sql.SQL("bucket_start {} {}::timestamptz").format(
                sql.SQL(operator), sql.Placeholder(name)))
            params[name] = value
    where = sql.SQL(" AND ").join(filters)
    direction = sql.SQL("DESC" if order_desc else "ASC")
    if bucket_seconds is None:
        query = sql.SQL("""
            SELECT *, bucket_start AS ts, node_id AS device_id
            FROM signal_buckets WHERE {where}
            ORDER BY bucket_start {direction} LIMIT %(limit)s OFFSET %(offset)s
        """).format(where=where, direction=direction)
    else:
        # Centered two-pass variance includes within- and between-bucket variance.
        # Ignore incomplete summaries defensively; never interpret unknowns as zero.
        query = sql.SQL("""
            WITH source AS (
                SELECT *, date_bin(%(bucket_seconds)s * interval '1 second',
                    bucket_start, timestamptz '1970-01-01 00:00:00+00') AS interval_start
                FROM signal_buckets WHERE {where}
                  AND sample_count > 0 AND mean IS NOT NULL AND min IS NOT NULL
                  AND max IS NOT NULL AND stddev IS NOT NULL
            ), centered AS (
                SELECT *, sum(mean * sample_count) OVER w / sum(sample_count) OVER w AS pooled_mean
                FROM source
                WINDOW w AS (PARTITION BY interval_start)
            )
            SELECT interval_start AS bucket_start,
                interval_start + %(bucket_seconds)s * interval '1 second' AS bucket_end,
                min(bucket_start) AS first_ts, max(bucket_start) AS last_ts,
                sum(sample_count) AS sample_count, sum(sample_count) AS count,
                count(*) AS bucket_count, max(pooled_mean) AS mean,
                min(min) AS min, max(max) AS max,
                sqrt(greatest(0::double precision,
                    sum(sample_count * (stddev * stddev +
                        (mean - pooled_mean) * (mean - pooled_mean))) / sum(sample_count))) AS stddev,
                CASE WHEN count(unit) = count(*) AND count(DISTINCT unit) = 1
                     THEN min(unit) END AS unit
            FROM centered GROUP BY interval_start
            ORDER BY interval_start {direction} LIMIT %(limit)s OFFSET %(offset)s
        """).format(where=where, direction=direction)
    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(query, params)
                rows = [dict(row) for row in cur.fetchall()]
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Database query failed") from exc
    for row in rows:
        if bucket_seconds is None:
            row.setdefault(signal_id, row["mean"])
        else:
            row.update(node_id=node_id, device_id=node_id, signal_id=signal_id)
            row[f"{signal_id}_avg"] = row["mean"]
            if aggregate_mode == "lite":
                for key in ("min", "max", "stddev"):
                    row.pop(key)
            else:
                for key in ("min", "max", "stddev"):
                    row[f"{signal_id}_{key}"] = row[key]
    return rows
