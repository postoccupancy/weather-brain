from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal, Optional

import psycopg
from fastapi.encoders import jsonable_encoder
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.database import DATABASE_URL


RAW_DATA_TABLE = os.getenv("RAW_DATA_TABLE", "readings")
SNAPSHOT_DATA_TABLE = os.getenv("SNAPSHOT_DATA_TABLE", "snapshots")
ALLOWED_TABLES = {RAW_DATA_TABLE, SNAPSHOT_DATA_TABLE}


@dataclass
class InsertResult:
    data: list[dict[str, Any]]


@dataclass
class SignalBucketInsertResult:
    accepted: int
    inserted: int
    deployment_id: str | None


def insert_postgres(row: dict[str, Any]) -> InsertResult | None:
    if not DATABASE_URL or RAW_DATA_TABLE not in ALLOWED_TABLES:
        return None
    try:
        if row:
            query = sql.SQL("INSERT INTO {table} ({columns}) VALUES ({values}) RETURNING *").format(
                table=sql.Identifier(RAW_DATA_TABLE),
                columns=sql.SQL(", ").join(map(sql.Identifier, row)),
                values=sql.SQL(", ").join(sql.Placeholder() for _ in row),
            )
        else:
            query = sql.SQL("INSERT INTO {} DEFAULT VALUES RETURNING *").format(
                sql.Identifier(RAW_DATA_TABLE)
            )
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                values = [
                    Jsonb(value) if isinstance(value, (dict, list)) else value
                    for value in row.values()
                ]
                cur.execute(query, values)
                return InsertResult(data=jsonable_encoder(cur.fetchall()))
    except Exception as exc:
        print("[postgres] insert error:", repr(exc))
        return None


def insert_signal_buckets(
    *, node_id: str, buckets: list[dict[str, Any]]
) -> SignalBucketInsertResult | None:
    """Insert one node's scalar aggregate buckets without duplicating retries."""
    if not DATABASE_URL:
        return None
    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "INSERT INTO nodes (id) VALUES (%s) ON CONFLICT (id) DO NOTHING",
                    (node_id,),
                )
                cur.execute(
                    "SELECT id FROM node_deployments "
                    "WHERE node_id = %s AND ended_at IS NULL "
                    "ORDER BY started_at DESC LIMIT 1",
                    (node_id,),
                )
                deployment = cur.fetchone()
                deployment_id = deployment["id"] if deployment else None
                inserted = 0
                for bucket in buckets:
                    cur.execute(
                        """
                        INSERT INTO signal_buckets (
                          node_id, deployment_id, signal_id, bucket_start, unit,
                          mean, min, max, stddev, sample_count, metadata
                        ) VALUES (
                          %(node_id)s, %(deployment_id)s, %(signal_id)s,
                          %(bucket_start)s, %(unit)s, %(mean)s, %(min)s, %(max)s,
                          %(stddev)s, %(sample_count)s, %(metadata)s
                        )
                        ON CONFLICT (node_id, signal_id, bucket_start) DO NOTHING
                        RETURNING node_id
                        """,
                        {
                            "node_id": node_id,
                            "deployment_id": deployment_id,
                            **bucket,
                            "metadata": Jsonb(bucket.get("metadata", {})),
                        },
                    )
                    inserted += int(cur.fetchone() is not None)
                return SignalBucketInsertResult(
                    accepted=len(buckets),
                    inserted=inserted,
                    deployment_id=str(deployment_id) if deployment_id is not None else None,
                )
    except Exception as exc:
        print("[postgres] signal bucket insert error:", repr(exc))
        return None


