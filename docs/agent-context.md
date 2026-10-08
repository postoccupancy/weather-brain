# Agent Context

## Last Updated
- 2026-09-30

## Hourly Snapshot Event Review
- Pressure assessment: verified indoor-sky firmware emits local BME pressure in hPa alongside temperature/RH and Electric Sea preserves aligned pressure samples. Pressure is useful for humidity ratio, wet-bulb, density, and enthalpy derivations; recommend hourly context/trend before standalone events. Current readings snapshot detector still uses only temperature/RH. Persisted pressure completeness/calibration not verified; psychrometric use needs local absolute pressure converted to Pa, not sea-level-reduced pressure. No code changes for this assessment.
- Resolved the rebase onto 7f9eea7 by retaining the newer update date and snapshot notes alongside upstream context; application code had no conflicts.
- User requested committing the completed snapshot changes, tests, and documentation. Commit prepared from parent 027f5fb; no push requested. Validation remains 17 focused tests passing and the documented pre-existing full-suite auth failure. Live example remains blocked by the unavailable database.
- Latest-hour example attempt: configured PostgreSQL endpoint localhost:5432 refused connections even after an approved sandbox escalation for a read-only query. No current Electric Sky data could be fetched, and no example was fabricated. Requires the database service/tunnel to be reachable or a corrected DATABASE_URL; no database writes or service startup performed.
- Implemented corrected detection in the existing readings-based snapshot builder (2026-09-30). `snapshot_events.py` is a small deterministic helper; snapshot text now includes five-minute sustained high/low and rapid-change observations, prior-history median/MAD baselines, physical-unit floors, persistence/hysteresis, per-metric coverage, and cross-hour evidence. Daily bounded history queries feed the helper. Missing aggregate metrics render as unavailable instead of crashing.
- Configuration and limitations: `docs/snapshot-events.md`. Defaults are provisional 2 C / 5 RH percentage points, 80% bucket coverage, two-bucket persistence, 30-day same-local-hour history with at least 18 buckets over three dates. Rapid observations may overlap sustained episodes; metric observations remain separate. No comfort/causal claims or linear expected-RH heuristic.
- Validation: 17 focused tests passed; full suite before seven additional focused cases: 151 passed, one documented pre-existing missing-token auth failure. Diff whitespace check passed. No live DB/reindex, migration, commit, or esp32_ui changes. Electric Sea source adaptation and psychrometrics remain future work; existing stored snapshots require rebuilding.
- Inspected esp32_ui dashboard, alerts, and build-threshold-events.ts for the requested snapshot enrichment; implementation is pending the user's ongoing scope discussion.
- Existing event logic groups consecutive breached buckets across temperature, temperature-adjusted RH, and absolute humidity; records peak deviations, timing, settings, and completeness. Alerts currently select hour-of-day means; the older baseline documentation describes window means.
- Next: adapt this logic directly into the snapshot builder to retain aggregates and describe zero or more events per hour. No snapshot code or stored data changed during this review.
- Electric Sea follow-up: signal_buckets stores one scalar signal per node/time with mean/min/max/stddev/count and optional unit/metadata/deployment. Its contract does not require bucket duration, expected sampling rate, timezone, or canonical signal IDs. Snapshot adaptation needs explicit signal/unit mapping, cadence metadata, weighted rollups, aligned temperature/RH pairs, deployment-aware baselines, and node/source identity in the existing archive/index path. Description only; no implementation changes.
- Comfort/DSP assessment: researched CBE, pythermalcomfort, PsychroLib, SciPy, NIST CUSUM, and ruptures documentation. Recommend retaining event grouping/evidence but replacing percent-of-Fahrenheit thresholds and linear expected-RH heuristic with physical-unit/robust residual thresholds and psychrometrics. Distinguish anomaly, physical interpretation, and comfort applicability in snapshot text; add hysteresis/persistence and direction-aware grouping. Future comfort requires deployment context, outdoor daily history, and measured/explicitly assumed MRT/air speed/clothing/activity. Globe-to-MRT correction depends on air speed, diameter, and emissivity; account for installation heat and response lag. No analysis implementation changed.

## Workspace Scope
- Multi-root workspace covering `esp32_api`, `esp32_ui`, and `b2b-dashboard-demo`.
- Use this file for dynamic task/session context and handoff notes.
- Use workspace or repo settings for stable editor/runtime configuration.

## Current Objective
- Adapt hourly snapshots to Electric Sea and verify a bounded real archive/vector write. Implemented and live-validated; no bulk historical rebuild or service restart performed.

## Indoor USB batch-loss fix
- On 2026-10-07, live normalized-stream tracing found Indoor Sky packet gaps were primarily created by `SerialBatchPacer`: relative `setTimeout` delays accumulated event-loop latency until its 256-batch queue stayed full and silently discarded old batches. Electric Sky bypasses this USB presentation pacer.
- Electric Sea now schedules paced delivery against an absolute device-time timeline and exposes raw USB packet gaps plus pacer received/delivered/dropped/reset/depth counters in `/indoor-sky/status`. Local router suite: 97 passed. The scoped runtime files and deterministic pacer test were deployed to the Pi and `router.service` restarted.
- Initial deployed counters showed 897 received/897 delivered, zero pacer drops, maximum depth 20, current depth zero. A subsequent 20-second comparison showed no new raw USB gaps and no firmware scalar-send failures.
- Indoor firmware hardening in `device/src/main.cpp` gives scalar frames priority at the shared USB mutex, completes partial USB writes, and retries transient zero-byte writes for up to one second. After `device/secrets.py` became available, firmware build `2026-10-08T04:23:14Z` was built and installed by OTA. Electric Sea keeps one read/write TTY descriptor so PCM control commands neither discard buffered input nor toggle USB CDC.
- Final 20-second raw-PCM stress verification delivered 318 consecutive Indoor batches: zero WebSocket gaps, raw USB gaps, missing packets, firmware scalar-send failures, or pacer drops. Pacer depth returned to four and its observed maximum was 14.
- Existing unrelated Weather Brain context edits and Electric Sea archive diagnostics remain preserved. Pi's unrelated untracked `pi-setup/scripts/theremin-log` remains untouched.

