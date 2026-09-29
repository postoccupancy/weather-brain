# Node deployments

Weather Brain owns node deployment/location state. No schema migration is needed:
the existing `nodes` and `node_deployments` tables already have the required fields
and a unique partial index enforcing one open deployment per node.

## API

All paths start with `/nodes/{node_id}/deployment`:

| Method/path suffix | Authentication | Behavior |
| --- | --- | --- |
| GET (no suffix) | `X-Status-Token` / existing `STATUS_TOKEN` | Current deployment, or `deployment: null` (also for an unknown node) |
| POST `/start` | `X-Ingest-Token` / existing `INGEST_TOKEN` | Creates the node if needed and opens its deployment; 409 if one is already active |
| POST `/change` | `X-Ingest-Token` | Atomically ends the current deployment and opens a new one |
| POST `/end` | `X-Ingest-Token` | Ends the current deployment without deleting it |

Example start body:

```json
{
  "name": "Home Office",
  "location_label": "Capitol Hill, Seattle",
  "latitude": 47.62,
  "longitude": -122.32,
  "altitude_m": null,
  "notes": "On the desk",
  "metadata": {}
}
```

Only `name` is required for start. `started_at` may be an explicit timezone-aware
ISO timestamp; otherwise PostgreSQL server time is used. Coordinates must be finite
and in range. Optional fields default to null (metadata defaults to `{}`).

Change takes the same fields plus mandatory `expected_deployment_id`, set to the
active ID returned by GET. End takes `expected_deployment_id` and optional
timezone-aware `ended_at` (server time by default). The expected ID prevents stale
pages from changing or ending someone else's replacement deployment.

Responses have `ok`, `node_id`, and `deployment`. An active deployment includes
`id`, `node_id`, name/location/coordinates/altitude, notes/metadata, `started_at`,
`ended_at` (null while open), and `created_at`. End/change also return
`ended_deployment`. End returns `deployment: null`.

Management operations serialize on the node row. Change closes the old row at
the new start time and creates the new row in the same transaction. History is
retained. Conflicts return 409; invalid inputs return 422. Future timestamps,
end times before the current start, and overlaps with already closed history
are rejected. These operations describe current placement, not scheduled moves.

Existing ingestion behavior is unchanged: each arriving batch resolves the
currently open deployment once, and newly inserted buckets receive that ID.
Subsequent batches after an end receive null until another deployment starts.
An already in-flight batch keeps its resolved association; a duplicate bucket
keeps its originally stored ID. Association is based on ingestion state, not a
historical lookup using the bucket timestamp. Management does not backfill or
rewrite previously stored buckets.

## Electric Sea dashboards

Electric Sea injects a shared control into its proxied `/electric-sky/` and
`/indoor-sky/` pages, including the cached indoor page. Firmware remains unchanged.
The browser calls a narrow same-origin router proxy so an HTTPS dashboard does
not have to contact the HTTP LAN API or receive the ingestion credential.

Configure on the router (using its existing environment/`.env`):

```dotenv
WEATHER_BRAIN_URL=http://WEATHER_BRAIN_LAN_HOST:8000
WEATHER_BRAIN_INGEST_TOKEN=YOUR_EXISTING_INGEST_TOKEN
WEATHER_BRAIN_STATUS_TOKEN=YOUR_EXISTING_STATUS_TOKEN
```

The operator enters that status token in the deployment control. It is kept only
in page memory, never embedded in public JavaScript or stored in local/session
storage. The proxy validates it, restricts node IDs to the two dashboards, checks
browser Origin, and supplies the ingestion token server-side for writes. This
explicitly grants holders of the configured status token deployment-management
access through this proxy. It does not grant access to arbitrary upstream routes.
No new secret type, deployment cache, or deployment state is introduced on the Pi.

The location button requests browser geolocation once. The operator can edit or
clear the coordinates because browser location may differ from sensor location.
Changing deployment creates a new history row; it does not edit past placement.

Restart Weather Brain for the new routes and Electric Sea for the control/proxy.
No real deployment is created automatically.

## Explicit historical backfill

Use the repository virtual environment with the configured `DATABASE_URL`:

```powershell
.venv/Scripts/python.exe scripts/backfill_deployment.py --node-id electric-sky --deployment-id DEPLOYMENT_UUID --start 2026-09-29T06:00:00Z --end 2026-09-29T07:00:00Z
```

This reports **Would change N** and does not update anything. Review the intended
node, deployment and times, then append `--apply` to execute in one transaction
and report **Changed N**. All four parameters are mandatory. Time bounds are
`start <= bucket_start < end`, with timezone-aware `start < end`. The destination
deployment must belong to the given node. Only `deployment_id IS NULL` rows in
that node/time range are updated; existing associations and other nodes are
untouched. Repeating an applied command safely changes zero already assigned rows.

An explicitly requested range may predate the administrative creation/start of
the destination deployment, allowing assignment of known earlier placement.
The command does not infer history or expand to all NULL records. A dry-run count
can differ from a later apply if ingestion adds matching records in between.

## Verification

`tests/test_deployments.py` tests auth/validation plus real PostgreSQL lifecycle,
concurrent-start exclusion, historical preservation, ingestion association and
scoped backfill. Set `TEST_DATABASE_URL` to run database cases; they create and
remove isolated test schemas, never modify production tables. Electric Sea uses
its existing `node:test` runner for proxy and lightweight browser-control tests.
