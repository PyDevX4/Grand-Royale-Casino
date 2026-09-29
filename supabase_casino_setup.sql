-- Grand Royale Casino online accounts. This goes in the SAME Supabase project as Cube Shooter.
-- Paste all of this into Supabase: SQL Editor -> New query -> Run. Running it again is safe.

-- One row per casino player: their username and saved progress (chips, stocks, trophies, stats...)
create table if not exists public.casino_players (
  id uuid primary key references auth.users (id) on delete cascade,
  username text not null,
  progress jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

-- balance: the player's chips. You can edit it in Table Editor -> casino_players; the player's game picks up
-- your new number within about 30 seconds (or the next time they log in).
alter table public.casino_players add column if not exists balance bigint;
-- is_owner: tick it on your own row. Players can't change it.
alter table public.casino_players add column if not exists is_owner boolean not null default false;

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

-- Players can only write their own username, progress and balance - never is_owner
revoke insert, update on public.casino_players from authenticated, anon;
grant insert (id, username, progress, balance) on public.casino_players to authenticated;
grant update (progress, balance, updated_at) on public.casino_players to authenticated;
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

-- ============================================================================================================
-- Live events and themes: one shared row that every player's game checks every ~15 seconds.
-- Nobody can read or change the table directly - only through the functions below, and only an account with
-- is_owner = true can switch things on.
-- ============================================================================================================
create table if not exists public.casino_events (
  id int primary key default 1 check (id = 1),
  double_until timestamptz,
  rain_until timestamptz,
  jackpot_until timestamptz,
  theme text not null default '',
  updated_at timestamptz not null default now()
);
insert into public.casino_events (id) values (1) on conflict (id) do nothing;
alter table public.casino_events enable row level security;

-- Every game asks this: how many seconds each event has left, and the theme
create or replace function public.casino_get_events()
returns json
language sql
security definer
set search_path = public
as $$
  select json_build_object(
    'double', greatest(0, coalesce(extract(epoch from double_until - now()), 0)),
    'rain', greatest(0, coalesce(extract(epoch from rain_until - now()), 0)),
    'jackpot', greatest(0, coalesce(extract(epoch from jackpot_until - now()), 0)),
    'theme', theme)
  from public.casino_events where id = 1;
$$;
grant execute on function public.casino_get_events() to anon, authenticated;

create or replace function public.casino_is_owner()
returns boolean
language sql
security definer
set search_path = public
as $$
  select coalesce((select is_owner from public.casino_players where id = auth.uid()), false);
$$;

-- The owner menu: start an event for this many seconds (0 stops it)
create or replace function public.casino_owner_event(which text, seconds int)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if not public.casino_is_owner() then
    raise exception 'only the owner can do that';
  end if;
  if which = 'double' then
    update public.casino_events set double_until = now() + make_interval(secs => seconds), updated_at = now() where id = 1;
  elsif which = 'rain' then
    update public.casino_events set rain_until = now() + make_interval(secs => seconds), updated_at = now() where id = 1;
  elsif which = 'jackpot' then
    update public.casino_events set jackpot_until = now() + make_interval(secs => seconds), updated_at = now() where id = 1;
  else
    raise exception 'unknown event %', which;
  end if;
end;
$$;
revoke execute on function public.casino_owner_event(text, int) from anon, public;
grant execute on function public.casino_owner_event(text, int) to authenticated;

-- The owner menu: the theme for everyone ('' = none)
create or replace function public.casino_owner_theme(new_theme text)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if not public.casino_is_owner() then
    raise exception 'only the owner can do that';
  end if;
  update public.casino_events set theme = coalesce(new_theme, ''), updated_at = now() where id = 1;
end;
$$;
revoke execute on function public.casino_owner_theme(text) from anon, public;
grant execute on function public.casino_owner_theme(text) to authenticated;

-- ============================================================================================================
-- The owner menu's PLAYERS tab: see every player's chips and set them. Only is_owner accounts can use these.
-- The player's game picks up the new number within about 30 seconds (or the next time they log in).
-- ============================================================================================================
create or replace function public.casino_owner_players()
returns table (username text, balance bigint, updated_at timestamptz)
language plpgsql
security definer
set search_path = public
as $$
begin
  if not public.casino_is_owner() then
    raise exception 'only the owner can do that';
  end if;
  return query
    select p.username, p.balance, p.updated_at from public.casino_players p
    order by p.updated_at desc limit 300;
end;
$$;
revoke execute on function public.casino_owner_players() from anon, public;
grant execute on function public.casino_owner_players() to authenticated;

create or replace function public.casino_owner_set_balance(player text, amount bigint)
returns bigint
language plpgsql
security definer
set search_path = public
as $$
declare
  n int;
begin
  if not public.casino_is_owner() then
    raise exception 'only the owner can do that';
  end if;
  if amount is null or amount < 0 or amount > 1000000000000000 then
    raise exception 'that amount is not allowed';
  end if;
  update public.casino_players set balance = amount, updated_at = now() where lower(username) = lower(player);
  get diagnostics n = row_count;
  if n = 0 then
    raise exception 'there is no player called %', player;
  end if;
  return amount;
end;
$$;
revoke execute on function public.casino_owner_set_balance(text, bigint) from anon, public;
grant execute on function public.casino_owner_set_balance(text, bigint) to authenticated;
