"""The handover. An update starts a new server beside the old one, and for a minute or so both
run: the host (Render) sends every new request to the new server once it is up, while the
pages' open streams stay with the old one. The game lives in one server's memory, so it has to
move from the old to the new, at that moment, or a page would watch the ball on one server
and buy on the other. It goes like this:

1. The new server, before it takes any requests, says hello at the site's own address
   (PUBLIC_URL). No request reaches it yet, so the old server answers. The new one now knows
   there is a game to wait for (its role is "waiting"), and the old one starts watching.
2. The old server asks the same address "who are you?" twice a second. The first time the new
   server answers, the host has switched over.
3. The old server stops changing anything ("moving") and sends the new one everything that
   lives only in memory: the round being played, ball in the air and all, every player's
   points and shares, the leaderboard, and whatever isn't saved yet (handover_snapshot()).
4. The new server takes it and carries on from the same frame (take_over()). Requests that
   reached it before this were kept waiting, a second at most, and are answered now.
5. The old server ends every page's stream ("retired"). Each browser connects again by
   itself, which reaches the new server, and the ball carries on from where it was.

If any step fails, the game is never left without a server: the old one carries on until the
host shuts it down, when it tries once more, and a new server that hears nothing starts a game
of its own, into which a late handover is merged. The worst case is what an update used to
be: a new game, and guests back on 10 points. See docs/deploys.md.
"""
import asyncio
import contextlib
import hmac
import json
import time
import zlib

import httpx
from starlette.responses import JSONResponse, PlainTextResponse

from bouncemarket import broadcast, config, leaderboard, players, role, rounds, state, streams
from bouncemarket.game import RULES_VERSION, counterparty

# How the old and the new server talk during an update: each asks the site's own address, which
# reaches whichever of them is taking requests just then (see above). The key
# is worked out from the database key, which both have, so there is nothing more to set up, and
# the database key itself is never sent. No connection is kept open between requests, so each
# one is sure to be routed afresh.
HANDOVER_KEY = hmac.new(config.SUPABASE_KEY.encode(), b"bouncemarket handover", "sha256").hexdigest()
peer = httpx.AsyncClient(
    base_url=config.PUBLIC_URL or "http://localhost",  # never used without a PUBLIC_URL
    headers={"Authorization": "Bearer " + HANDOVER_KEY},
    timeout=3,
    limits=httpx.Limits(max_keepalive_connections=0),
)
HANDOVER_FORMAT = 1  # change it whenever handover_snapshot() changes what it sends in a way take_over() can't read

# What is handed over of the round being played, or of the wait for the next one.
ROUND_KEYS = (
    "dropping", "drops_at", "started_at", "start_x", "path", "bucket", "close_frame",
    "commit", "gravity", "bounce", "nudge", "nudge_seed", "sent_frames", "shares", "buys", "broadcasts", "buy_timing", "last_bucket",
)

# What a round handed over by a server from before these keys existed had instead: its drops
# were played without nudges.
ROUND_KEYS_BEFORE = {"nudge": 0.0, "nudge_seed": None}


def go_live():
    """Start running the game on this server, from wherever the game is."""
    role.set_role("live")
    state.game_tasks.extend(asyncio.create_task(job) for job in (rounds.run_rounds(), broadcast.broadcast()))


def start_afresh():
    """Start a game of our own, with a full countdown: there was no server before us to take
    one from, or it never handed its game over."""
    state.game["drops_at"] = time.time() + config.COUNTDOWN_SECONDS
    go_live()


def end_stream(queue, parting):
    """End one page's stream, after a last message and telling its browser when to connect
    again. Whatever was still waiting to be sent is dropped, as the new server sends everything again."""
    while not queue.empty():
        queue.get_nowait()
    queue.put_nowait((parting, None))
    queue.put_nowait((role.come_back(), None))
    queue.put_nowait(None)  # events() ends the stream when it reaches this


