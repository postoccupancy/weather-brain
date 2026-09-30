# Electric Sea hourly snapshots

The existing snapshot entry point now accepts `source="signal_buckets"`. It
delegates Electric Sea SQL/formatting to `electric_sea_snapshots.py` and reuses
`SnapshotRecord`, `snapshot_events.describe_events`, the configured Ollama
embedding model, the existing `snapshots` archive, and the existing LangChain
PostgreSQL collection. No schema migration or new vector database is needed.
The original readings-based builder and its indexers remain available unchanged.

## Document and statistics

One document represents one node and completed UTC hour. Each deployment ID
present in that hour has a separate section; units and signals are also kept
separate. This preserves the existing archive's `(device_id, window_start,
window_end)` uniqueness while preventing different locations from being pooled.
The node is stored in the archive's existing `device_id` field. Each section
includes location, deployment interval, optional notes, and statistics for every
signal. Vector metadata retains the structured deployment and signal statistics;
the archive retains their human-readable text.

For source summaries with count `n`, mean `m` and population stddev `s`, totals are
`N=sum(n)`, `M=sum(n*m)/N`, extrema of extrema, and population variance
`sum(n*(s*s+(m-M)^2))/N`. Original observation count and stored-second coverage
are separate. No expected high-rate cadence is assumed. Coverage is distinct
stored seconds divided by 3600, **per deployment/signal/unit section**, even for
a deployment occupying only part of the hour. It is not a claim about sample
loss or sensor uptime. Gaps are not filled. Incomplete summaries are excluded.
No unit conversions or energy-domain reinterpretation of dB values are performed.

The existing detector receives weighted five-minute means and counts of stored
seconds (300 expected per bucket). Its 80% coverage gate, two-bucket persistence,
physical floors and robust historical baseline remain in use. Events currently
cover exactly `temperature/celsius` and `humidity/percent`. Other signal statistics
are included but not assessed for events. Baselines and boundary evidence are
restricted to the same node, deployment ID, signal and unit. Unassigned records
may support rapid changes but are not used as historical baselines. Configured
`SNAPSHOT_EVENT_TIMEZONE` applies to hour-of-day comparisons; it defaults to
America/Los_Angeles and is not inferred from coordinates.

History is queried in daily output batches with the preceding 30 days and one
following hour of context. Missing future context may leave event continuation
unresolved until a later rebuild. Thresholds and available evidence remain in
each snapshot. No comfort or causal claims are added.

## Scheduling and manual runs

`SNAPSHOT_SOURCE=signal_buckets` is the scheduler default. Set it to `readings` to
retain legacy scheduling. Each hourly iteration rebuilds the latest three
completed hours for every node represented in that interval. A 30-second grace
period precedes selection of the hour boundary. This revisits late records and
event continuation; arrivals or deployment backfills older than that window need
an explicit rebuild. Scheduler errors are logged and retried next iteration.
Restart the API to load the new scheduler; this implementation task did not
restart it or run a bulk historical rebuild.

The protected existing `/rag/index?source=signal_buckets` endpoint indexes one
recent completed hour. Existing HTTP requests default to `source=readings` for
backward compatibility. Electric Sea uses the existing LangChain collection;
requesting LlamaIndex is rejected rather than creating its missing snapshot table.
`/rag/rebuild?source=signal_buckets` is rejected: use explicit CLI bounds instead.

Preview a recent hour without writing to the database:

```powershell
.venv/Scripts/python.exe scripts/electric_sea_snapshots.py --node-id electric-sky --lookback-hours 1 --output scratch/snapshot-preview.json
```

Preview an explicit range (bounds are floored to UTC hours), then repeat with
`--apply` to generate embeddings and store it:

```powershell
.venv/Scripts/python.exe scripts/electric_sea_snapshots.py --node-id electric-sky --start 2026-09-30T14:00:00Z --end 2026-09-30T15:00:00Z --output scratch/snapshot-preview.json --apply
```

Omit `--node-id` to include all reporting nodes in the selected range. Default
lookback is three hours; implicit lookback must be 1..168 hours. Both explicit
start and end are required for historical CLI ranges. No implicit all-time
Electric Sea rebuild occurs. Use modest explicit ranges for large backfills.

## Persistence and verification

Electric Sea vector IDs use a source-prefixed hash of node and UTC hour, independent
of deployment assignment and algorithm version. Rebuilding replaces the document,
metadata and embedding, including any revised deployment sections. Embeddings
are generated and dimension/finite-value checked before writes. Under a writer
advisory lock, archive and vector upserts occur in the same PostgreSQL transaction;
failure rolls both back. The writer refuses to replace a legacy archive row or a
vector ID in another collection. No legacy vectors are deleted.

Before this change, inspection found 498 archived windows and 703 legacy vector
documents: 205 windows had two vectors each. Every archive window had a vector,
and every vector mapped to an archive window. Duplicate cleanup is deliberately
outside this task; the cause was not proven.

Live validation wrote only electric-sky's 2026-09-30 14:00-15:00 UTC hour, then
repeated the write. The saved document in `scratch/electric-sea-snapshot-validation.json`
matches both the archive text and vector text/metadata. Exactly one new archive
row and one 768-dimensional vector remain (totals 499 and 704 respectively).
It contains Home Office, eight signals, 93.81% stored-second coverage for
temperature/RH, and explicitly unavailable historical baselines.

Tests use temporary PostgreSQL tables, fake embeddings, and existing event tests
to cover unequal counts/pooled variance, gaps, multiple nodes/deployments/units,
baseline isolation, late-data replacement, repeated writes, and rollback on
vector failure. Set `TEST_DATABASE_URL` to enable the SQL integration tests.
