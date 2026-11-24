-- Run this once in the Supabase SQL editor to create the table the server uses.

create table players (
  id text primary key,  -- the random id the player's browser remembers; treat it like a password
  name text not null check (char_length(name) between 1 and 16),
  points numeric not null default 10
);

-- Names are unique, ignoring capitals, so "Sam" and "sam" can't both exist.
create unique index players_name_unique on players (lower(name));

-- Makes the leaderboard query (the highest points first) fast.
create index players_points on players (points desc);

-- Row level security with no policies: only the server's secret key can read or write this
-- table. The public key that browsers could use gets nothing.
alter table players enable row level security;
