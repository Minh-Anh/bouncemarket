"""The round log. Every round that at least one player bought in becomes one row of the
"rounds" table: where the ball dropped from, its path, where it landed, and every buy.
packing.py packs the path and the buys, and analysis/ reads the log back. See docs/round-log.md.
"""
import asyncio
import datetime
import re

import httpx

from bouncemarket import config, db, packing, state
from bouncemarket.game import RULES_VERSION

# Columns added to the rounds table after it was first made, each with the round log's keys
# that go in it. When the database says it doesn't have one (db/schema.sql not run since it was
# added), save_rounds() notes its keys in state.missing_round_columns and stops sending them,
# until the server restarts.
LATER_ROUND_COLUMNS = {"timing": ("timing",), "nudge": ("nudge", "nudge_seed"), "nudge_seed": ("nudge", "nudge_seed")}


def entry():
    """The round that just finished, as a row for the "rounds" table, or None if no player
    bought anything in it. Rounds nobody played aren't logged: they are most rounds on a quiet
    day, and the analysis can make as many drops of its own as it likes.

    "buys" is still the list buy() made, with each player's long id. save_rounds() packs it,
    once it knows every buyer's key.
    """
    if not state.game["buys"]:
        return None
    return {
        "started_at": datetime.datetime.fromtimestamp(state.game["started_at"], datetime.timezone.utc).isoformat(),
        "commit_id": state.game["commit"],
        "gravity": state.game["gravity"],
        "bounce": state.game["bounce"],
        "start_x": state.game["start_x"],
        "nudge": state.game["nudge"],
        "nudge_seed": state.game["nudge_seed"],
        "frames": len(state.game["path"]),
        "close_frame": state.game["close_frame"],
        "bucket": state.game["bucket"],
        "format": packing.LOG_FORMAT,
        "rules": RULES_VERSION,
        "path": db.to_bytea(packing.pack_path(state.game["path"])),
        "buys": state.game["buys"],
        # None if the timing doesn't line up with the buys, which shouldn't happen.
        "timing": db.to_bytea(packing.pack_timing(state.game["broadcasts"], state.game["buy_timing"]))
        if len(state.game["buy_timing"]) == len(state.game["buys"]) else None,
    }


async def save_rounds():
    """Write every round in unsaved_rounds to the database, in two steps.

    First, ask the database for the key of every buyer we don't know the key of yet. It makes
    up a new key for anyone it hasn't seen before, and hands back the old one for anyone it has.
    Then write the rounds, each with its buys packed. No two rounds start at the same moment,
    and the database skips a round it already has, so trying again after a failure that was
    really a success doesn't save a round twice.
    """
    logs = list(state.unsaved_rounds)  # more may finish while we wait for the database; they are saved next time
    new_ids = sorted({buy[2] for log in logs for buy in log["buys"]} - state.player_keys.keys())
    for start in range(0, len(new_ids), config.KEYS_PER_REQUEST):
        response = await db.client.post(
            "/player_keys",
            params={"on_conflict": "id"},
            json=[{"id": player_id} for player_id in new_ids[start:start + config.KEYS_PER_REQUEST]],
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        )
        response.raise_for_status()
        for row in response.json():
            state.player_keys[row["id"]] = row["key"]

    rows = []
    for log in logs:
        buys = [(frame, seen, state.player_keys[player_id], *rest) for frame, seen, player_id, *rest in log["buys"]]
        # A round handed over by a server from before the nudges has no nudge keys. The database
        # wants every row it is sent at once to have the same keys, so they are filled in: it
        # was played without nudges.
        row = {"nudge": 0.0, "nudge_seed": None, **log, "n_buys": len(buys), "buys": db.to_bytea(packing.pack_buys(buys))}
        rows.append(row)

    def without_missing(rows):
        return [{key: value for key, value in row.items() if key not in state.missing_round_columns} for row in rows]

    async def write(rows):
        return await db.client.post(
            "/rounds",
            params={"on_conflict": "started_at"},
            json=rows,
            headers={"Prefer": "resolution=ignore-duplicates"},
        )

    response = await write(without_missing(rows))
    # PGRST204: a column the database doesn't have. The rounds are saved without it rather than
    # not at all, until the server restarts after db/schema.sql has been run. A round saved without
    # its nudges can't be replayed exactly, and analysis/reader.py falls back on its saved path.
    while response.status_code == 400 and "PGRST204" in response.text:
        named = re.search(r"'(\w+)' column", response.text)
        keys = LATER_ROUND_COLUMNS.get(named.group(1)) if named else None
        if not keys or set(keys) <= state.missing_round_columns:
            break  # not a column we can do without: raise_for_status() below says what it was
        state.missing_round_columns.update(keys)
        print(f"The rounds table has no {named.group(1)} column: run db/schema.sql. Saving rounds without {', '.join(keys)} until then.")
        response = await write(without_missing(rows))
    response.raise_for_status()
    saved = {log["started_at"] for log in logs}
    state.unsaved_rounds[:] = [log for log in state.unsaved_rounds if log["started_at"] not in saved]


async def save_rounds_forever():
    """Save each logged round soon after it finishes, for as long as the server runs.

    If the database can't be reached, the rounds wait in unsaved_rounds (the newest
    UNSAVED_ROUNDS_KEPT of them) and we try again every 30 seconds.
    """
    while True:
        await asyncio.sleep(1)
        if not state.unsaved_rounds:
            continue
        try:
            await save_rounds()
        except (httpx.HTTPError, KeyError) as error:  # KeyError: the database didn't hand back a key we asked for
            print("Could not save the round log, trying again in 30 seconds:", repr(error))
            await asyncio.sleep(30)
