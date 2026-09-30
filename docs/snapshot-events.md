# Hourly snapshot events

The existing readings-based snapshot builder now appends deterministic event
descriptions to every nonempty hourly document. Existing aggregate statistics,
archive upserts, and vector indexing remain in use. No esp32_ui code is changed.
Electric Sea signal_buckets integration and psychrometric/comfort models are
separate follow-ups; this change does not infer signal IDs, units, or cadence.

## Detection

- Five-minute means are loaded in daily batches with 30 days of historical data
  and one hour of boundary context on either side of the output hour.
- Each metric requires 80% of its expected sample count per bucket. Missing or
  insufficiently covered buckets break sustained episodes and rapid comparisons.
- Baselines use the same local hour on historical dates, excluding the output
  hour and preceding context hour. At least 18 valid buckets over 3 dates are
  required. Median and scaled MAD determine a threshold with a physical floor.
- Sustained high/low episodes enter after two consecutive buckets exceed
  max(physical floor, 3 * 1.4826 * MAD). They end at half that threshold, a
  direction reversal, or missing data/baseline. Short unconfirmed excursions
  are not sustained events.
- Rapid rise/fall observations compare adjacent valid five-minute means against
  the physical floor without requiring historical data. They may overlap a
  sustained episode. Different signals retain separate evidence and intervals;
  observation counts are not counts of independent physical causes.
- Episode intervals and peak evidence are clipped to the snapshot hour. Boundary
  context identifies continuation; unavailable context leaves the ending
  unresolved. Timing is accurate only to bucket resolution. A later rebuild
  can resolve continuation and late-arriving readings.
- RH is observed RH, not the dashboard's linear temperature-adjusted heuristic.
  No severity, comfort, or causal claims are made. Quiet results explicitly
  qualify coverage and baseline availability.

## Configuration

These are initial engineering settings, not calibrated comfort limits:

| Environment variable | Default |
| --- | --- |
| SNAPSHOT_EVENT_TIMEZONE | America/Los_Angeles |
| SNAPSHOT_EVENT_TEMPERATURE_DELTA_C | 2 |
| SNAPSHOT_EVENT_HUMIDITY_DELTA_PP | 5 |
| SNAPSHOT_EVENT_SAMPLE_PERIOD_SECONDS | 2 |

Threshold and sampling-period settings must be finite and positive. The time
zone must be a valid IANA name. The original aggregate coverage field retains
its existing two-second sampling assumption; event coverage is per metric and
uses the configured period.

Snapshots include algorithm version, baseline method, thresholds, coverage,
and event evidence in their text, so archive retrieval retains the explanation.
Already-stored snapshots require rebuilding to receive these additions.
No database migration, live reindex, or historical rewrite is performed by this
code change. Validate the starting thresholds against recorded quiet periods
and labeled episodes before interpreting detector sensitivity operationally.