## Indoor Sky history diagnosis
- Read-only database inspection on 2026-10-01 confirmed the five Indoor Sky snapshots correspond to all five hours that contain Indoor Sky source rows. The snapshot builder did not omit populated hours.
- `signal_buckets` contains only 800 Indoor Sky rows: exactly 200 one-second rows for each of temperature, humidity, pressure, and RMS. They occur in ten short runs between 2026-09-28 23:50:02 PDT and 2026-09-29 11:59:17 PDT; the longest run is 58 seconds. There are no later Indoor Sky rows. Electric Sky is continuous across the period and has over 184,000 rows per principal signal at inspection.
- Electric Sea code routes accepted Indoor Sky `sample_batch` messages through the same Weather Brain archive observer, so the evidence points upstream of snapshot generation: the router only received/accepted brief Indoor Sky scalar batches, or the deployed router/device path stopped. Runtime Pi logs/status are needed to distinguish device transport, frame rejection, or deployment-version/configuration causes. No database or code changes made during diagnosis.
- Follow-up live investigation confirmed Indoor Sky is healthy and the public router is broadcasting its scalar batches: firmware reported about 2.69 million USB scalar packets since boot, and public WebSocket observations saw 128 Indoor Sky batches in about 9 seconds and 269 in about 19 seconds. PostgreSQL remained stale, isolating the failure to the router archival adapter rather than device transport, dashboard delivery, Weather Brain ingestion, or snapshots.
- Root cause is the timestamp acceptance design: USB batches pass through `SerialBatchPacer` before `weatherBrainArchive.observe`; the pacer can compress queued/device-time gaps to at most 250 ms, while the archive fixes one device-to-wall offset and permanently rejects samples outside a strict +/-2-second wall-time window. Live device timestamps advanced about 0.6-0.75 seconds more than observed wall delivery in each measurement, so batches rapidly become future/late and are silently dropped (only aggregate warnings go to the Pi journal). Electric Sky's different transport/timing remains inside the window.
- After the user configured SSH, Pi inspection confirmed the diagnosis. `router.service` is active from `/home/pi/electric-sea/router`, running commit 1a0d8c9 since 2026-09-29 11:59:03 PDT with both Weather Brain settings present. Deployed source matches the local pacer-then-archive path. The journal continuously records about 518-555 `late/future sample` drops per second, matching Indoor Sky's approximately 550 scalar samples/second; it also contains occasional separate Indoor USB frame-window rejections. The database accepted only 11 seconds after that service start (11:59:07-11:59:17), then stopped, consistent with the fixed +/-2-second clock mapping rapidly diverging after pacing.
- A durable fix should observe USB batches before presentation pacing and make clock mapping tolerate/reconcile long-running device drift without reopening finalized seconds; add regression coverage for backlog compression and drift. No Electric Sea code or live service changed in this diagnostic turn. The Pi worktree has one unrelated untracked `pi-setup/scripts/theremin-log`, which must be preserved.

## Indoor Sky archival fix deployed
- On 2026-10-01, changed Electric Sea so raw Indoor USB scalar batches enter Weather Brain archival before `SerialBatchPacer`; the paced dashboard copy explicitly skips archival to prevent duplicates. USB clock mapping now corrects drift toward local arrival time by at most 50 ms per packet while preserving intra-batch sample spacing and finalized-second protection. Documentation and focused regressions cover gradual drift and pre-pacer exactly-once routing.
- Local and deployed Pi router suites both passed all 94 tests; syntax and diff whitespace checks passed. Deployed four scoped files to `/home/pi/electric-sea/router` and restarted `router.service` at 10:04:05 PDT. The unrelated untracked `pi-setup/scripts/theremin-log` remains untouched.
- Live verification exceeded the old 11-second failure point: PostgreSQL received 78 consecutive one-second rows per Indoor Sky signal from 10:04:10 through 10:05:27 PDT, with 7,755 temperature/humidity/pressure source samples and 19,391 RMS samples. The public router delivered 332 Indoor batches during a 25-second check. No post-restart archive drop or HTTP failure logs were present.
- Electric Sea changes remain uncommitted locally and on the Pi; Weather Brain runtime code was unchanged. Existing historical gaps cannot be recovered from PostgreSQL. Next completed-hour snapshot will be built normally once the Weather Brain scheduler is running.
- Follow-up checksum comparison confirmed all four deployed files are byte-identical to local. Committed locally in Electric Sea as `ea688ec` (`fix: archive Indoor Sky before dashboard pacing`); local Electric Sea worktree is clean. The Pi still has the same four tracked modifications plus the preserved unrelated untracked `pi-setup/scripts/theremin-log` until the user pushes and pulls.

## Resident voices outage diagnosis
- `/voices` returns ECONNREFUSED because nothing listens on 127.0.0.1:3001. `resident.service` is enabled but has restarted over 6,000 times with status 200/CHDIR: its installed and tracked unit uses stale `WorkingDirectory=/home/pi/signal-router/router`, while the active checkout is `/home/pi/electric-sea/router`.
- `resident-live-controls.js`, `resident-live.js`, and package.json exist in the active checkout. The router service itself is healthy on port 3000. Fix requires updating the tracked unit and installed `/etc/systemd/system/resident.service`, daemon-reload, restart, and endpoint verification. Inspection only; no files or services changed.

