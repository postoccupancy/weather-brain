# Agent Context

## Last Updated
- 2026-09-29

## Workspace Scope
- Multi-root workspace covering `esp32_api`, `esp32_ui`, and `b2b-dashboard-demo`.
- Use this file for dynamic task/session context and handoff notes.
- Use workspace or repo settings for stable editor/runtime configuration.

## Current Objective
- Review existing snapshot/vector structures and show actual stored documents before adapting them to Electric Sea. Read-only investigation completed; implementation not requested yet.

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
