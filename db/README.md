# The database

A [Supabase](https://supabase.com) project, which the server reaches over its web API (`bouncemarket/db.py`). The pages never talk to it.

`schema.sql` makes everything the server uses. Run it in the Supabase SQL editor. It only adds what is missing, so run it again whenever it changes, and before pushing the code that needs the change.

| Table | Holds | Written by |
| --- | --- | --- |
| `players` | Every player who has claimed a name: their id, name, points, and whether the owner has locked the name. | `bouncemarket/players.py` |
| `rounds` | The round log: one row for every round that someone bought in. See [The round log](../docs/round-log.md). | `bouncemarket/round_log.py` |
| `player_keys` | A small number for every player who has ever bought, used in the round log in place of their long id. | `bouncemarket/round_log.py` |
| `best_rounds` | Every best-round card ever made. Rows are never removed, so old share links keep working. | `bouncemarket/cards.py` |

Every table has row level security turned on and no policies, so only the server's secret key can read or write it.

A player's id works like a password: whoever has it is that player. It is in `players`, `player_keys` and `best_rounds`, so treat a copy of any of them as secret.
