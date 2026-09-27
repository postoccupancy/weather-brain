# Agent Context

## Last Updated
- 2026-09-27

## Workspace Scope
- Multi-root workspace covering `esp32_api`, `esp32_ui`, and `b2b-dashboard-demo`.
- Use this file for dynamic task/session context and handoff notes.
- Use workspace or repo settings for stable editor/runtime configuration.

## Current Objective
- Commit the completed RAG diagnostics, no-lookup fix, and existing 8192 context settings at the user's request.

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
