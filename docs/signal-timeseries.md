# Electric Sea time-series queries

`GET /timeseries?table=signal_buckets&device_id=electric-sky&signal_id=temperature&bucket=60`
uses the existing status-token authentication. `device_id` selects a node ID;
`signal_id` selects its signal. Both are required for this table. Existing requests
default to legacy `readings` and retain their behavior. `/timeseries/summary` is unchanged.

`start_ts` and `end_ts` filter stored bucket start timestamps inclusively, as in
the legacy API. `bucket` is a positive integer number of seconds: 1, 60, 600,
900, 3600, etc. Intervals align to the UTC Unix epoch. Filtering precedes
aggregation; partial intervals include only selected source buckets. `limit`,
`offset`, and `order_desc` apply to output intervals.

Without `bucket`, the `signal_buckets` response array contains stored summaries,
plus `ts`, `device_id`, and a signal-named mean alias (unless the name conflicts
with an existing row field). With `bucket`, the existing `aggregates` envelope
contains `bucket_start`, `bucket_end`, `first_ts`, `last_ts`, node/signal IDs,
`mean`, `min`, `max`, `stddev`, `sample_count`, `count`, `bucket_count`, and `unit`.
`count` aliases source sample count; `bucket_count` counts stored seconds.
Signal aliases such as `temperature_avg`, `temperature_min`, `temperature_max`,
and `temperature_stddev` follow the legacy metric-field convention. Clients
must select that field; no temperature conversion or legacy metric mapping is inferred.
`aggregate_mode=lite` omits extrema and standard deviation fields, retaining
weighted means and counts. Unit is null if unknown or inconsistent; a producer
must use a consistent unit for each node/signal identity.

## Producer statistics convention

Each stored second must use **population standard deviation**:
`stddev = sqrt(sum((x - mean)^2) / n)`, including zero for a single sample.
Do not use the sample estimator with denominator `n - 1`.

For stored summaries `(n_i, mean_i, stddev_i)`, query-time aggregation computes:

- `N = sum(n_i)`
- `M = sum(n_i * mean_i) / N`
- `variance = sum(n_i * (stddev_i^2 + (mean_i - M)^2)) / N`
- `stddev = sqrt(variance)`, `min = min(min_i)`, `max = max(max_i)`

The centered two-pass formula includes within-second and between-second
variation. No fixed sample rate is assumed. Missing seconds are not filled;
empty intervals have no output row. The schema requires all summary statistics
and counts; aggregation defensively excludes incomplete summaries rather than
coalescing unknown values to zero. Optional metadata and unit may be null.
Results retain floating-point and original-summary precision limits.
