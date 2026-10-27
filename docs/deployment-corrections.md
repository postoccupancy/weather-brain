# Historical deployment correction

Use **Change deployment** when moving a node now. Use the existing
`backfill_deployment.py` only for explicit NULL associations. Use **Correct
deployment history** when an existing adjacent transition has the wrong date.
The latter changes the shared boundary and only buckets assigned to the selected
source deployment in the half-open interval between the old and new boundary.
Moving the boundary earlier transfers predecessor to successor; moving later
transfers successor to predecessor. NULL and already-correct records are untouched.

Install `migrations/20261026_deployment_corrections.sql` before running this code.
Preview is read-only and can run without the new audit table. Apply locks the
permanent node and deployment rows, rechecks the preview fingerprint, and commits
both boundaries, bucket assignments, audit record, and rebuild invalidation range
atomically. Ingestion and NULL backfill participate in the same node locking.
An expired preview returns 409; refresh and preview again. Replaying an applied
token with the identical request returns its audit record without repeating writes.
Current hour rollover, changed counts, new stored windows, or history edits can
expire a preview. No personal identity is available from existing shared-token
authentication; `operator_identity` remains NULL. The operator reason is required.

## API and UI

All correction paths use `/nodes/{node_id}/deployment/`:

| Method and suffix | Weather Brain authorization | Result |
| --- | --- | --- |
| POST `correction-preview` | `X-Status-Token` | Current/proposed history, source/target IDs, exact interval, counts by signal, snapshot window list and scope, preview token; conflicts return 409 |
| POST `correction-apply` | `X-Ingest-Token` | Atomic correction and audit; rebuild initially pending |
| GET `correction-status` | `X-Status-Token` | Audit, counts, rebuild cursor, pending/failed/complete status, sanitized error |

Preview body: `old_deployment_id`, `new_deployment_id` (chronological predecessor
and successor), timezone-aware `boundary`, and nonblank `reason`. Apply adds
`preview_token`. The UI on both Electric Sea sensor dashboards shows history,
requires a timestamp with Z or an explicit offset, displays its UTC equivalent,
and requires a separate **Apply correction** after preview. Its existing admin
session protects POSTs; Weather Brain credentials remain server-side.

```powershell
$base = 'http://100.115.91.11:8000'
$read = @{ 'X-Status-Token' = 'YOUR_STATUS_TOKEN' }
$write = @{ 'X-Ingest-Token' = 'YOUR_INGEST_TOKEN' }
$body = @{
  old_deployment_id = 'bf5f094d-6782-4c7b-b3c5-9470b403ea76'
  new_deployment_id = 'c5db35da-1121-49a6-b669-25cd8af816b1'
  boundary = '2026-10-23T21:00:00Z'
  reason = 'Physical move to Gallery on October 23; administrative update recorded October 26.'
}
$preview = Invoke-RestMethod "$base/nodes/indoor-sky/deployment/correction-preview" -Method Post -Headers $read -ContentType 'application/json' -Body ($body | ConvertTo-Json)
New-Item -ItemType Directory -Force scratch | Out-Null
$preview | ConvertTo-Json -Depth 20 | Set-Content -Encoding utf8 scratch/correction-preview.json
# After reviewing the preview and approving the production operation:
$body.preview_token = $preview.preview_token
Invoke-RestMethod "$base/nodes/indoor-sky/deployment/correction-apply" -Method Post -Headers $write -ContentType 'application/json' -Body ($body | ConvertTo-Json)
Invoke-RestMethod "$base/nodes/indoor-sky/deployment/correction-status" -Headers $read
```

CLI preview (no write unless both `--apply` and the reviewed `--preview-token` are supplied):

```powershell
.venv/Scripts/python.exe scripts/correct_deployment.py --node-id indoor-sky --old-deployment-id bf5f094d-6782-4c7b-b3c5-9470b403ea76 --new-deployment-id c5db35da-1121-49a6-b669-25cd8af816b1 --boundary 2026-10-23T21:00:00Z --reason "Physical move recorded late"
```

## Rebuild scope and recovery

Every snapshot embeds deployment intervals. Thus even genuine Home Office hours
may need new text/embeddings when its end changes, although their buckets must
remain Home Office. Preview includes both deployments' entire existing extent,
all stored documents naming either deployment, the preceding hour of event
context, and up to 30 subsequent days of deployment-specific baseline dependency.
The future portion is capped at the current hour: new future snapshots naturally
read corrected data. This is a conservative continuous range; gaps do not create
documents. The current unfinished hour remains pending until it is completed.

The existing hourly scheduler processes one day per pending correction per pass,
outside the correction transaction. Each chunk uses the bounded Electric Sea
builder, stable document IDs, and atomic archive/vector upserts. Progress is durable;
failures remain visible and retry automatically. The default pace deliberately
limits exhibit load; a month of context can take roughly 28 hourly passes.
To inspect status or explicitly advance/retry one daily chunk outside that cadence:

```powershell
.venv/Scripts/python.exe scripts/rebuild_deployment_snapshots.py --node-id indoor-sky
# Embedding work and archive/vector writes; run only when authorized:
.venv/Scripts/python.exe scripts/rebuild_deployment_snapshots.py --node-id indoor-sky --apply
```

An invalidated document without source buckets causes a visible failure, not false
completion. A deployment edit during building/embedding rejects the stale write.
Pending ranges remain disclosed in snapshot timeseries responses; snapshot-based
LangChain answering rejects affected documents with 409 until their rebuild cursor
passes those hours. Old archive/vector contents are retained during recovery.
Correction completion and snapshot completion are separate statuses.

See [production dry run and rollout plan](indoor-sky-correction-20261026.md),
[archive pathways](archive-pathways.md), [deployment controls](deployments.md), and
[snapshot implementation](electric-sea-snapshots.md).
