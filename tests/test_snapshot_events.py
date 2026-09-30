from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.retrieval.vector.snapshot_events import Bucket, EventSettings, STEP, describe_events
from app.retrieval.vector import snapshots

START = datetime(2026, 9, 30, 14, tzinfo=timezone.utc)
END = START + timedelta(hours=1)
SETTINGS = EventSettings(timezone="UTC")


def bucket(t, temperature=20, humidity=50, count=150):
    return Bucket(t, temperature, humidity, count, count)


def history():
    return [bucket(START - timedelta(days=d, hours=1) + i * STEP)
            for d in range(1, 5) for i in range(36)]


def current(temperatures=None):
    values = temperatures or [20] * 12
    return [bucket(START + i * STEP, t) for i, t in enumerate(values)]


def describe(rows):
    return describe_events(rows, START, END, SETTINGS)


def test_quiet_hour_and_original_aggregates():
    text = describe(history() + current())
    assert "No events detected" in text
    assert "12/12 baseline-assessable" in text
    stats = snapshots.HourlySnapshot("test", START, END, 1800, 20, 20, 20, 68, 68, 68, 50, 50, 50)
    document = snapshots._format_snapshot_text(snapshots.SnapshotWindow("test", START, END), stats, text)
    assert "Temperature °C: avg=20.00" in document
    assert "Event analysis v1" in document


def test_multiple_events_and_direction_reversal():
    text = describe(history() + current([20, 23, 23, 20, 20, 17, 17, 20, 20, 20, 20, 20]))
    assert text.count("sustained Temperature high") == 1
    assert text.count("sustained Temperature low") == 1
    assert "peak departure +3.00 C" in text
    assert "rapid Temperature fall" in text


def test_persistence_hysteresis_and_recovery():
    text = describe(history() + current([23, 23, 21.5, 21.2, 20.5, 20, 20, 20, 20, 20, 20, 20]))
    assert "20 minutes in hour" in text
    assert "returned within exit band" in text
    text = describe(history() + current([23, 21.5, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20]))
    assert "sustained Temperature" not in text


def test_gap_breaks_persistence():
    rows = current([23, 23, 23, 20, 20, 20, 20, 20, 20, 20, 20, 20])
    del rows[1]
    text = describe(history() + rows)
    assert "sustained Temperature" not in text
    assert "11/12 sufficiently covered" in text


def test_missing_history_still_detects_rapid_change():
    text = describe(current([20, 24] + [24] * 10))
    assert "0/12 baseline-assessable" in text
    assert "rapid Temperature rise" in text
    assert "sustained Temperature" not in text


def test_future_data_cannot_change_baseline():
    rows = history() + current([23] * 12)
    text = describe(rows + [bucket(END + i * STEP, 90) for i in range(12)])
    assert "median=20.00 C" in text
    assert "baseline 20.00" in text


def test_cross_hour_event_is_clipped_and_annotated():
    rows = history() + [bucket(START - STEP, 23)] + current([23] * 12) + [bucket(END, 23), bucket(END + STEP)]
    text = describe(rows)
    assert "60 minutes in hour" in text
    assert "continues from previous hour" in text
    assert "continues beyond this hour" in text


def test_per_metric_coverage_and_nonfinite_values():
    rows = history() + [Bucket(START + i * STEP, 23, float("nan"), 150, 0) for i in range(12)]
    text = describe(rows)
    assert "sustained Temperature high" in text
    assert "RH: 0/12 sufficiently covered" in text
    assert "sustained RH" not in text


def test_config_rejects_invalid_floors():
    with pytest.raises(ValueError):
        EventSettings(temperature_delta_c=0)


def test_variable_history_raises_entry_threshold():
    rows = [bucket(b.start, 16 if i % 2 else 24) for i, b in enumerate(history())]
    text = describe(rows + current([23] * 12))
    assert "entry deviation=17.79 C" in text
    assert "sustained Temperature" not in text


def test_humidity_uses_percentage_points_and_signals_are_independent():
    rows = history() + [bucket(START + i * STEP, 20, 56) for i in range(12)]
    text = describe(rows)
    assert "sustained RH high" in text
    assert "peak departure +6.00 percentage points" in text
    assert "sustained Temperature" not in text


def test_exact_entry_threshold_is_not_a_sustained_breach():
    assert "sustained Temperature" not in describe(history() + current([22] * 12))


def test_sparse_or_short_history_is_not_sufficient():
    text = describe(history()[:12] + current([23] * 12))
    assert "0/12 baseline-assessable" in text
    assert "sustained Temperature" not in text


def test_timezone_is_used_for_historical_hour_matching():
    text = describe_events(history() + current([23] * 12), START, END,
                           EventSettings(timezone="America/Los_Angeles"))
    assert "Temperature local hour 07" in text
    assert "sustained Temperature high" in text


def test_missing_metric_hour_stats_can_be_rendered():
    connection = MagicMock()
    connection.cursor.return_value.fetchone.return_value = (150, 20, 20, 20, 68, 68, 68, None, None, None)
    window = snapshots.SnapshotWindow("test", START, END)
    stats = snapshots._fetch_hour_stats(connection, window)
    assert "Humidity %RH: avg=unavailable" in snapshots._format_snapshot_text(window, stats)


def test_bucket_query_is_device_and_time_scoped():
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [(START, 20, None, 150, 0)]
    rows = snapshots._fetch_event_buckets(connection, "node-a", START, END)
    assert rows == [Bucket(START, 20, None, 150, 0)]
    assert cursor.execute.call_args.args[1] == {"device_id": "node-a", "start": START, "end": END}


def test_builder_embeds_events_in_persistable_document(monkeypatch):
    connection = MagicMock()
    monkeypatch.setattr(snapshots.psycopg, "connect", lambda _: connection)
    monkeypatch.setattr(snapshots, "DEVICE_ID", "test")
    fetch = MagicMock(return_value=history() + current([23] * 12))
    monkeypatch.setattr(snapshots, "_fetch_event_buckets", fetch)
    stats = snapshots.HourlySnapshot("test", START, END, 1800, 23, 23, 23, 73.4, 73.4, 73.4, 50, 50, 50)
    monkeypatch.setattr(snapshots, "_fetch_hour_stats", lambda *_: stats)
    monkeypatch.setenv("SNAPSHOT_EVENT_TIMEZONE", "UTC")
    records = snapshots.build_snapshot_records(lookback_hours=1, end_time=END)
    assert len(records) == 1
    assert "sustained Temperature high" in records[0].text
    assert records[0].metadata["event_analysis_version"] == 1
    assert fetch.call_args.args[2] == START - timedelta(days=30)
    connection.close.assert_called_once()
