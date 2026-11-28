"""Moderation, for the owner. If a bad name reaches the leaderboard, the owner can give it a
neutral name straight away, without a restart, and lock it so the player can't change it
back. The lock is the name_locked column of the players table, so it survives a restart.
The owner can also announce an update to every open page.

These routes need the ADMIN_KEY environment variable, sent as "Authorization: Bearer <key>".
docs/moderation.md has the commands to use them.
"""
import datetime
import hmac
import math
import random
import time

import httpx
from starlette.responses import JSONResponse, PlainTextResponse

from bouncemarket import cards, config, db, leaderboard, players, role, state, streams
from bouncemarket.game import names
from bouncemarket.web import inputs


def admin_refusal(request):
    """None if the request carries the owner's key. Otherwise the response to send instead:
    "Not Found" if no ADMIN_KEY is set, just as for a page that doesn't exist, or a 401 for
    a missing or wrong key. compare_digest takes as long whatever the key sent, so the time
    a wrong guess takes gives away nothing about how close it was."""
    if not config.ADMIN_KEY:
        return PlainTextResponse("Not Found", status_code=404)
    given = request.headers.get("authorization", "")
    if not hmac.compare_digest(given.encode(), ("Bearer " + config.ADMIN_KEY).encode()):
        return JSONResponse({"error": "wrong or missing admin key."}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
    return None


def stored_name(wanted):
    """The name as the leaderboard has it, matching wanted ignoring capitals, or wanted itself
    if no name on the leaderboard matches (the database then has the last word)."""
    wanted = wanted.strip()
    for name in state.named_points:
        if name.lower() == wanted.lower():
            return name
    return wanted


def made_up_name():
    """A neutral name nobody has, such as player-4821, or None in the unlikely case that every
    one we try is taken."""
    taken = {name.lower() for name in state.named_points}
    for attempt in range(100):
        name = f"player-{random.randint(1000, 99999)}"
        if name not in taken:
            return name
    return None


async def change_player_by_name(name, changes):
    """Write changes to the database row of the player with this exact name. Returns the rows
    changed, as they are now: one, or none if nobody has that name."""
    response = await db.client.patch(
        "/players",
        params={"name": "eq." + name},
        json=changes,
        headers={"Prefer": "return=representation"},
    )
    response.raise_for_status()
    return response.json()


def moderated(player_id, row):
    """After the owner changed a player's row, bring the player in memory up to date, if they
    are there, and send their open pages their new "me" message."""
    player = state.players.get(player_id)
    if player is None:
        return
    if player["loaded"] and player["name"]:
        player["name"], player["name_locked"] = row["name"], bool(row.get("name_locked", False))
    else:
        # Their lookup failed, or may still be on its way back with the row as it was before.
        # Use the row as it is now, and mark them loaded so an older answer is ignored.
        players.use_saved_row(player_id, row)
    streams.send_to_player(player_id, streams.sse("me", player))


async def admin_names(request):
    """Every named player, most points first, with whether their name is locked: so the owner
    can look over names beyond the top 10. The points are the server's own, which may be newer
    than the database's. "lock_column" says whether the database has the name_locked column
    yet (null if there are no players to tell from)."""
    refusal = admin_refusal(request)
    if refusal:
        return refusal
    try:
        rows = await db.read_all("*")  # "*" so this works before the name_locked column exists
    except httpx.HTTPError as error:
        return JSONResponse({"error": "couldn't read the players. " + db.database_problem(error)}, status_code=503)
    listing = [
        {
            "name": row["name"],
            "points": state.named_points.get(row["name"], row["points"]),
            "name_locked": bool(row.get("name_locked", False)),
        }
        for row in rows
    ]
    listing.sort(key=lambda row: (-row["points"], row["name"]))
    lock_column = ("name_locked" in rows[0]) if rows else None
    return JSONResponse({"players": listing, "count": len(listing), "lock_column": lock_column})


async def admin_rename(request):
    """Give a player a neutral name and lock it. Send {"name": <their name now>} and, if you
    like, "to": the new name; without one the server makes one up, such as player-4821.
    Their points stay with them.

    It works whether or not the player has the page open: the database row is changed first,
    by name (ignoring capitals), and only if that works is the server's memory changed to match.
    """
    refusal = admin_refusal(request)
    if refusal:
        return refusal
    data = await inputs.read_json(request)
    wanted, to = data.get("name"), data.get("to")
    if not isinstance(wanted, str) or not wanted.strip():
        return JSONResponse({"error": 'say whose name to change, as "name".'}, status_code=400)
    if to is not None:
        if not isinstance(to, str):
            return JSONResponse({"error": '"to" must be text.'}, status_code=400)
        to = to.strip()
        problem = names.name_problem(to)
        if problem:
            return JSONResponse({"error": problem}, status_code=400)
    if not state.game["ranking_loaded"]:
        # Until then the startup load could still put the old name back on the leaderboard.
        return JSONResponse({"error": "the leaderboard hasn't loaded from the database yet. try again in a minute."}, status_code=503)

    async with state.saving_players:  # so no round's points are saved under the old name meanwhile
        if state.game["role"] != "live":
            return role.moved(request)  # the game was handed to a new server while we waited our turn
        old_name = stored_name(wanted)
        for attempt in range(5):
            new_name = to if to is not None else made_up_name()
            if new_name is None:
                return JSONResponse({"error": 'couldn\'t make up a free name. give one as "to".'}, status_code=503)
            try:
                rows = await change_player_by_name(old_name, {"name": new_name, "name_locked": True})
                break
            except httpx.HTTPError as error:
                taken = isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 409
                if taken and to is None:
                    continue  # someone has the name we made up, though we didn't know: make up another
                if taken:
                    return JSONResponse({"error": "that username is taken."}, status_code=409)
                return JSONResponse({"error": "nothing has changed. " + db.database_problem(error)}, status_code=503)
        else:
            return JSONResponse({"error": 'couldn\'t find a free name. give one as "to".'}, status_code=503)
        if not rows:
            return JSONResponse({"error": "nobody is called that."}, status_code=404)
        row = rows[0]
        points = state.named_points.pop(old_name, row["points"])
        state.named_points[new_name] = points
        state.saved_ids.add(row["id"])
        moderated(row["id"], row)
    leaderboard.publish_leaderboard()
    cards_renamed = await cards.rename_cards(row["id"], new_name)
    return JSONResponse({"renamed": old_name, "to": new_name, "points": points, "name_locked": True, "cards": cards_renamed})


async def admin_unlock(request):
    """Lift the lock on a name, so its player can change it again. Send {"name": <their name>}."""
    refusal = admin_refusal(request)
    if refusal:
        return refusal
    data = await inputs.read_json(request)
    wanted = data.get("name")
    if not isinstance(wanted, str) or not wanted.strip():
        return JSONResponse({"error": 'say whose name to unlock, as "name".'}, status_code=400)
    async with state.saving_players:
        if state.game["role"] != "live":
            return role.moved(request)  # the game was handed to a new server while we waited our turn
        name = stored_name(wanted)
        try:
            rows = await change_player_by_name(name, {"name_locked": False})
        except httpx.HTTPError as error:
            return JSONResponse({"error": "nothing has changed. " + db.database_problem(error)}, status_code=503)
        if not rows:
            return JSONResponse({"error": "nobody is called that."}, status_code=404)
        moderated(rows[0]["id"], rows[0])
    return JSONResponse({"unlocked": rows[0]["name"], "name_locked": False})


MAX_NOTICE_MINUTES = 24 * 60  # the furthest ahead an update can be announced


async def admin_notice(request):
    """Announce a system update to everyone. Send {"minutes": N}: every page shows a banner
    counting down N minutes, telling guests their points may be lost and to choose a username
    to keep them. Send {"minutes": 0} to take the banner down. Sending a new number restarts it.

    It lives in memory only, so a restart (which the update itself usually is) takes it down.
    Once the time is up the banner says the update is starting now, until it is taken down.
    """
    refusal = admin_refusal(request)
    if refusal:
        return refusal
    data = await inputs.read_json(request)
    minutes = data.get("minutes")
    # type() rather than isinstance(), because True and False count as numbers to isinstance().
    if type(minutes) not in (int, float) or not math.isfinite(minutes) or not 0 <= minutes <= MAX_NOTICE_MINUTES:
        return JSONResponse(
            {"error": f'"minutes" must be a number from 0 to {MAX_NOTICE_MINUTES}. 0 takes the notice down.'},
            status_code=400,
        )
    if minutes == 0:
        state.game["update_at"] = None
        return JSONResponse({"notice": None})
    state.game["update_at"] = time.time() + minutes * 60
    update_at = datetime.datetime.fromtimestamp(state.game["update_at"], datetime.timezone.utc).isoformat(timespec="seconds")
    return JSONResponse({"notice": "published", "minutes": minutes, "update_at": update_at})