def ours(handler):
    """Wraps every route that reads or changes the game, so it only runs on the server that
    has the game. On a new server still waiting for it, or an old one just handing it over, the
    request waits, as that takes well under a second. If a new server has waited HOLD_SECONDS
    and been handed nothing, it starts a game of its own."""
    async def gated(request):
        # Read what was sent first, so that nothing is waited for between the check below and
        # the handler's own work: a buy can't slip in after the game has been handed over.
        await request.body()
        if state.game["role"] in ("waiting", "moving"):
            try:
                await asyncio.wait_for(state.role_settled.wait(), config.HOLD_SECONDS)
            except TimeoutError:
                if state.game["role"] == "waiting":
                    print("Nobody handed the game over, so starting a new one.")
                    start_afresh()
        if state.game["role"] != "live":
            return role.moved(request)
        return await handler(request)
    return gated


def handover_refusal(request):
    """None if the request comes from our own other server, which sends HANDOVER_KEY.
    Otherwise "Not Found", just as for a page that doesn't exist."""
    given = request.headers.get("authorization", "")
    if not hmac.compare_digest(given.encode(), ("Bearer " + HANDOVER_KEY).encode()):
        return PlainTextResponse("Not Found", status_code=404)
    return None


def other_server(answer):
    """Whether an answer from the site's address, {"instance": ...}, came from a server other than this one."""
    return isinstance(answer, dict) and answer.get("instance") not in (None, role.INSTANCE)


async def health(request):
    """Says this server is up, and which one it is. Render asks, to know when a new server is
    ready for requests, and the old server asks during an update, to know when the new one has
    started getting them (see watch_for_successor())."""
    return JSONResponse({"instance": role.INSTANCE}, headers={"Cache-Control": "no-store"})


async def find_predecessor():
    """When this server starts, before it takes requests: ask at the site's address whether a
    server is running the game. Returns its name, or None if there isn't one, which is also the
    answer when it is too old to know about handovers, or can't be reached in three tries.

    A server that is itself still waiting for the game counts: with two updates close together
    it is the one that will have the game by the time requests come to us, and it hears of us
    when we say hello again (see keep_saying_hello())."""
    if not config.PUBLIC_URL:
        return None
    for attempt in range(3):
        try:
            response = await peer.post("/handover/hello", json={"instance": role.INSTANCE})
            if response.status_code < 500:
                answer = response.json() if response.status_code == 200 else None
                if other_server(answer) and answer.get("role") in ("live", "moving", "waiting"):
                    return answer["instance"]
                return None
        except (httpx.HTTPError, ValueError) as error:
            print("Could not ask for the server before us:", repr(error))
        await asyncio.sleep(1)
    return None


async def keep_saying_hello():
    """On a new server, for as long as it waits for the game: say hello again every
    HELLO_SECONDS. The server that ends up handing us the game may not be the one that heard
    us the first time. With two updates close together, it is the server of the update before
    ours, which was itself still waiting when we started."""
    while state.game["role"] == "waiting":
        await asyncio.sleep(config.HELLO_SECONDS)
        if state.game["role"] == "waiting":
            with contextlib.suppress(httpx.HTTPError):
                await peer.post("/handover/hello", json={"instance": role.INSTANCE})


async def handover_hello(request):
    """A new server, just started and not yet taking requests, asks who is running the game.
    If that's us, we start watching for the moment it does take them."""
    refusal = handover_refusal(request)
    if refusal:
        return refusal
    if state.game["role"] == "live" and (state.watching is None or state.watching.done()):
        print("A new server has started. Watching for it to take over.")
        state.watching = asyncio.create_task(watch_for_successor())
    return JSONResponse({"instance": role.INSTANCE, "role": state.game["role"]})


async def watch_for_successor():
    """On the old server, once a new one has said hello: ask the site's address who answers
    there, every PROBE_SECONDS. While that is still us, requests are still coming to us. The
    first time it's another server, the host has switched over, and we hand the game on."""
    deadline = time.monotonic() + config.WATCH_SECONDS
    tries = 0
    while state.game["role"] == "live" and tries < config.HAND_OVER_TRIES and time.monotonic() < deadline:
        await asyncio.sleep(config.PROBE_SECONDS)
        try:
            answer = (await peer.get("/health")).json()
        except (httpx.HTTPError, ValueError):
            continue
        if other_server(answer):
            tries += 1
            if await hand_over(config.HAND_OVER_SECONDS) != "unreachable":
                return


