# Moderating names

The word filter can't catch everything. If a bad name reaches the leaderboard, the owner can replace it straight away, with no restart: the server gives the player a neutral name and locks it, so they can't change it back. Their points stay with them. The lock is the `name_locked` column of the `players` table, so it lasts through restarts.

This needs `ADMIN_KEY` set on the server (see [Set up](../README.md#set-up)). The code is in `bouncemarket/web/admin.py`. Each request sends it as `Authorization: Bearer <key>`. A missing or wrong key gets `401`, and if `ADMIN_KEY` isn't set at all, the routes answer `404 Not Found`, as if they weren't there. In a terminal, first:

```
export ADMIN_KEY=the-key-you-set-on-render
export SITE=https://bouncemarket.minhanhday.com
```

Every named player with their points and whether their name is locked, most points first, so you can look beyond the top 10:

```
curl -s "$SITE/admin/names" -H "Authorization: Bearer $ADMIN_KEY"
```

It answers `{"players": [{"name": "Sam", "points": 42.5, "name_locked": false}, ...], "count": 1, "lock_column": true}`. `lock_column` says whether the database has the `name_locked` column yet (`null` if there are no players to tell from).

Replace a name with one the server makes up, such as `player-4821`, and lock it. The name is matched ignoring capitals, and the player doesn't need to be online:

```
curl -s -X POST "$SITE/admin/rename" -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" -d '{"name": "TheBadName"}'
```

Or choose the new name yourself with `"to"`. It must follow the same rules as any username:

```
curl -s -X POST "$SITE/admin/rename" -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" -d '{"name": "TheBadName", "to": "renamed1"}'
```

It answers `{"renamed": "TheBadName", "to": "player-4821", "points": 12.5, "name_locked": true, "cards": "renamed"}`. The new name is written to the database first; only if that works does the server change the leaderboard for everyone, and the player's own pages, at once. If the database write fails, nothing changes and the answer says why.

A rename also puts the new name on all the player's best-round cards, old ones included, so the bad name doesn't live on in share pages. `"cards"` says whether that worked; if it didn't, the rename itself still has, and renaming again retries it. Link previews that chat apps have already fetched may keep the old picture for up to a day.

Lift a lock, so the player can choose a name again:

```
curl -s -X POST "$SITE/admin/unlock" -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" -d '{"name": "player-4821"}'
```

The errors are `400` for a bad request or a `"to"` that breaks the username rules, `404` if nobody has the name, `409` if `"to"` is taken, and `503` if the database can't be reached or the server is still reading the leaderboard after a restart (try again in a minute).

The `name_locked` column must exist before these work: run `db/schema.sql` again (it only adds what is missing). If the new code runs before the column exists, the game itself is unaffected, as the server reads each player's row without naming the column and treats a missing one as "not locked". Only renaming and unlocking fail, with an answer saying to run `db/schema.sql`.

During a deploy, a command can reach the old server just as it hands the game over. It then answers `503` with `"moved": true` and has changed nothing: run the command again. If a handover fails (see [Updating without stopping the game](deploys.md#updating-without-stopping-the-game)), the previous server keeps running for a minute or so and may save a round's points under the old name, undoing a rename made meanwhile. If that happens, rename again once the deploy has finished.
