-- Daily Brief database. Run once in the Supabase SQL editor.
-- Tables are prefixed "dailybrief_" so they can live in an existing project.

create table if not exists public.dailybrief_stories (
  id         text primary key,
  title      text   not null,
  url        text   not null,
  summary    text   not null default '',
  image      text   not null default '',
  ts         bigint not null,              -- published time (unix seconds)
  seen       bigint not null,              -- first collected (unix seconds)
  source     text   not null,
  region     text   not null,
  cat        text,                         -- null = left out (sport/entertainment)
  editorial  boolean not null default false,
  has_full   boolean not null default false,
  words      integer not null default 0,
  paras      jsonb  not null default '[]'::jsonb
);
create index if not exists dailybrief_stories_ts on public.dailybrief_stories (ts desc);

-- one row: when the scraper last ran, plus the section and portal names
create table if not exists public.dailybrief_runs (
  id         integer primary key default 1 check (id = 1),
  running    boolean not null default false,
  started    bigint  not null default 0,
  generated  bigint  not null default 0,
  meta       jsonb   not null default '{}'::jsonb
);

insert into public.dailybrief_runs (id) values (1) on conflict (id) do nothing;

-- the only accounts allowed to read (add your own user id after creating your login)
create table if not exists public.dailybrief_readers (
  user_id uuid primary key references auth.users (id) on delete cascade
);

alter table public.dailybrief_stories enable row level security;
alter table public.dailybrief_runs    enable row level security;
alter table public.dailybrief_readers enable row level security;

-- reader check lives in a schema the API does not expose
create schema if not exists private;
revoke all on schema private from public, anon;
grant usage on schema private to authenticated;

create or replace function private.dailybrief_is_reader()
returns boolean language sql stable security definer set search_path = '' as $$
  select exists (select 1 from public.dailybrief_readers r where r.user_id = (select auth.uid()));
$$;
revoke execute on function private.dailybrief_is_reader() from public, anon;
grant  execute on function private.dailybrief_is_reader() to authenticated;

drop policy if exists "readers can read stories" on public.dailybrief_stories;
create policy "readers can read stories" on public.dailybrief_stories
  for select to authenticated using ((select private.dailybrief_is_reader()));

drop policy if exists "readers can read runs" on public.dailybrief_runs;
create policy "readers can read runs" on public.dailybrief_runs
  for select to authenticated using ((select private.dailybrief_is_reader()));

-- No insert/update/delete policies: only the scraper's secret key can write.
-- dailybrief_readers has no policies at all, so nobody can read or change it from the page.
