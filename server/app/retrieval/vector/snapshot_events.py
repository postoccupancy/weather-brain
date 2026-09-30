"""Small, deterministic event calculations used by the hourly snapshot builder."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from statistics import median
from zoneinfo import ZoneInfo

STEP = timedelta(minutes=5)


@dataclass(frozen=True)
class EventSettings:
    timezone: str = "America/Los_Angeles"
    temperature_delta_c: float = 2.0
    humidity_delta_pp: float = 5.0
    sample_period_seconds: float = 2.0

    def __post_init__(self):
        ZoneInfo(self.timezone)
        for value in (self.temperature_delta_c, self.humidity_delta_pp, self.sample_period_seconds):
            if not isfinite(value) or value <= 0:
                raise ValueError("Snapshot event thresholds and sample period must be positive and finite")


@dataclass(frozen=True)
class Bucket:
    start: datetime
    temperature: float | None
    humidity: float | None
    temperature_count: int
    humidity_count: int


def describe_events(
    buckets: list[Bucket], start: datetime, end: datetime, settings: EventSettings
) -> str:
    """Evaluate an hour with one hour of boundary context, never filling data gaps."""
    zone = ZoneInfo(settings.timezone)
    context_start, context_end = start - timedelta(hours=1), end + timedelta(hours=1)
    rows = {b.start: b for b in buckets}
    expected = 300 / settings.sample_period_seconds
    events: list[tuple[datetime, str]] = []
    quality: list[str] = []
    baselines: list[str] = []

    for metric, label, unit, floor in (
        ("temperature", "Temperature", "C", settings.temperature_delta_c),
        ("humidity", "RH", "percentage points", settings.humidity_delta_pp),
    ):
        def valid(b):
            value = getattr(b, metric)
            return value is not None and isfinite(value) and getattr(b, metric + "_count") / expected >= 0.8

        history: dict[int, list[Bucket]] = {}
        for b in buckets:
            if start - timedelta(days=30) <= b.start < context_start and valid(b):
                history.setdefault(b.start.astimezone(zone).hour, []).append(b)
        reference = {}
        for hour, samples in history.items():
            if len(samples) < 18 or len({b.start.astimezone(zone).date() for b in samples}) < 3:
                continue
            center = median(getattr(b, metric) for b in samples)
            mad = median(abs(getattr(b, metric) - center) for b in samples)
            reference[hour] = (center, max(floor, 3 * 1.4826 * mad))

        points = []
        t = context_start
        while t < context_end:
            b = rows.get(t)
            ref = reference.get(t.astimezone(zone).hour)
            points.append((t, getattr(b, metric) if b and valid(b) else None, ref))
            t += STEP
        usable = sum(start <= t < end and value is not None for t, value, _ in points)
        assessed = sum(start <= t < end and value is not None and ref is not None for t, value, ref in points)
        quality.append(f"{label}: {usable}/12 sufficiently covered buckets; {assessed}/12 baseline-assessable")
        for hour in sorted({t.astimezone(zone).hour for t, _, _ in points if start <= t < end}):
            ref = reference.get(hour)
            if ref:
                baselines.append(f"{label} local hour {hour:02}: median={ref[0]:.2f} {'C' if metric == 'temperature' else '%RH'}, entry deviation={ref[1]:.2f} {unit}")

        # Each signal/direction retains its own interval and peak evidence.
        intervals = []
        active = []
        direction = 0

        def flush(reason):
            nonlocal active, direction
            if len(active) >= 2:
                intervals.append((active, direction, reason))
            active, direction = [], 0

        for t, value, ref in points:
            if value is None or ref is None:
                flush("data or baseline unavailable")
                continue
            center, threshold = ref
            delta = value - center
            sign = 1 if delta > 0 else -1
            if active:
                if sign != direction:
                    flush("direction reversed")
                elif abs(delta) <= threshold * 0.5:
                    flush("returned within exit band")
                elif len(active) == 1 and abs(delta) <= threshold:
                    flush("entry not persistent")
                else:
                    active.append((t, value, center, delta))
                    continue
            if abs(delta) > threshold:
                active = [(t, value, center, delta)]
                direction = sign
        flush("continuation unresolved at context boundary")

        for episode, sign, reason in intervals:
            first, last = episode[0][0], episode[-1][0] + STEP
            if first >= end or last <= start:
                continue
            inside = [p for p in episode if start <= p[0] < end]
            peak = max(inside, key=lambda p: abs(p[3]))
            a, z = max(first, start), min(last, end)
            boundary = "; continues from previous hour" if first < start else ""
            if last > end:
                boundary += "; continues beyond this hour"
            if first == context_start:
                boundary += "; onset predates or equals available context"
            events.append((a, f"{a.isoformat()} to {z.isoformat()}: sustained {label} {'high' if sign > 0 else 'low'}; "
                           f"{int((z-a).total_seconds()/60)} minutes in hour; peak departure {peak[3]:+.2f} {unit} "
                           f"at {peak[0].isoformat()} (observed {peak[1]:.2f}, baseline {peak[2]:.2f}); "
                           f"{reason}{boundary}."))

        # Rapid changes need no historical baseline; isolated large steps remain visible.
        for previous, current in zip(points, points[1:]):
            t, value, _ = current
            pt, pv, _ = previous
            if value is None or pv is None or not start <= t < end:
                continue
            change = value - pv
            if abs(change) < floor:
                continue
            events.append((t, f"{t.isoformat()}: rapid {label} {'rise' if change > 0 else 'fall'}; "
                           f"{change:+.2f} {unit} between consecutive five-minute means "
                           f"({pv:.2f} to {value:.2f}); may overlap a sustained episode."))

    lines = ["Event analysis v1: five-minute means; timing is bucket-aligned; no comfort or causal assessment.",
             f"Baseline: prior 30 days, excluding this hour and preceding context hour; same local hour in {settings.timezone}; "
             "median +/- max(physical floor, 3 * scaled MAD); minimum 18 buckets across 3 dates.",
             f"Physical floors: temperature {settings.temperature_delta_c:g} C; RH {settings.humidity_delta_pp:g} percentage points. "
             "Sustained entry: 2 consecutive breached buckets; exit: 50% of entry deviation. "
             f"Coverage gate: 80%; expected sample period {settings.sample_period_seconds:g} seconds.",
             *baselines, *quality]
    if events:
        lines.append(f"Notable event observations: {len(events)} (signals and rapid changes may overlap).")
        lines.extend("- " + text for _, text in sorted(events))
    else:
        lines.append("No events detected in assessable observations; insufficient coverage or history does not establish a quiet hour.")
    return "\n".join(lines) + "\n"