## Electric Sky stall diagnosis
- On 2026-10-01, Electric Sky stopped reaching `signal_buckets` at about 20:51:36 PDT while Indoor Sky remained current. The public router WebSocket continued receiving current Electric Sky `sample_batch` messages from 192.168.0.44, proving the sensor-to-router scalar path and router process were alive.
- Archival is the same clock-gate class of failure: the deployed drift correction applies only when `message.transport === 'usb'`; Electric Sky's decoded OSC sample batches have no USB transport marker, retain the fixed clock offset, and are now rejected as `late/future sample`. Router journal shows continuing drops while PostgreSQL is stale. A follow-up fix should safely enable drift tracking for uptime-timestamped sample batches on both transports, with separate-node/drop observability tests.
- Separately, Electric Sky's status/dashboard proxy returns 502 because `electric-sky.local` no longer resolves on the Pi. Direct 192.168.0.44 ping and HTTP also fail with host unreachable even though inbound scalar batches arrive, indicating asymmetric reachability/device HTTP failure rather than a stopped scalar producer. The router currently hardcodes the mDNS hostname for HTTP instead of using its live source registry. Inspection only; no code, device, or service changes.

## Electric Sky diagnostics deployed
- Electric Sky recovered without reboot: status still reported boot_count 1 and roughly 58 hours uptime. Router/service restart reset the archive clock anchor, while mDNS/HTTP reachability recovered separately. Device status showed strong RSSI (-27 dBm) but 620 Wi-Fi reconnects, about 972k OSC send failures, 206k transport drops and 397k send stalls across about 8.9m packets, pointing to device/high-rate transport or extender-path behavior rather than simple weak signal. Indoor Sky shared the BSSID but previously reported zero reconnects; a laptop's ordinary traffic is not comparable to Electric Sky's sustained high-rate UDP load.
- Updated Electric Sea archival drift tracking for all uptime-timestamped sample batches (OSC and USB). Correction requires three consecutive packets with at least 250 ms error in one direction, then adjusts at most 50 ms per packet; a jitter regression verifies a single delayed packet does not move samples across second boundaries.
- Drop logs now identify node and distinguish late/future; HTTP archive failures identify node and safe error message. Electric Sky mDNS proxy failures are rate-limited to one log per 30 seconds. Discovery remains hostname-based with no registered/fixed-IP fallback, per user requirement.
- All 95 local router tests passed. Deployed four scoped files and restarted router at 22:41:41 PDT. Live verification: 1,179 Electric Sky and 354 Indoor Sky batches in 25 seconds; PostgreSQL stored 38 consecutive seconds for all eight Electric and four Indoor signals through 22:42:22, with no archive warnings after restart. Local Electric Sea changes are uncommitted; base HEAD fc3e938.

## Electric Sea snapshot backfill completed
- User authorized all Electric Sea history and resumed after baseline clarification. Backfilled electric-sky (33 documents) and indoor-sky (5 documents) for completed hours in 2026-09-29 06:00 UTC through 2026-09-30 15:00 UTC (exclusive). Excluded the apparent test node named string and the unfinished hour.
- Read-only verification matched all 38 source node-hours to exactly one archive row and stable-ID vector each, with exact preview text/metadata and 768-dimensional embeddings. Preview artifacts: scratch/electric-sea-backfill-electric-sky.json and scratch/electric-sea-backfill-indoor-sky.json. Existing matching snapshots were upserted; legacy readings and source buckets were not changed.
- Baselines use earlier signal buckets, not earlier snapshots: up to 30 days of history, at least 18 covered five-minute buckets across three dates for the same local hour and deployment. Rapid changes need no historical baseline.
- No runtime edits, service restart, commit, or push during backfill. Base commit f3d3cf8; next: ongoing hourly scheduler after API restart, as previously documented.

## Electric Sea snapshot commit handoff
- User requested committing the implementation, naming updates, tests, and documentation on main from deb804e. Validation remains as recorded below; whitespace check passed. Ignored .env and scratch artifacts are excluded.
- Next: restart API to load the hourly scheduler and inspect subsequent snapshots. No push or service restart performed in this commit task; no new blockers.

## Electric Sea snapshot naming
- Renamed the module, CLI, tests, documentation, validation artifact, and source-specific functions to use Electric Sea snapshot naming. Runtime behavior and persisted IDs remain unchanged.
- Validation: 18 focused tests passed; five PostgreSQL integration tests skipped without TEST_DATABASE_URL. Renamed CLI help/import and diff whitespace checks passed; no old naming references remain in source, tests, scripts, or docs. No database write, service restart, commit, or push.

## Electric Sea snapshot implementation
- Existing `build_snapshot_records` accepts `source=signal_buckets`; focused `electric_sea_snapshots.py` supplies node discovery, weighted hourly/five-minute statistics, pooled population stddev, and separate deployment/unit sections. One node/hour document fits the existing archive key; no migration. Vector metadata retains structured deployment and signal stats.
- Temperature/celsius and humidity/percent feed the existing event helper with stored-second coverage and same-deployment history. Unknown deployment does not provide historical baselines. Other signals are summarized without event claims. Legacy readings builder and HTTP defaults remain unchanged.
- Electric Sea archive/vector upserts use source-prefixed stable IDs and one transaction, with embeddings prepared first, dimension checks, and a writer lock. Scheduler defaults to signal_buckets, revisits three completed hours with 30-second grace, and survives failures. `SNAPSHOT_SOURCE=readings` restores legacy source scheduling. Explicit CLI preview/apply: `scripts/electric_sea_snapshots.py`; docs: `docs/electric-sea-snapshots.md`.
- Live validation stored and repeated only electric-sky 2026-09-30 14:00-15:00 UTC (Home Office, eight signals). Verified one archive row and one matching 768-dimensional vector including exact text/metadata. Artifact: `scratch/electric-sea-snapshot-validation.json`. Totals now 499 archive rows / 704 collection documents. The pre-existing discrepancy is 205 legacy windows with two vectors each; none were removed.
- Validation: 69 focused database-enabled tests passed, plus the final source-dispatch regression. Full suite: 163 passed, 22 database tests skipped, one previously documented auth direct-call failure. New SQL tests use temporary tables and verify uneven counts, gaps, nodes/deployments/units, history isolation, idempotence, late-data replacement and rollback. Diff whitespace check passed.
- Next: restart API to load the scheduler and inspect subsequent hours; older late arrivals/backfills need explicit bounded rebuilds. No commit/push requested. Existing context notes preserved; no ingestion, frontend, Electric Sea transport, RAG answering, literature or legacy-data changes.

