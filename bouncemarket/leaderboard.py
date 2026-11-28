"""The leaderboard: every named player ranked by points, kept up to date in memory, and the
part of it each page is sent.
"""
import asyncio
import bisect

import httpx

from bouncemarket import config, db, state, streams


async def load_ranking():
    """Read every named player's id and points from the database, once, when the server starts.

    After that the server keeps the ranking up to date itself. It is the only thing that
    changes anyone's points, so it already knows every change without asking the database.
    If the database can't be reached, we keep trying every 30 seconds. Players who play in
    the meantime are ranked with their newer points, which the database rows don't overwrite.

    The ids go into saved_ids, so that from then on a new visitor's page can start without
    asking the database about them (see get_player()). They are added to what is already
    there, never put in its place: a player who claims a name while the pages are being read
    is added by set_name(), and their row may be too new to be on a page we have read.
    """
    while True:
        try:
            rows = await db.read_all("id,name,points")
        except httpx.HTTPError as error:
            print("Could not load the leaderboard, trying again in 30 seconds:", repr(error))
            await asyncio.sleep(30)
            continue
        for row in rows:
            player = state.players.get(row["id"])
            if player and player["loaded"]:
                # This player has loaded since the server started, so what we hold in memory is
                # newer than the row: they may have renamed themselves while we were reading,
                # and the row's old name mustn't come back onto the leaderboard.
                if player["name"]:
                    state.named_points.setdefault(player["name"], player["points"])
            else:
                state.named_points.setdefault(row["name"], row["points"])
        state.saved_ids.update(row["id"] for row in rows)
        state.game["ranking_loaded"] = True
        publish_leaderboard()
        return


def publish_leaderboard():
    """Rank the named players, most points first, and send each page its leaderboard.
    Players with the same points are ranked by name, so the order never shuffles."""
    ranking = [{"name": name, "points": points} for name, points in state.named_points.items()]
    ranking.sort(key=lambda row: (-row["points"], row["name"]))
    state.game["ranking"] = ranking
    state.game["positions"] = {row["name"]: i for i, row in enumerate(ranking)}
    state.game["minus_points"] = [-row["points"] for row in ranking]  # ascending, for finding where a guest fits
    for queue, player_id in state.streams.items():
        streams.send(queue, leaderboard_message(state.players[player_id]))


def leaderboard_message(player):
    """The leaderboard as one player sees it: the top LEADERBOARD_SIZE players and, if this
    player is ranked below them, the NEIGHBORS players either side of them as well.
    Every row carries its rank, counting from 1, and the player's own row is marked "me".

    Everyone is ranked, named or not. A guest (or a named player not in the ranking yet) is
    slotted in where their points would put them, after anyone with the same points, and
    the players below them move down one, just for them. Other guests are never shown.
    """
    ranking = state.game["ranking"]
    my_position = state.game["positions"].get(player["name"])
    slotted_in = my_position is None
    if slotted_in:
        my_position = bisect.bisect_right(state.game["minus_points"], -player["points"])
        mine = {"name": player["name"], "points": player["points"]}
    total = len(ranking) + slotted_in

    def row(i):
        """The row at index i of the ranking as this player sees it."""
        if slotted_in and i == my_position:
            entry = mine
        else:
            entry = ranking[i - 1 if slotted_in and i > my_position else i]
        return {"rank": i + 1, **entry, **({"me": True} if i == my_position else {})}

    def rows(start, end):
        return [row(i) for i in range(start, min(end, total))]

    around_me = []
    if my_position >= config.LEADERBOARD_SIZE:
        around_me = rows(max(config.LEADERBOARD_SIZE, my_position - config.NEIGHBORS), my_position + config.NEIGHBORS + 1)
    return streams.sse("leaderboard", {"top": rows(0, config.LEADERBOARD_SIZE), "around_me": around_me})
