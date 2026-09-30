from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import os
import psycopg
from psycopg import sql

DEVICE_ID = os.getenv("DEVICE_ID", "")
RAW_DATA_TABLE = os.getenv("RAW_DATA_TABLE", "")
from app.database import DATABASE_URL
from app.retrieval.vector.snapshot_events import Bucket, EventSettings, describe_events


@dataclass(frozen=True)
class SnapshotWindow:
    device_id: str
    window_start: datetime
    window_end: datetime


@dataclass(frozen=True)
class HourlySnapshot:
    device_id: str
    window_start: datetime
    window_end: datetime
    n: int
    t_min_c: float | None
    t_max_c: float | None
    t_avg_c: float | None
    t_min_f: float | None
    t_max_f: float | None
    t_avg_f: float | None
    h_min: float | None
    h_max: float | None
    h_avg: float | None


@dataclass(frozen=True)
class SnapshotRecord:
    text: str
    metadata: dict[str, object]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _floor_to_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def _get_earliest_timestamp(
    connection: psycopg.Connection,
    table_name: str = RAW_DATA_TABLE,
    timestamp_column_name: str = "ts",
    device_id: Optional[str] = None,
) -> Optional[datetime]:
    if device_id is None:
        query = sql.SQL(
            """
            select min({timestamp_column_name})
            from {table_name};
        """
        ).format(
            timestamp_column_name=sql.Identifier(timestamp_column_name),
            table_name=sql.Identifier(table_name),
        )
        params = None
    else:
        query = sql.SQL(
            """
            select min({timestamp_column_name})
            from {table_name}
            where device_id = %(device_id)s;
        """
        ).format(
            timestamp_column_name=sql.Identifier(timestamp_column_name),
            table_name=sql.Identifier(table_name),
        )
        params = {"device_id": device_id}

    with connection.cursor() as cursor:
        cursor.execute(query, params)
        fetched = cursor.fetchone()
        earliest = fetched[0] if fetched is not None else None

    if earliest is None:
        return None
    if earliest.tzinfo is None:
        earliest = earliest.replace(tzinfo=timezone.utc)
    else:
        earliest = earliest.astimezone(timezone.utc)
    return earliest


def _define_hour_windows_from_start(
    device_id: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
) -> list[SnapshotWindow]:
    if start_time:
        window_start = _floor_to_hour(start_time)
    else:
        connection = psycopg.connect(DATABASE_URL)
        earliest = _get_earliest_timestamp(
            connection=connection,
            timestamp_column_name="created_at",
        )
        if earliest is None:
            connection.close()
            return []
        window_start = _floor_to_hour(earliest)
        connection.close()

    window_end = _floor_to_hour(end_time or _utc_now())
    windows: list[SnapshotWindow] = []
    current_dt = window_start
    while current_dt < window_end:
        windows.append(
            SnapshotWindow(
                device_id=device_id,
                window_start=current_dt,
                window_end=current_dt + timedelta(hours=1),
            )
        )
        current_dt += timedelta(hours=1)

    return windows


def _define_hour_windows_from_end(
    device_id: str,
    lookback_hours: int,
    end_time: Optional[datetime] = None,
) -> list[SnapshotWindow]:
    window_end = _floor_to_hour(end_time or _utc_now())
    window_start = window_end - timedelta(hours=lookback_hours)

    print(f"Defining hour windows looking back {lookback_hours} hours from {window_end}...")

    windows: list[SnapshotWindow] = []
    current_dt = window_start
    while current_dt < window_end:
        snapshot = SnapshotWindow(
            device_id=device_id,
            window_start=current_dt,
            window_end=current_dt + timedelta(hours=1),
        )
        windows.append(snapshot)
        print(
            f"{snapshot.device_id}: {snapshot.window_start.isoformat()} to {snapshot.window_end.isoformat()}"
        )
        current_dt += timedelta(hours=1)
    return windows


def _fetch_hour_stats(
    connection: psycopg.Connection,
    window: SnapshotWindow,
    timestamp_column_name: str = "ts",
    temperature_c_column_name: str = "temp_c",
    temperature_f_column_name: str = "temp_f",
    humidity_column_name: str = "rh",
) -> Optional[HourlySnapshot]:
    query = sql.SQL(
        """
        select
          count(*) as n,
          min({temperature_c_column_name}) as t_min_c,
          max({temperature_c_column_name}) as t_max_c,
          avg({temperature_c_column_name}) as t_avg_c,
          min({temperature_f_column_name}) as t_min_f,
          max({temperature_f_column_name}) as t_max_f,
          avg({temperature_f_column_name}) as t_avg_f,
          min({humidity_column_name}) as h_min,
          max({humidity_column_name}) as h_max,
          avg({humidity_column_name}) as h_avg
        from {raw_data_table}
        where device_id = %(device_id)s
          and {timestamp_column_name} >= %(start)s
          and {timestamp_column_name} <  %(end)s;
    """
    ).format(
        temperature_c_column_name=sql.Identifier(temperature_c_column_name),
        temperature_f_column_name=sql.Identifier(temperature_f_column_name),
        humidity_column_name=sql.Identifier(humidity_column_name),
        raw_data_table=sql.Identifier(RAW_DATA_TABLE),
        timestamp_column_name=sql.Identifier(timestamp_column_name),
    )

    params = {
        "device_id": window.device_id,
        "start": window.window_start,
        "end": window.window_end,
    }

    cursor = connection.cursor()
    cursor.execute(query, params)
    row = cursor.fetchone()
    cursor.close()

    if row is None:
        return None
    count = int(row[0])
    if count == 0:
        return None

    return HourlySnapshot(
        device_id=window.device_id,
        window_start=window.window_start,
        window_end=window.window_end,
        n=int(count),
        **{key: float(value) if value is not None else None
           for key, value in zip(
               ("t_min_c", "t_max_c", "t_avg_c", "t_min_f", "t_max_f", "t_avg_f", "h_min", "h_max", "h_avg"),
               row[1:10],
           )},
    )