## Electric Sky snapshot preview
- On 2026-09-30, PostgreSQL was reachable. Latest completed Electric Sky hour at inspection was 13:00-14:00 UTC (06:00-07:00 PDT); latest received source timestamp was 14:51:20 UTC. All 26,956 records in that hour were associated with Home Office.
- Generated ignored `scratch/electric-sky-snapshot-preview-20260930.txt` via read-only, repeatable-read queries: weighted means, pooled population stddev, extrema, source sample counts and stored-second coverage for all eight signals. Existing `describe_events` used weighted five-minute temperature/RH means, distinct stored-second counts and explicit one-second coverage expectation. This is an ad hoc preview adapter, not production builder output or an indexed snapshot.
- Temperature/RH each cover 3370/3600 seconds (93.61%), with 335292 original observations per signal. Both have 12/12 covered five-minute buckets, zero baseline-assessable buckets, and no rapid changes crossing default thresholds. No historical-baseline event conclusion is available.
- Review: production builder still targets legacy readings/device/temp_c/rh and hard-coded 1800/hour aggregate coverage. New helper only detects temperature/RH events, not other Electric Sea signals, and does not partition baselines by deployment. Electric Sea integration must address these before indexing. All 17 snapshot event tests pass. No runtime code, database contents or vectors changed.

## Upstream merge handoff
- Preserved both the local database-review findings below and upstream snapshot event implementation, tests, and documentation. The review describes the pre-enrichment state; upstream now adds event descriptions to the legacy readings-based builder.
- Electric Sea signal-bucket adaptation remains future work. No live indexing, database writes, or service startup performed during this merge. No push requested.
- Merged-state validation: all 17 snapshot event tests passed; merge applied without conflicts. Use `git log -1` for the resulting merge commit.

## Snapshot review
- Read-only PostgreSQL inspection found 498 `snapshots` archive rows (all archive embedding columns NULL) and 703 documents in LangChain collection `esp32_rag`. Latest three document texts match between the archive and vector collection: June 19, June 16, and April 20, 2026 UTC windows.
- Current builder reads legacy `readings` for configured `esp32-s3-devkit-001`, produces hourly temperature/humidity summaries and metadata, and assumes 1800 observations/hour for coverage. Default index framework is LangChain; scheduler requests the previous completed hour every 3600 seconds.
- Archive text is upserted separately from vector indexing. LlamaIndex alternative expects `data_esp32_rag`, which was absent from inspected public tables; literature vector table exists separately. Existing collection/archive counts differ; cause not investigated in this examples-only review.
- Electric Sea adaptation needs generic node/signal/deployment metadata, sufficient-statistic pooling and revised coverage semantics. No runtime, schema, database content, embeddings, indexing or service startup changes were made. Only this continuity note changed.

## Remaining changes commit
- User requested all remaining Weather Brain changes be committed on `main`, from `1888c78`. Deployment-management commits are Weather Brain `1888c78` and Electric Sea `ea2e9d0`.
- Includes the previously completed signal-bucket timeseries implementation and README startup notes. Validation remains 50 focused backend tests passing (including PostgreSQL integration); no implementation changes since that run. Diff whitespace check passed.
- No secrets, live database changes or push requested. Next: restart services to load the implemented API features. Use `git log -1` for the resulting commit ID.

## Deployment management
- Added authenticated current/start/change/end routes at `/nodes/{node_id}/deployment`, using status auth for reads and ingest auth for writes. Node-row locks serialize writes; change/end require the observed active ID to reject stale edits. Existing schema/history and ingestion selection are preserved.
- Electric Sea injects a shared control into both proxied dashboards (including cached indoor HTML). A same-origin proxy validates the existing status token entered by the operator, then supplies the server-held ingest token for management writes; no Pi deployment state or public embedded credentials. Configure `WEATHER_BRAIN_STATUS_TOKEN` alongside the existing archive URL/token.
- Added `scripts/backfill_deployment.py`, requiring node, destination deployment, inclusive start and exclusive end. Dry-run by default; `--apply` only fills NULL associations for the validated node/range. No existing archive records have been backfilled.
- Documentation: `docs/deployments.md`. Final validation: 50 focused backend deployment/ingestion/PostgreSQL/timeseries tests passed, including disposable PostgreSQL schemas; all 90 Electric Sea router tests passed, including four new proxy/control tests. JavaScript syntax, backfill CLI help, and diff whitespace checks passed.
- Existing uncommitted `/timeseries` work, README edits and handoff notes are preserved. No firmware, archival aggregation/transport, schema, RAG, snapshots, recorder, PCM/OSC/MIDI changes or live deployment creation.
- Next: set `WEATHER_BRAIN_STATUS_TOKEN` on Electric Sea and restart both services, then explicitly create the real deployment in the dashboard. No backfill was performed. Creating deployment-management commits on `main` from Weather Brain `027f5fb` and Electric Sea `c90a933`; use `git log -1` for resulting IDs. Earlier Weather Brain timeseries/README implementation changes remain uncommitted. No blockers or push requested.