def get_postgres(
    *,
    table: str = RAW_DATA_TABLE,
    device_id: Optional[str] = None,
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None,
    limit: int = 10000,
    offset: int = 0,
    order_desc: bool = True,
) -> list[dict[str, Any]]:
    if table not in ALLOWED_TABLES:
        print(f"[postgres] get_postgres rejected invalid table: {table!r}")
        return []
    if not DATABASE_URL:
        return []
    try:
        time_column = "window_start" if table == SNAPSHOT_DATA_TABLE else "ts"
        clauses: list[sql.Composable] = []
        params: list[Any] = []
        if device_id is not None:
            clauses.append(sql.SQL("device_id = %s"))
            params.append(device_id)
        if start_ts is not None:
            clauses.append(sql.SQL("{} >= %s::timestamptz").format(sql.Identifier(time_column)))
            params.append(start_ts)
        if end_ts is not None:
            clauses.append(sql.SQL("{} <= %s::timestamptz").format(sql.Identifier(time_column)))
            params.append(end_ts)
        where = (
            sql.SQL("WHERE ") + sql.SQL(" AND ").join(clauses)
            if clauses else sql.SQL("")
        )
        query = sql.SQL("SELECT * FROM {table} {where} ORDER BY {time} {direction} LIMIT %s OFFSET %s").format(
            table=sql.Identifier(table), where=where, time=sql.Identifier(time_column),
            direction=sql.SQL("DESC" if order_desc else "ASC"),
        )
        params.extend([limit, offset])
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(query, params)
                rows = jsonable_encoder(cur.fetchall())
        print(
            f"[postgres] get_postgres: got {len(rows)} rows from '{table}' "
            f"(time_column={time_column})"
        )
        return rows
    except Exception as exc:
        print("[postgres] get_postgres error:", repr(exc))
        return []


def get_postgres_aggregated(
    *,
    table: str = RAW_DATA_TABLE,
    bucket_seconds: int,
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None,
    device_id: Optional[str] = None,
    limit: int = 1000,
    offset: int = 0,
    order_desc: bool = False,
    aggregate_mode: Literal["full", "lite"] = "full",
) -> list[dict[str, Any]]:
    """Return bucketed sensor aggregates from the raw readings table."""
    if not DATABASE_URL:
        print("[postgres] aggregate query skipped: DATABASE_URL is missing")
        return []

    if table not in ALLOWED_TABLES:
        print(f"[postgres] get_postgres_aggregated rejected invalid table: {table!r}")
        return []

    if bucket_seconds < 1:
        raise ValueError("bucket_seconds must be >= 1")

    order_direction = sql.SQL("DESC") if order_desc else sql.SQL("ASC")
    where_clause, where_params = _build_timeseries_where_clause(
        device_id=device_id,
        start_ts=start_ts,
        end_ts=end_ts,
    )

    if aggregate_mode == "lite":
        query = sql.SQL(
            """
            select
              date_bin(
                (%(bucket_seconds)s * interval '1 second'),
                ts,
                timestamptz '1970-01-01 00:00:00+00'
              ) as bucket_start,
              date_bin(
                (%(bucket_seconds)s * interval '1 second'),
                ts,
                timestamptz '1970-01-01 00:00:00+00'
              ) + (%(bucket_seconds)s * interval '1 second') as bucket_end,
              date_bin(
                (%(bucket_seconds)s * interval '1 second'),
                ts,
                timestamptz '1970-01-01 00:00:00+00'
              ) as first_ts,
              date_bin(
                (%(bucket_seconds)s * interval '1 second'),
                ts,
                timestamptz '1970-01-01 00:00:00+00'
              ) + (%(bucket_seconds)s * interval '1 second') as last_ts,
              count(*)::int as count,
              avg(temp_f) as temp_f_avg,
              avg(rh) as rh_avg
            from {table}
            {where_clause}
            group by bucket_start
            order by bucket_start {order_direction}
            limit %(limit)s offset %(offset)s;
            """
        ).format(
            table=sql.Identifier(table),
            where_clause=where_clause,
            order_direction=order_direction,
        )
    else:
        query = sql.SQL(
            """
            select
              date_bin(
                (%(bucket_seconds)s * interval '1 second'),
                ts,
                timestamptz '1970-01-01 00:00:00+00'
              ) as bucket_start,
              date_bin(
                (%(bucket_seconds)s * interval '1 second'),
                ts,
                timestamptz '1970-01-01 00:00:00+00'
              ) + (%(bucket_seconds)s * interval '1 second') as bucket_end,
              min(ts) as first_ts,
              max(ts) as last_ts,
              count(*)::int as count,
              avg(temp_c) as temp_c_avg,
              min(temp_c) as temp_c_min,
              max(temp_c) as temp_c_max,
              avg(temp_f) as temp_f_avg,
              min(temp_f) as temp_f_min,
              max(temp_f) as temp_f_max,
              avg(rh) as rh_avg,
              min(rh) as rh_min,
              max(rh) as rh_max
            from {table}
            {where_clause}
            group by bucket_start
            order by bucket_start {order_direction}
            limit %(limit)s offset %(offset)s;
            """
        ).format(
            table=sql.Identifier(table),
            where_clause=where_clause,
            order_direction=order_direction,
        )

    params = {
        "bucket_seconds": bucket_seconds,
        "limit": limit,
        "offset": offset,
    }
    params.update(where_params)

    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
                print(
                    f"[postgres] get_postgres_aggregated: got {len(rows)} rows from '{table}' "
                    f"(bucket_seconds={bucket_seconds})"
                )
                return [dict(row) for row in rows]
    except Exception as exc:
        print("[postgres] get_postgres_aggregated error:", repr(exc))
        return []


