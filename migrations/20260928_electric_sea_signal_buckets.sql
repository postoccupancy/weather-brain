begin;

create table if not exists nodes (
  id text primary key,
  created_at timestamptz not null default now(),
  metadata jsonb not null default '{}'::jsonb
);

create table if not exists node_deployments (
  id uuid primary key default gen_random_uuid(),
  node_id text not null references nodes(id) on delete restrict,
  name text not null,
  location_label text,
  latitude double precision,
  longitude double precision,
  altitude_m double precision,
  started_at timestamptz not null default now(),
  ended_at timestamptz,
  notes text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  check (latitude is null or latitude between -90 and 90),
  check (longitude is null or longitude between -180 and 180),
  check (ended_at is null or ended_at >= started_at)
);

create unique index if not exists node_deployments_one_open_per_node
  on node_deployments (node_id)
  where ended_at is null;

create index if not exists node_deployments_node_started_at
  on node_deployments (node_id, started_at desc);

create table if not exists signal_buckets (
  node_id text not null references nodes(id) on delete restrict,
  deployment_id uuid references node_deployments(id) on delete restrict,
  signal_id text not null,
  bucket_start timestamptz not null,
  unit text,
  mean double precision not null,
  min double precision not null,
  max double precision not null,
  stddev double precision not null,
  sample_count integer not null check (sample_count > 0),
  metadata jsonb not null default '{}'::jsonb,
  received_at timestamptz not null default now(),
  primary key (node_id, signal_id, bucket_start),
  check (min <= mean and mean <= max),
  check (stddev >= 0)
);

create index if not exists signal_buckets_deployment_bucket_start
  on signal_buckets (deployment_id, bucket_start desc);

commit;