## Electric Sea time-series retrieval
- `/timeseries` accepts `table=signal_buckets`, reuses `device_id` for the node, and adds `signal_id`. Both selectors are required for this source. Existing table defaults, legacy SQL, and summary endpoint are unchanged.
- New supporting `signal_timeseries.py` queries stored summaries or epoch-aligned intervals in PostgreSQL. Means are sample-count weighted; population variance uses the centered within/between-bucket formula. No fixed sample rate or gap filling. Full/lite response envelopes and signal-named metric aliases follow existing conventions.
- Producer population-stddev convention, parameter/response details, inclusive time boundaries, and null behavior are documented in `docs/signal-timeseries.md`.
- Validation: 43 focused tests passed, including PostgreSQL integration tests with unequal source counts, differing means/stddevs, multiple resolutions, selector isolation, numerical stability, and legacy responses. Integration tests use `TEST_DATABASE_URL`, transaction-local temporary tables, and rollback; no persistent table/data changes. Diff whitespace check passed.
- Preserved pre-existing README and migration handoff edits. No ingestion, Electric Sea, snapshot, RAG, frontend, or schema edits. Branch `main`, HEAD `027f5fb`. Next: restart API to expose the new query option; no blockers.

## Electric Sea live migration applied
- On 2026-09-28, inspected commit `52d23c6` and applied `migrations/20260928_electric_sea_signal_buckets.sql` using the root `.env` DATABASE_URL. All three target tables were absent before execution; the migration committed successfully in the `public` schema.
- Verified `nodes`, `node_deployments`, and `signal_buckets`, all six indexes, primary/foreign keys, and check constraints. Live bucket insertion with a deployment and duplicate retry passed in a rolled-back transaction; no test rows remain.
- Focused tests: 22 passed (`tests/test_signal_buckets.py tests/test_postgres.py`). Existing tables/data were not modified by this migration. No API startup, indexing, deployment, commit, or push performed.
- Next: ensure the running API uses commit `52d23c6` or later before enabling Electric Sea posting. No migration blockers. Branch `main`, HEAD `027f5fb`.

## Electric Sea aggregate ingestion
- The repository has no ORM models or tracked migration framework. Legacy `/ingest` dynamically inserts wide rows into `readings`; `/timeseries` and snapshot/RAG code assume its existing `ts`, `device_id`, temperature, and humidity columns. Do not alter those paths for Electric Sea aggregates.
- `migrations/20260928_electric_sea_signal_buckets.sql` is a forward-only migration. It adds `nodes`, `node_deployments`, and `signal_buckets`, preserving the existing `readings` and `snapshots` tables and all historical queries. `node_deployments` retains node-independent deployment name/location/coordinates/altitude/start/end/notes/metadata and permits only one open deployment per node.
- `POST /ingest/signal-buckets` accepts up to 5,000 authenticated scalar aggregates for one node. Each record requires a timezone-aware bucket start, generic signal ID, mean/min/max/stddev, and sample count; unit and metadata are optional. Extra fields, including raw sample arrays, are rejected. The endpoint automatically creates a node identity if needed and associates all newly inserted buckets with its current open deployment. It never stores PCM.
- `signal_buckets` has primary key `(node_id, signal_id, bucket_start)`. Inserts use `ON CONFLICT DO NOTHING`, so an exact batch retry creates no duplicate and reports it as a duplicate. The original deployment association remains intact if a node later moves.
- Local `DATABASE_URL` is absent, so no live schema inspection or migration execution occurred. Focused tests passed: `22 passed` for `tests/test_signal_buckets.py tests/test_postgres.py`; compile checks passed with `PYTHONPYCACHEPREFIX=/tmp/weather-brain-pyc` because repository `__pycache__` paths are read-only in this environment. Full suite: 141 passed, with the one pre-existing direct-call `Query` default failure in `test_require_status_token_rejects_missing_header`. Next: apply the migration to the configured PostgreSQL instance before enabling Electric Sea posting.

## Public Literature Provenance
- `/rag/query` now projects each `literature_sources` entry to citation, source, page, optional category/organization and score. Node IDs, nested/raw metadata, paths, sizes, timestamps and document/storage identifiers are excluded.
- The same allowlist plus passage text is sent to answer synthesis, preventing the model from echoing internal metadata. Underlying vector metadata and ingestion are unchanged.
- The existing synthesis prompt now requires `[data]` for SQL facts, reserves numbered citations for the matching literature passages, requires separate provenance for combined claims, and forbids `[data]` in literature-only answers. Existing grounding/qualifier rules remain.
- Focused MVP/diagnostics suite: 87 passed. Full suite: 138 passed, one unchanged pre-existing direct-call auth failure (`test_require_status_token_rejects_missing_header`). Compileall/diff checks passed.
- Call-count tests still verify SQL 1/0, literature 1/1 and combined 2/1 LLM/embedding calls. No routing, SQL, retrieval, model, context, RAG, snapshot, latency, storage or architecture changes. No commit requested; existing pending edits preserved. Branch main, base 2b25c90.
- User requested a commit after this work. Validation remains 87 focused tests passing and 138 full-suite tests passing with the one pre-existing auth direct-call failure. `.env.example` is deliberately excluded as a pre-existing local configuration edit.

## MVP SQL Date Range and Failure Handling
- `POSTGRES_TEXT_TO_SQL` now requires direct `ts` comparison with inclusive local-calendar start and exclusive end timestamps, forbids DATE casts/BETWEEN for calendar periods, and includes the requested Jan-Mar 2026 America/Los_Angeles example plus a generalization rule.
- `answer_with_llamaindex` catches `psycopg.Error` only around validated SELECT execution. It logs request ID, generated SQL and the PostgreSQL exception server-side, then returns a generic rephrase answer with the original route/paths, generated SQL, timing/call counts, null SQL result and `sql_status: error`. No exception detail reaches the response.
- Added prompt/canonical SQL tests and an API-level combined-route database-error regression. Focused MVP/diagnostics suite: 85 passed. Full suite: 136 passed, one unchanged pre-existing direct-call auth failure (`test_require_status_token_rejects_missing_header`). Compileall and diff whitespace checks passed.
- No retries, extra calls/stages, model/context/RAG changes, retrieval/synthesis changes, snapshot changes, validation/transaction changes or commit. Existing pending edits preserved; branch main, base 2b25c90.