def handover_snapshot():
    """Everything the next server needs to carry on the game exactly where it is.

    The players sent are everyone with a page open, everyone holding shares in this round,
    every named player, and the guests who have played and were last here within
    GUEST_KEPT_SECONDS. A visitor who only looked, and left, is the same made afresh. A guest
    who left longer ago is forgotten here, as every guest used to be at every update:
    otherwise the players would be handed from server to server for ever, and only grow.
    Times are the clock's own (seconds since 1970), which both servers' clocks agree on
    closely enough.
    """
    online = set(state.streams.values())
    now = time.time()

    def kept(player_id, player):
        if player_id in online or player["name"] or player["spent"] or any(player["shares"].values()):
            return True
        played = player["points"] != 10 or player["last_won"]
        return played and now - player["seen"] < config.GUEST_KEPT_SECONDS

    return {
        "format": HANDOVER_FORMAT,
        "from": role.INSTANCE,
        "rules": RULES_VERSION,
        "game": {key: state.game[key] for key in ROUND_KEYS},
        "players": {player_id: player for player_id, player in state.players.items() if kept(player_id, player)},
        "named_points": state.named_points if state.game["ranking_loaded"] else None,
        "unsaved_rounds": state.unsaved_rounds,
        "unsaved_cards": state.unsaved_cards,
    }


async def hand_over(seconds):
    """Hand the game to the new server, trying for up to this many seconds. Returns "taken",
    "refused" (it answered, but can't take this game: it reads a different HANDOVER_FORMAT),
    or "unreachable". Unless it was taken, the game carries on here.

    Both saving locks are held throughout, so no change of name or deleted card is half-way
    to the database as the game moves: what is sent is everything, and nothing changes here
    after it. Requests that arrive meanwhile wait (see ours()), and so does the round loop.
    """
    async with state.saving_players, state.saving_cards:
        if state.game["role"] != "live":
            return "unreachable"
        role.set_role("moving")
        outcome = "unreachable"
        try:
            body = zlib.compress(json.dumps(handover_snapshot()).encode())
            deadline = time.monotonic() + seconds
            while outcome == "unreachable" and time.monotonic() < deadline:
                try:
                    response = await peer.post("/handover/take", content=body)
                    answer = response.json()
                except (httpx.HTTPError, ValueError):
                    answer = None
                if other_server(answer):
                    outcome = "taken" if response.status_code == 200 and answer.get("took") else "refused"
                else:
                    await asyncio.sleep(0.25)  # the request came back to us, or nowhere: the switch isn't complete yet
        except BaseException as error:
            # Stopped part-way (the server is shutting down), or something that shouldn't go
            # wrong did. The game is still ours, and mustn't be left frozen.
            print("Could not hand the game over:", repr(error))
            role.set_role("live")
            raise
        if outcome != "taken":
            print(f"Could not hand the game over: {outcome}.")
            role.set_role("live")
            return outcome
        print(f"Handed the game to the new server, {answer['instance']}, with {len(state.streams)} pages open.")
        role.set_role("retired")
        # The new server saves these now. Writing them from here as well could undo something
        # a player does there, such as deleting a card.
        state.unsaved_rounds.clear()
        state.unsaved_cards.clear()
        parting = broadcast.parting_message()
        for queue in state.streams:
            end_stream(queue, parting)
        return outcome


