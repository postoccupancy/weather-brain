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
    t_min_c: float
    t_max_c: float
    t_avg_c: float
    t_min_f: float
    t_max_f: float
    t_avg_f: float
    h_min: float
    h_max: float
    h_avg: float


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
        t_min_c=float(row[1]),
        t_max_c=float(row[2]),
        t_avg_c=float(row[3]),
        t_min_f=float(row[4]),
        t_max_f=float(row[5]),
        t_avg_f=float(row[6]),
        h_min=float(row[7]),
        h_max=float(row[8]),
        h_avg=float(row[9]),
    )


def _format_snapshot_text(window: SnapshotWindow, stats: HourlySnapshot) -> str:
    return (
        f"Hourly snapshot for device '{window.device_id}'\n"
        f"Window (UTC): {window.window_start.isoformat()} → {window.window_end.isoformat()}\n"
        f"Samples: {stats.n}\n"
        f"Data coverage: {((stats.n * 100) / 1800):.2f}%\n"
        f"Temperature °C: avg={stats.t_avg_c:.2f}, "
        f"min={stats.t_min_c:.2f}, max={stats.t_max_c:.2f}\n"
        f"Humidity %RH: avg={stats.h_avg:.2f}, "
        f"min={stats.h_min:.2f}, max={stats.h_max:.2f}\n"
    )


def build_snapshot_records(
    lookback_hours: Optional[int] = None,
    end_time: Optional[datetime] = None,
    start_time: Optional[datetime] = None,
) -> list[SnapshotRecord]:
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
        for hour in hours:
            stats = _fetch_hour_stats(connection, hour)
            if stats is None:
                continue

            snapshot_text = _format_snapshot_text(hour, stats)

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