## Literature Evidence Prompt
- Updated only the shared MVP literature/combined synthesis prompt in `llamaindex/answering.py`: per-claim citations, explicit evidence categories, no inferred recommended ranges, missing-answer disclosure, and preservation of lower/upper qualifiers.
- 56 MVP tests passed; diff whitespace check passed. Live authenticated humidity query returned 200 with one embedding and one LLM call and stopped inventing a recommended range. It still dropped the word lower in one claim; added an explicit qualifier-preservation instruction afterward. That final instruction has not been live-retested. Prompt guidance does not guarantee factual compliance.
- No retrieval, model, context, data, architecture or other behavior changed. No commit requested; existing pending changes preserved. Branch main, base 2b25c90.

## Literature Replacement Completed
- `literature_ingestion.py` now strictly loads PDFs, prepares all chunks/embeddings before deletion, and deletes/inserts selected sources under one PostgreSQL transaction and writer lock. Splitter/model/dimension/retrieval settings unchanged.
- Added focused tests and local-only `scripts/rebuild_literature.py --apply` maintenance/verification command. Backup and live report: `scratch/literature-rebuild-20260927T160441Z/`.
- Before: 6247 rows, including 6245 PDF rows and two web rows. The requested humidity query reproduced two exact-score page-14 duplicate pairs in positions 1-4.
- All seven PDF source names match the current authoritative corpus. Rebuild preserves the two web rows and does not touch snapshots, LangChain tables, raw readings or schemas.
- First rebuild committed: 4432 freshly prepared PDF chunks + two unchanged web rows = 4434 total. Verified every prepared chunk occurrence, including 3347 overlapping adjacent pairs. Real injected post-delete/partial-insert failure rolled back to the full original row fingerprint.
- Single-PDF re-ingestion of M&V Protocol retained 441 rows; total stayed 4434. Second full PDF ingestion also retained every source count and 4434 total rows. Zero repeated source/page/text/position groups; one parent document per PDF page. M&V page 14 now has eight distinct chunks/vectors.
- Authenticated TestClient query (without startup/scheduler) returned HTTP 200, literature route, five distinct passages, one embedding and one LLM call, 23.779 seconds. Response saved beside the report. Synthesis misinterpreted upper humidity limits as a recommended range; documented but intentionally not changed in this ingestion task.
- Results and per-source before/after counts: `docs/literature-rebuild-results.md`. Tests: 133 passed, one pre-existing auth failure; compileall/diff checks passed. No commit requested. Branch main, base 2b25c90. Existing unrelated pending changes preserved. Implementation and authorized cleanup complete; retain CSV backup.

## Literature Duplicate Investigation
- Local `public.data_rag_literature_chunks` has 6247 rows, all distinct node IDs. RAG_K is 5.
- M&V Protocol 2001.pdf page 14 has 16 rows: IDs 1293-1300 and 3341-3348, eight matching chunk-position pairs with identical vectors and source metadata. Seven text pairs are exact; 1300/3348 differ by one extra space. Eight whitespace-normalized texts total, two parent document UUIDs.
- Across the index, 1710 groups share exact source/page/text, with 1724 excess rows by that grouping; inspect provenance/positions before treating every group as removable.
- Code loads PDF pages, normalizes into fresh Documents, uses SentenceSplitter(256, overlap=50) with random UUID node IDs, cleans text, embeds metadata-aware node content and appends via PGVectorStore.add. No idempotency, deletion or unique node/content constraint; only numeric row ID is unique.
- Cause: repeated ingestion/reprocessing is strongly indicated, not intentional multi-representation indexing. Exact historical trigger cannot be proven from the table. Current query text/result IDs were not provided, so the specific top-five result was not replayed.
- Recommended smallest durable fix: prepare a source's chunks/embeddings then transactionally replace that source's existing rows, plus a reviewed cleanup of existing equivalent chunks. Stable node IDs alone do not stop append-only inserts without uniqueness/upsert or an existence check.
- Inspection used local-only connection validation, read-only transactions and timeouts; no API startup, ingestion, re-embedding or data/schema changes. Only this handoff note was updated.

## SQL-biased Router Follow-up
- Benchmark `tell me weather trends from January to March 2026` previously missed singular `trend` and the limited date rules, then fell through to the literature default.
- Router now defaults to SQL, recognizes plural observed/statistical terms and month/year periods, and requires knowledge/guidance intent for literature. Observed-data comparisons with standards/research select both paths; publication years alone do not imply sensor observations.
- Added benchmark, route-boundary, definition, research and combined-path regression cases. Only routing, tests and supporting documentation changed in this task; all existing pending MVP edits were preserved.
- No prompts, model/context settings, retrieval, synthesis, SQL generation or snapshot behavior changed. No live calls or commits. Branch `main`, base `2b25c90`.
- Validation: 82 routing/SQL/diagnostics tests passed; diff whitespace check passed. Next: restart FastAPI and retry the exact benchmark against local sensor data.

