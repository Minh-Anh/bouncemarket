"""Players: made the first time their browser's id is seen, and loaded from and saved to the
players table once they have claimed a name.
"""
import time

import httpx

from bouncemarket import db, state


def new_player():
    """A player as they are the first time we see their id: a guest with 10 points."""
    return {
        "name": None,
        "points": 10,
        "shares": {"left": 0, "right": 0},
        "spent": 0,
        "last_won": 0,
        "loaded": False,
        "name_locked": False,
        "seen": time.time(),
        "last_reset": 0,  # when they last reset their points (seconds since 1970), or 0 if never
    }


async def load_player(player_id):
    """Fetch a named player's row from the database, or None if this id never claimed a name.

    It asks for every column ("*") rather than naming them, so that it still works before the
    owner has added the name_locked column (see db/schema.sql): the row then just has no
    "name_locked", which counts as unlocked. Naming a missing column would make the database
    refuse every lookup, and no returning player could load their name.
    """
    response = await db.client.get("/players", params={"id": "eq." + player_id, "select": "*"})
    response.raise_for_status()
    rows = response.json()
    return rows[0] if rows else None


def use_saved_row(player_id, row):
    """Give a player in memory the name, points and lock from their database row."""
    player = state.players[player_id]
    player["name"], player["points"] = row["name"], row["points"]
    player["name_locked"] = bool(row.get("name_locked", False))  # missing until db/schema.sql adds the column
    player["loaded"] = True
    state.saved_ids.add(player_id)


async def save_players(rows):
    """Write rows of {"id", "name", "points"} to the database, replacing any with the same id."""
    if rows:
        response = await db.client.post("/players", json=rows, headers={"Prefer": "resolution=merge-duplicates"})
        response.raise_for_status()


async def get_player(player_id):
    """Look up a player, creating them with 10 points the first time we see their id.

    If they claimed a name before, the database has their name and points, and we load those.
    If the database can't be reached they can still play, without a name for now, and we
    look again the next time they connect. "loaded" records whether that lookup has worked.

    Most new ids are new visitors, who have never claimed a name. Once load_ranking() has read
    the whole players table, saved_ids holds every id that has a row, so for any other id we
    already know the answer is "no row" and don't ask. Until then (just after a restart, or
    while the database is down) we ask about every player, as there is no other way to know.
    A player who does have a row is still looked up here, the first time they connect: the
    database is always at least as up to date as what load_ranking() read, as the server
    writes every change there, and the lookup also brings their name_locked.
    """
    if player_id not in state.players:
        state.players[player_id] = new_player()
    player = state.players[player_id]
    if not player["loaded"] and state.game["ranking_loaded"] and player_id not in state.saved_ids:
        player["loaded"] = True
    if not player["loaded"]:
        try:
            saved = await load_player(player_id)
        except httpx.HTTPError as error:
            print("Could not load a player:", repr(error))
            return player
        # Another request for the same player may have finished first, or the owner may have
        # renamed them meanwhile (see admin_rename()). Either way the newer answer is already
        # in memory, and this one mustn't overwrite it.
        if not player["loaded"]:
            if saved:
                use_saved_row(player_id, saved)
            else:
                player["loaded"] = True
    return player