async def handover_take(request):
    """The old server hands us the game, as made by handover_snapshot() and squeezed with zlib."""
    refusal = handover_refusal(request)
    if refusal:
        return refusal
    try:
        handed = json.loads(zlib.decompress(await request.body()))
    except (zlib.error, ValueError):
        return JSONResponse({"error": "couldn't read that."}, status_code=400)
    readable = isinstance(handed, dict) and handed.get("format") == HANDOVER_FORMAT
    # Not from ourselves (the old server's request can come back to it, mid-switch), and not
    # if we are the old server, with a game of our own on its way out.
    if not readable or handed.get("from") == role.INSTANCE or state.game["role"] not in ("waiting", "live"):
        return JSONResponse({"instance": role.INSTANCE, "took": False}, status_code=409)
    if handed["from"] not in state.taken_from:  # the old server sends it again if our answer got lost
        state.taken_from.add(handed["from"])
        take_over(handed)
    return JSONResponse({"instance": role.INSTANCE, "took": True})


def take_over(handed):
    """Carry on the game the old server handed us.

    Normally we have been waiting for it, with no game of our own. Then everything is taken as
    it is, the round being played included, and the round loop starts from there: the same
    ball, at the same frame, with the same shares. The counterparty isn't sent. It is made
    again from the path and the buys, which always gives the same counterparty (see Counterparty).

    Their round is called off, and everyone's shares in it refunded, in two cases: if we play
    by different rules (RULES_VERSION), as a round can't change rules half-way, or if we gave
    up waiting and have a game of our own running. In that second case the players are merged
    into ours: a guest who has already come to us started here on 10 points, so the points
    they had with the old server take the place of those 10. A named player we already hold
    was loaded from the database, where their points are saved after every round.
    """
    theirs = handed["game"]
    waiting = state.game["role"] == "waiting"
    carry_on = waiting and theirs["dropping"] and handed["rules"] == RULES_VERSION
    for player_id, carried in handed["players"].items():
        player = players.new_player()
        player.update({key: carried[key] for key in player if key in carried})
        if not carry_on:
            player["points"] = round(player["points"] + player["spent"], 2)
            player["shares"], player["spent"] = {"left": 0, "right": 0}, 0
        mine = state.players.get(player_id)
        if mine is None:
            state.players[player_id] = player
        elif mine["name"] is None and player["name"] is None:
            mine["points"] = round(mine["points"] + player["points"] - 10, 2)
        if player["name"]:
            state.saved_ids.add(player_id)

    known = {log["started_at"] for log in state.unsaved_rounds}
    state.unsaved_rounds.extend(log for log in handed["unsaved_rounds"] if log["started_at"] not in known)
    known = {card["share_id"] for card in state.unsaved_cards}
    for card in handed["unsaved_cards"]:
        if card["share_id"] not in known:
            state.unsaved_cards.append(card)
        # A card this new isn't in the database yet, where get_best_round() would look for it.
        best = state.best_rounds.get(card["player"])
        if not card["cleared"] and (best is None or card["profit"] > best["profit"]):
            state.best_rounds[card["player"]] = card
            state.card_holders.add(card["player"])

    if not waiting:
        print(f"Merged {len(handed['players'])} players from the old server, {handed['from']}, into the game already running here.")
        for queue, player_id in state.streams.items():
            streams.send(queue, streams.sse("me", state.players[player_id]))
        leaderboard.publish_leaderboard()
        return
    if handed["named_points"] is not None:
        # The old server's leaderboard is newer than what we read from the database as we started.
        state.named_points.clear()
        state.named_points.update(handed["named_points"])
    if carry_on:
        state.game.update({key: theirs[key] if key in theirs else ROUND_KEYS_BEFORE[key] for key in ROUND_KEYS})
        state.game["counterparty"] = counterparty.Counterparty(state.game["path"], state.game["close_frame"], state.game["start_x"])
    else:
        state.game["last_bucket"] = theirs["last_bucket"]
        # Between rounds, their countdown carries on. A round called off starts a new countdown.
        state.game["drops_at"] = time.time() + config.COUNTDOWN_SECONDS if theirs["dropping"] else max(theirs["drops_at"], time.time())
    outcome = "carrying on its round" if carry_on else "its round called off" if theirs["dropping"] else "between rounds"
    print(f"Took over the game from the old server, {handed['from']}: {len(handed['players'])} players, {outcome}.")
    go_live()
    leaderboard.publish_leaderboard()