def _build_timeseries_where_clause(
    *,
    device_id: Optional[str],
    start_ts: Optional[str],
    end_ts: Optional[str],
) -> tuple[sql.Composable, dict[str, Any]]:
    clauses: list[sql.Composable] = []
    params: dict[str, Any] = {}

    if device_id is not None:
        clauses.append(sql.SQL("device_id = %(device_id)s::text"))
        params["device_id"] = device_id
    if start_ts is not None:
        clauses.append(sql.SQL("ts >= %(start_ts)s::timestamptz"))
        params["start_ts"] = start_ts
    if end_ts is not None:
        clauses.append(sql.SQL("ts <= %(end_ts)s::timestamptz"))
        params["end_ts"] = end_ts

    if not clauses:
        return sql.SQL(""), params

    return sql.SQL("where ") + sql.SQL(" and ").join(clauses), params


def get_postgres_summary(
    *,
    table: str = RAW_DATA_TABLE,
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None,
    device_id: Optional[str] = None,
) -> dict[str, Any]:
    """Return lightweight summary metrics for dashboard cards."""
    if not DATABASE_URL:
        print("[postgres] summary query skipped: DATABASE_URL is missing")
        return {}

    if table not in ALLOWED_TABLES:
        print(f"[postgres] get_postgres_summary rejected invalid table: {table!r}")
        return {}

    where_clause, where_params = _build_timeseries_where_clause(
        device_id=device_id,
        start_ts=start_ts,
        end_ts=end_ts,
    )

    query = sql.SQL(
        """
        select
          min(ts) as first_ts,
          max(ts) as last_ts,
          count(*)::int as count,
          avg(temp_c) as temp_c_avg,
          avg(temp_f) as temp_f_avg,
          min(temp_f) as temp_f_min,
          max(temp_f) as temp_f_max,
          avg(rh) as rh_avg,
          min(rh) as rh_min,
          max(rh) as rh_max
        from {table}
        {where_clause};
        """
    ).format(
        table=sql.Identifier(table),
        where_clause=where_clause,
    )

    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(query, where_params)
                row = cur.fetchone()
                summary = dict(row) if row is not None else {}
                print(
                    f"[postgres] get_postgres_summary: got summary from '{table}' "
                    f"(count={summary.get('count', 0)})"
                )
                return summary
    except Exception as exc:
        print("[postgres] get_postgres_summary error:", repr(exc))
        return {}
