# Archive and snapshot pathways

| Request | Meaning |
| --- | --- |
| `/latest` | Last legacy `/ingest` reading held by the API process; not the Electric Sea archive |
| `/timeseries` | Legacy `readings` by default |
| `/timeseries?table=signal_buckets` | Current Electric Sea archive; requires `device_id` (node ID) and `signal_id` |
| `/timeseries?table=snapshots` | Stored hourly snapshot documents; pending correction ranges appear in `snapshot_corrections` |
| `/nodes/{node_id}/deployments/{deployment_id}` | Historical name, location and interval referenced by a bucket's deployment ID |

Weather Brain currently runs on the Windows PC with local PostgreSQL; the Pi
rf-router forwards buckets. The historical Supabase-to-local hosting migration is separate from the
readings-to-signal_buckets source transition. A successful `/latest` request does
not verify bucket ingestion. An archived snapshot does not prove its embedding
exists or that an automatic scheduler is running.

From PowerShell, use placeholders for credentials:

```powershell
$base = 'http://100.115.91.11:8000'
$read = @{ 'X-Status-Token' = 'YOUR_STATUS_TOKEN' }
Invoke-RestMethod "$base/latest" -Headers $read
Invoke-RestMethod "$base/timeseries?table=signal_buckets&device_id=indoor-sky&signal_id=temperature&limit=10" -Headers $read
Invoke-RestMethod "$base/timeseries?table=snapshots&device_id=indoor-sky&limit=10" -Headers $read
Invoke-RestMethod "$base/nodes/indoor-sky/deployments" -Headers $read
Invoke-RestMethod "$base/nodes/indoor-sky/deployments/c5db35da-1121-49a6-b669-25cd8af816b1" -Headers $read
```

Verify separately: (1) bounded snapshot generation produces documents,
(2) those documents persist in the archive, (3) matching stable IDs/text/metadata
exist in the vector collection, (4) the selected retrieval route reads that
source, and (5) scheduler logs and advancing archive windows prove automatic runs.
The default SQL/literature answer route is distinct from experimental snapshot RAG.

See [signal timeseries](signal-timeseries.md), [snapshot generation and indexing](electric-sea-snapshots.md),
[ordinary changes and NULL backfill](deployments.md), and
[historical correction](deployment-corrections.md).
