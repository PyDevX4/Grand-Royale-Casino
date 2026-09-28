-- Grand Royale Casino online accounts. This goes in the SAME Supabase project as Cube Shooter.
-- Paste all of this into Supabase: SQL Editor -> New query -> Run. Running it again is safe.

-- One row per casino player: their username and saved progress (chips, stocks, trophies, stats...)
create table if not exists public.casino_players (
  id uuid primary key references auth.users (id) on delete cascade,
  username text not null,
  progress jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

-- Usernames are unique no matter the upper/lower case
create unique index if not exists casino_players_username_key on public.casino_players (lower(username));

-- Row Level Security: a player can only ever read and change their own row
alter table public.casino_players enable row level security;

drop policy if exists "read own row" on public.casino_players;
create policy "read own row" on public.casino_players
  for select using (auth.uid() = id);

drop policy if exists "create own row" on public.casino_players;
create policy "create own row" on public.casino_players
  for insert with check (auth.uid() = id);

drop policy if exists "update own row" on public.casino_players;
create policy "update own row" on public.casino_players
  for update using (auth.uid() = id) with check (auth.uid() = id);

drop policy if exists "delete own row" on public.casino_players;
create policy "delete own row" on public.casino_players
  for delete using (auth.uid() = id);

-- Players can only write their own username and progress (nothing else)
revoke insert, update on public.casino_players from authenticated, anon;
grant insert (id, username, progress) on public.casino_players to authenticated;
grant update (progress, updated_at) on public.casino_players to authenticated;
grant select, delete on public.casino_players to authenticated;

-- Lets the game say "that username is taken" before signing up, without showing anyone's data
create or replace function public.casino_username_taken(name text)
returns boolean
language sql
security definer
set search_path = public
as $$
  select exists (select 1 from public.casino_players where lower(username) = lower(name));
$$;
grant execute on function public.casino_username_taken(text) to anon, authenticated;

-- Lets a logged-in player delete their own account from Settings.
-- (Cube Shooter's setup already made this; it's repeated here so this script works on its own.)
-- Casino accounts use their own logins, so deleting one never touches a Cube Shooter account.
create or replace function public.delete_my_account()
returns void
language sql
security definer
set search_path = public, auth
as $$
  delete from auth.users where id = auth.uid();
$$;
revoke execute on function public.delete_my_account() from anon;
grant execute on function public.delete_my_account() to authenticated;
