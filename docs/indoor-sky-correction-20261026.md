# Indoor Sky historical correction: dry run and deployment plan

Status: prepared, not applied. No production code, schema, deployment boundary,
buckets, snapshots, or embeddings were changed. No exhibit services were restarted.

## Running configuration inspected

On October 26, 2026, read-only inspection over Tailscale found:

- Pi `100.118.138.43`: `router.service` runs `/usr/bin/node server.js` from
  `/home/pi/electric-sea/router`, started October 25 at 15:29:12 PDT. Checkout
  `ecc2935`. Router configuration points to `https://api.postoccupancy.com`;
  status, ingest and admin credentials are configured (values not recorded).
- Windows `100.115.91.11`: listening PID 3312 runs the Python 3.12 uvicorn
  process on port 8000, launched through the Weather Brain virtual environment
  at `C:\Users\PC1\projects\weather-brain`. Checkout `163114e`.
  Running OpenAPI has only current/start/change/end deployment routes.
- Windows checkout configuration selects localhost:5432 PostgreSQL,
  `snapshots` archive, `esp32_rag` collection and the default `signal_buckets`
  scheduler source. This is on-disk configuration, not proof that every setting
  was loaded by the already-running process. Archive/vector rows were checked
  separately. Neither checkout HEAD alone proves loaded process code.

## Database-confirmed proposal

The repeatable-read, read-only query snapshot was observed at
`2026-10-26T18:20:46.956973Z` (11:20:46.956973 PDT).

| Deployment | ID | Current interval (UTC) | Proposed interval (UTC) |
| --- | --- | --- | --- |
| Home Office, Capitol Hill, Seattle, 98112 | `bf5f094d-6782-4c7b-b3c5-9470b403ea76` | `[2026-09-29T18:41:21.039521Z, 2026-10-26T17:22:12.501752Z)` | `[2026-09-29T18:41:21.039521Z, 2026-10-23T21:00:00Z)` |
| 9th Ave Gallery, Seattle, 98121 | `c5db35da-1121-49a6-b669-25cd8af816b1` | `[2026-10-26T17:22:12.501752Z, open)` | `[2026-10-23T21:00:00Z, open)` |

Both records belong to `indoor-sky`, are adjacent, and share the current boundary.
The proposed boundary is timezone-aware, after Home Office's start and before
Gallery's open end. There are no other indoor-sky deployments to overlap.

Only `indoor-sky` buckets currently assigned to Home Office in
`[2026-10-23T21:00:00Z, 2026-10-26T17:22:12.501752Z)` are eligible for reassignment
to Gallery. Genuine earlier Home Office buckets, already-Gallery buckets, NULLs,
the exclusive upper boundary and other nodes are excluded.

| Signal | Eligible buckets |
| --- | ---: |
| humidity | 68,655 |
| pressure | 68,655 |
| rms | 68,655 |
| temperature | 68,655 |
| Total | **274,620** |

These are persisted one-second bucket rows, not raw sample counts. Candidate reason:
"Physical move to Gallery on October 23; administrative deployment update recorded October 26."

## Snapshot impact

Read-only inspection found 500 indoor-sky archived documents and 500 Electric Sea
vector documents. Of these, 496 archive documents name one of the two deployment
IDs, spanning starts at `2026-09-29T18:00:00Z` through an exclusive end of
`2026-10-26T17:00:00Z`. Nineteen stored documents cover the directly changed hours.
Other documents need rebuilding because they embed the Home Office end boundary,
or consume adjacent-hour and up-to-30-day deployment-specific baseline context.

At inspection time the conservative rebuild range was
`[2026-09-29T18:00:00Z, 2026-10-26T19:00:00Z)`: **649 hourly windows**, including
gaps and the unfinished current hour. The range contains 496 persisted documents
at that observation; generation may create additional documents where buckets
exist but snapshots do not. Genuine historical bucket assignments remain unchanged.
Future snapshots read corrected history automatically. A fresh preview may expand
the scope as new hours arrive; its fingerprint must be reviewed before apply.

The service preview was not executed on production: automatic approval review
rejected executing a module that also contains mutation code. The live figures
above instead came from explicit SELECT-only queries with PostgreSQL
`default_transaction_read_only=on`; there is no production apply token from this
inspection. The implemented service has been exercised against isolated PostgreSQL.

## Proposed rollout (requires approval)

1. Preserve the current Windows/Pi checkouts and database backup. Review repository
   diffs and this dry run. Schedule the API/router restarts outside active audiovisual
   presentation; a Pi router restart briefly interrupts signal routing.
2. Apply only `migrations/20261026_deployment_corrections.sql` on Windows, then
   install the reviewed Weather Brain code and restart its API once. This adds an
   empty audit/progress table, not a data correction. Verify authentication,
   history and preview endpoints against the running API. Confirm its indexing
   loop and configured embedding provider/collection before applying data changes.
3. Install the reviewed Electric Sea proxy/control code and restart `router.service`
   once during the agreed window. Verify admin login and preview on both dashboards.
   No firmware, resident analyzer, sensor or audio service restart is required.
4. Generate a fresh indoor-sky correction preview using the confirmed IDs and
   `2026-10-23T21:00:00Z`. Review any count/scope changes; obtain approval for that
   concrete preview, then explicitly apply once. Repeating its token is safe.
5. Verify both boundaries, per-signal changed counts, audit reason and pending
   rebuild state. Let the existing scheduler advance one day per hourly pass, or
   authorize bounded manual retries when embedding load is acceptable. Watch
   `rebuild_next`, `rebuilt_windows`, and failures; do not declare snapshot completion
   until status is complete and archive/vector text and IDs agree.

Do not revert to old snapshot writers while a correction is rebuilding: they lack
the deployment revision guard. A failed correction transaction rolls back fully;
a failed embedding job retains its progress and retries without undoing the
already-committed boundary correction. A later historical correction is a new
reviewed operation, never an unaudited manual reversal.

See [API/CLI and recovery](deployment-corrections.md) and [archive discoverability](archive-pathways.md).
