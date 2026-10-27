begin;
create table if not exists deployment_corrections (
  id uuid primary key default gen_random_uuid(),
  node_id text not null references nodes(id),
  preview_token text not null unique,
  corrected_at timestamptz not null default clock_timestamp(),
  reason text not null check (length(trim(reason)) > 0),
  operator_identity text,
  request jsonb not null,
  preview jsonb not null,
  changed_counts jsonb not null,
  rebuild_start timestamptz not null,
  rebuild_end timestamptz not null,
  rebuild_next timestamptz not null,
  rebuild_status text not null default 'pending'
    check (rebuild_status in ('pending', 'failed', 'complete')),
  rebuild_error text,
  rebuilt_windows integer not null default 0,
  rebuilt_at timestamptz
);
create index if not exists deployment_corrections_pending
  on deployment_corrections(corrected_at) where rebuild_status <> 'complete';
commit;