## MVP Answering
- New `planning/knowledge_paths.py` selects sql/literature/sql+literature with English heuristics, no model routing. Explicit LangChain selection retains snapshot experiments.
- `llamaindex/answering.py` now runs one-call SQL or one-call literature, or two-call combined answering. Direct PGVectorStore top-k uses the existing literature table, RAG_K and 768-dimensional model. Context remains 8192.
- Shared factories in `runtime.py`; old multi-stage engines moved to `experimental.py`. Placeholder document embedding/index creation removed entirely.
- `retrieval/structured/generated_sql.py` validates a single SELECT and executes under SET TRANSACTION READ ONLY with a 30-second statement timeout. Unsupported generated SQL returns 422 before execution.
- Response metadata includes paths, SQL/rows, literature sources, stage/LLM/token timings, call counts, and total time through response construction. Full response-send time remains in logs.
- Endpoints/auth/indexers/snapshots/scheduler/schema/dependencies/deployment are unchanged. No live DB/Ollama work performed.
- Documentation: `docs/mvp-answering.md`. Next: restart API and verify live route quality/latency. Heuristics and conservative SQL syntax intentionally have MVP limits.
- Branch `main`, base `2b25c90`; no commit/push requested.
- Final validation: 103 tests passed; the same pre-existing missing-status-token unit test fails on its FastAPI Query default. Compileall and diff whitespace checks passed. External I/O was mocked for query-path tests; live latency remains unmeasured.

## Diagnostics Commit Handoff
- Scope includes timing/token logs, robust no-lookup parsing, regression tests, documentation, and both existing model context settings.
- Validation remains 22 focused tests passing; full suite 67 passed with one pre-existing auth failure. Diff whitespace check passes.
- `.env` remains ignored and excluded. No push requested. Next: restart FastAPI and measure the corrected no-lookup path live.
- Commit being created on `main` from parent `ee0aa6e`; use `git log -1` for its resulting ID.

## No-lookup and Token Metrics Follow-up
- User's live run measured six LLM calls and 36.997 seconds before this fix.
- `no_literature_lookup` is passed as `SQLAugmentQueryTransform.check_stop_parser`. Bare `None` and `LOOKUP: None` are matched case-insensitively with whitespace normalization; real query strings are untouched.
- No-lookup regression uses the real installed engine with external I/O mocked: three LLM calls, no literature retrieval/planning/synthesis or combined synthesis, existing SQL response returned. Real lookups retain six calls.
- `llm_call` logs include available `prompt_tokens`, `output_tokens`, `prompt_eval_ms`, and `generation_ms`, read from raw Ollama response metadata. Missing values are omitted; nanoseconds converted to milliseconds; no new requests or response mutations.
- LlamaIndex context remains 8192, verified against model call options. No further pipeline optimization, schema, prompt or model changes.
- Focused diagnostics tests: 22 passed. No post-fix live timing measured; restart the API to clear its cached query engine before verifying. Branch `main`, base commit `ee0aa6e`; no commit/push requested.
- Final validation: full suite 67 passed with the same pre-existing auth failure; compileall and diff whitespace checks passed.

## LlamaIndex Performance Diagnostics
- Added removable standard-library timing/logging adapters in `server/app/frameworks/llamaindex/diagnostics.py`, middleware registration in `main.py`, and engine/query scopes in `answering.py`.
- Logs include request IDs, total duration, actual synchronous LLM/embedding call counts, purpose/duration, SQL generation, SQLAlchemy PostgreSQL execution, query/fetch, vector retrieval and synthesis. Durations are inclusive; no payloads are added to logs.
- Offline tests of the installed engine confirm six sequential LLM calls on SQL plus literature: selection, SQL generation, transformation, retrieval planning, literature synthesis, combined synthesis. Cold initialization adds a discarded placeholder-document embedding (two embeddings cold, one warm in the tested path).
- Confirmed `LOOKUP: None` does not match the installed stop parser's exact `none` check and continues to literature retrieval. This and other possible redundant work remain unchanged.
- Details and removal instructions: `docs/llamaindex-performance.md`. No real database/Ollama request was run; the observed 43-second latency has not yet been attributed.
- Validation: all six new diagnostics tests passed; full suite 51 passed with the same pre-existing auth failure. Compileall and diff whitespace checks passed.
- Preserved user edits present at task start: LlamaIndex `context_window=8192` and LangChain `num_ctx=8192`. No models, prompts, retrieval settings, SQL, schemas, API responses or dependencies changed by this task.
- Next: restart FastAPI, issue one LlamaIndex `/rag/query`, and inspect `rag_timing` lines sharing its request ID. No commit or push requested; branch `main`, HEAD `ee0aa6e`.

## Commit Handoff
- Scope: direct PostgreSQL configuration/access, vector-store preservation, dependency cleanup, ingestion HTTP errors, regression tests, and documentation.
- Validation: 19 PostgreSQL tests passed; diff whitespace check passed. The full-suite baseline auth failure is documented below.
- `.env` is ignored and excluded. No push is requested. Live database validation remains outstanding.
- Commit is being created on `main` from parent `d91e7707cf9a0b0176cfa998eaf335b67bb77ed0`; use `git log -1` for the resulting commit ID.

## Ingestion Error Handling Follow-up
- `/ingest` returns HTTP 503 with `ok: false`, `postgres: error`, and a generic detail when a write fails or database configuration is missing. Successful writes still return HTTP 200.
- Failed requests preserve the previous `latest_reading`; only persisted readings update `/latest`.
- Updated endpoint regression tests and README. All 19 PostgreSQL tests pass; `git diff --check` passes. No live database writes were performed.
- Next: local connectivity validation remains outstanding; prior `.env` observations below refer to the migration turn, before the user's subsequent edits.
- Branch remains `main` at `d91e7707cf9a0b0176cfa998eaf335b67bb77ed0`; no commit or push.