def _fetch_event_buckets(connection, device_id, start, end) -> list[Bucket]:
    query = sql.SQL("""
        select date_bin(interval '5 minutes', ts, timestamptz '2000-01-01 00:00:00+00') as bucket,
               avg(temp_c), avg(rh), count(temp_c), count(rh)
        from {table}
        where device_id = %(device_id)s and ts >= %(start)s and ts < %(end)s
        group by bucket order by bucket
    """).format(table=sql.Identifier(RAW_DATA_TABLE))
    with connection.cursor() as cursor:
        cursor.execute(query, {"device_id": device_id, "start": start, "end": end})
        return [Bucket(t, float(tc) if tc is not None else None,
                       float(rh) if rh is not None else None, nt, nh)
                for t, tc, rh, nt, nh in cursor.fetchall()]


def _format_snapshot_text(window: SnapshotWindow, stats: HourlySnapshot, event_text: str = "") -> str:
    def fmt(value):
        return f"{value:.2f}" if value is not None else "unavailable"

    return (
        f"Hourly snapshot for device '{window.device_id}'\n"
        f"Window (UTC): {window.window_start.isoformat()} → {window.window_end.isoformat()}\n"
        f"Samples: {stats.n}\n"
        f"Data coverage: {((stats.n * 100) / 1800):.2f}%\n"
        f"Temperature °C: avg={fmt(stats.t_avg_c)}, "
        f"min={fmt(stats.t_min_c)}, max={fmt(stats.t_max_c)}\n"
        f"Humidity %RH: avg={fmt(stats.h_avg)}, "
        f"min={fmt(stats.h_min)}, max={fmt(stats.h_max)}\n"
        f"{event_text}"
    )


def build_snapshot_records(
    lookback_hours: Optional[int] = None,
    end_time: Optional[datetime] = None,
    start_time: Optional[datetime] = None,
    source: str = "readings",
    node_id: str | None = None,
    db_url: str = DATABASE_URL,
) -> list[SnapshotRecord]:
    if source == "signal_buckets":
        from app.retrieval.vector.electric_sea_snapshots import build_electric_sea_snapshot_records
        return build_electric_sea_snapshot_records(lookback_hours=lookback_hours, start_time=start_time,
            end_time=end_time, node_id=node_id, db_url=db_url)
    if source != "readings":
        raise ValueError("Unsupported snapshot source")
    connection = psycopg.connect(DATABASE_URL)
    records: list[SnapshotRecord] = []

    if lookback_hours:
        hours = _define_hour_windows_from_end(
            DEVICE_ID,
            lookback_hours=lookback_hours,
            end_time=end_time,
        )
    else:
        hours = _define_hour_windows_from_start(
            DEVICE_ID,
            start_time=start_time,
            end_time=end_time,
        )

    try:
        settings = EventSettings(
            timezone=os.getenv("SNAPSHOT_EVENT_TIMEZONE", "America/Los_Angeles"),
            temperature_delta_c=float(os.getenv("SNAPSHOT_EVENT_TEMPERATURE_DELTA_C", "2")),
            humidity_delta_pp=float(os.getenv("SNAPSHOT_EVENT_HUMIDITY_DELTA_PP", "5")),
            sample_period_seconds=float(os.getenv("SNAPSHOT_EVENT_SAMPLE_PERIOD_SECONDS", "2")),
        )
        # Bound history queries/memory to one day of output plus its baseline context.
        buckets: list[Bucket] = []
        loaded_until = None
        for hour in hours:
            if loaded_until is None or hour.window_start >= loaded_until:
                loaded_until = min(hour.window_start + timedelta(days=1), hours[-1].window_end)
                buckets = _fetch_event_buckets(
                    connection, hour.device_id, hour.window_start - timedelta(days=30),
                    loaded_until + timedelta(hours=1),
                )
            stats = _fetch_hour_stats(connection, hour)
            if stats is None:
                continue

            snapshot_text = _format_snapshot_text(
                hour, stats, describe_events(buckets, hour.window_start, hour.window_end, settings)
            )

            records.append(
                SnapshotRecord(
                    text=snapshot_text,
                    metadata={
                        "window_start": hour.window_start.isoformat(),
                        "window_end": hour.window_end.isoformat(),
                        "kind": "hourly_snapshot",
                        "device_id": DEVICE_ID,
                        "source": RAW_DATA_TABLE,
                        "data_coverage": stats.n / 1800,
                        "event_analysis_version": 1,
                    },
                )
            )
    finally:
        connection.close()

    return records


def fetch_snapshots_between(
    start_utc: datetime,
    end_utc: datetime,
    device_id: str = DEVICE_ID,
    table: str = os.getenv("SNAPSHOT_DATA_TABLE", ""),
) -> list[SnapshotRecord]:
    query = sql.SQL(
        """
      select window_start, window_end, snapshot_text
      from {table}
      where device_id = %s
        and window_start >= %s
        and window_end <= %s
      order by window_start asc;
    """
    ).format(table=sql.Identifier(table))
    records: list[SnapshotRecord] = []
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (device_id, start_utc, end_utc))
            for window_start, window_end, snapshot_text in cur.fetchall():
                records.append(
                    SnapshotRecord(
                        text=snapshot_text,
                        metadata={
                            "kind": "hourly_snapshot",
                            "device_id": device_id,
                            "window_start": window_start.isoformat(),
                            "window_end": window_end.isoformat(),
                            "source": table,
                        },
                    )
                )
    return records