## PostgreSQL Migration
- Shared `server/app/database.py` reads only `DATABASE_URL`; vector/SQLAlchemy URLs use psycopg 3 and the `public,extensions` session search path.
- Structured inserts/reads now use parameterized psycopg queries; callers and tests use PostgreSQL names. Both requirements files drop the client and its five dedicated packages.
- LlamaIndex setup is disabled. LangChain uses an existing-store adapter that skips table creation and validates existing collections without creating them.
- Real `.env` is unchanged and still contains legacy connection variables, with no `DATABASE_URL`. User must supply the local password in the new URL before live validation.
- Real mapping is `SNAPSHOT_DATA_TABLE=snapshots`, `RAG_SNAPSHOT_TABLE=esp32_rag`, `RAG_LITERATURE_TABLE=rag_literature_chunks`. LlamaIndex snapshots therefore expect `data_esp32_rag`, absent from the supplied migrated-table list; preserve this and resolve before using that path. LangChain interprets `esp32_rag` as a collection name.
- No database connections, data/schema changes, indexing, deployment changes, commits, or pushes were performed. The existing startup indexer remains enabled; do not launch it merely to validate connectivity.
- Baseline tests: 26 passed, one pre-existing failure in `test_require_status_token_rejects_missing_header` (direct call leaves a FastAPI Query default).
- Branch `main`, latest commit `d91e7707cf9a0b0176cfa998eaf335b67bb77ed0`.
- Final validation: 44 tests passed, with the same one pre-existing auth failure (18 added migration cases pass). Python compileall passed; diff whitespace check passed after removing one trailing space.
- Next: user configures `DATABASE_URL` for a read-only local smoke check. The code refactor is complete; live connectivity and existing collection contents have not been verified.

## Current Device Work
- Target: Espressif ESP32-S3-DevKitC-1 with ESP32-S3-WROOM-1-N8R8 (8 MB flash, 8 MB octal PSRAM).
- Wiring: BME280 SDA/SCL on GPIO10/9; INMP441 SD/BCLK/WS on GPIO16/17/18. INMP441 L/R is grounded for left-channel capture.
- OTA hostname: `indoor-sky.local`.
- PlatformIO USB and OTA environments are under `device/`.
- Existing ignored `device/secrets.py` supplies Wi-Fi credentials at build time without copying them into tracked source.
- Bootstrap firmware was installed successfully over native USB at `/dev/cu.usbmodem11201`.
- Bootstrap is online at `192.168.0.32`; `/status` confirms clean firmware identity, strong Wi-Fi, 8 MB PSRAM, and OTA readiness.
- macOS currently times out resolving `indoor-sky.local`, although the device advertises the name; OTA can target the IP directly.
- `/status` verifies `wifi_sleep: false`; loss improved to zero, but the extender path remains bursty at roughly 331 ms average latency.
- OTA negotiates and transfers normally, but the stock Espressif uploader aborts whenever one 1 KB acknowledgment exceeds its hard-coded 10-second limit. `device/scripts/espota.py` is the upstream uploader with that per-chunk timeout changed to the configured value; the OTA environment selects it through `use_local_espota.py`.
- The local uploader revealed the matching device-side limit: ArduinoOTA defaults to a one-second receive timeout with only three retries. Firmware now sets a 30-second receive timeout so transient extender stalls do not abort the update server.
- OTA was verified end to end on 2026-07-20 by wirelessly reinstalling clean firmware `1714178`; PlatformIO reported success and `/status` confirmed a reboot into the freshly built image. Use the explicit IP while mDNS remains unreliable on the extender network.
- Sensor diagnostics firmware `1f231cb` was installed OTA through `indoor-sky.local`. Live `/status` checks confirmed the BME280 at its configured I2C address with plausible readings (~29.1 C, 38.3% RH, 998.2 hPa) and confirmed changing, nonzero INMP441 samples at 16 kHz on GPIO16/17/18.
- The full transport/dashboard firmware uses the electric-sky architecture: BME at a requested 100 Hz, RMS at 250 Hz, 20 binary WebSocket batches/sec, six seconds of PSRAM transport buffering, 10-second browser scopes with adjustable presentation delay, network/reset/stack diagnostics, `/batch/indoor-sky/*` OSC routes to `192.168.0.41:5005`, and optional 16 kHz PCM to port 5007. Raw PCM defaults off and auto-disables after repeated send failures.
- Firmware `3f24e68` was verified live after OTA: BME 100 Hz, RMS 250 Hz, no overruns or transport drops, strong Wi-Fi, healthy stack reserves, and correct dashboard HTML. Packet capture on the Pi confirmed indoor OSC batches on UDP 5005 and valid 672-byte PCM frames on UDP 5007; the PCM stream key is `pcm/192.168.0.32/audio`.

## Repository Map
- `esp32_api`: Python/FastAPI backend.
- `esp32_ui`: Next.js/React/TypeScript frontend.
- `b2b-dashboard-demo`: Separate Next.js/React/TypeScript frontend.

## Relevant Setup Notes
- `esp32_api` uses a local virtual environment at `.venv`.
- The backend interpreter is pinned in `esp32_api/.vscode/settings.json` with:
  - `python.defaultInterpreterPath: ${workspaceFolder}/.venv/bin/python`
  - `python.terminal.activateEnvironment: true`
- For the saved multi-root workspace, the interpreter setting should live in the `.code-workspace` file with the `esp32_api` folder-specific path.

## Frontend Notes
- `esp32_ui` and `b2b-dashboard-demo` are both standard Node/Next.js projects.
- Both advertise Node `>=20` and use TypeScript plus ESLint.
- These repos normally do not need special interpreter-style workspace settings.
- If VS Code ever misidentifies the frontend tooling in a multi-root session, add workspace-level overrides only then, such as `typescript.tsdk` or `eslint.workingDirectories`.

## Practical Guidance
- Prefer keeping workspace settings minimal.
- Pin the Python interpreter for `esp32_api`.
- Let VS Code auto-detect the TypeScript and ESLint tooling for the two frontend repos unless a concrete editor issue appears.
- Keep notes short and specific to what changed, what is next, and any blockers.

## Handoff Format
- When leaving context for the next session, record:
  - What changed
  - What is next
  - Any blockers or risks
  - Any repo-specific paths or settings that matter

## Source Context
- This workspace file was distilled from the continuity pattern used in the `orcasound-next` `docs/agent-context.md` file, but only the parts relevant to this workspace were retained.
