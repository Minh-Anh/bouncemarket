"""What a player's page asks of the server: its stream of messages, and to buy a share, set a
name, reset its points and delete its card.
"""
import asyncio
import collections
import math
import time

import httpx
from starlette.responses import JSONResponse, StreamingResponse

from bouncemarket import broadcast, cards, config, db, leaderboard, limits, market, packing, players, role, state, streams
from bouncemarket.game import names
from bouncemarket.web import inputs


class PageStream(StreamingResponse):
    """A page's stream, which gives its slot back when it is over: when the page goes away,
    when the game moves to a new server, and also if the page went away before the stream
    had even started, when nothing inside the stream itself would get to run."""

    def __init__(self, content, address, **kwargs):
        super().__init__(content, **kwargs)
        self.address = address

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            limits.give_back_slot(self.address)


async def events(request):
    """Stream messages to one player's page for as long as it stays open.

    The page gets a private "me" message (its name, points and shares) when it connects and
    after each round, the public "game" message when it connects (see catch_up()) and then
    from broadcast(), and its own "leaderboard"
    message (the top players, plus the players around it) when it connects and whenever the
    leaderboard is refreshed. If the player has a best-round card, a "card" message brings it
    when the page connects, and again whenever a round becomes their new best.

    Only so many pages can be open at once, and so many from one address: see take_slot().
    And only so many new guests can come from one address in an hour: see admit_new_guest().
    """
    player_id = inputs.parse_id(request.query_params.get("player"))
    if player_id is None:
        return JSONResponse({"error": "bad player id."}, status_code=400)
    address = limits.client_address(request)
    refusal = limits.take_slot(address)
    if refusal:
        return refusal
    try:
        new = player_id not in state.players
        player = await players.get_player(player_id)
        # Someone we have never seen, and who has no saved name: a new guest, with 10 free points.
        if new and player["name"] is None and not limits.admit_new_guest(address):
            if state.players.get(player_id) is player and not player["spent"]:
                del state.players[player_id]  # nothing to keep: they never played
            limits.give_back_slot(address)
            state.turned_away[address] += 1
            return JSONResponse({"error": "too many new players from your network lately."}, status_code=429, headers={"Retry-After": "15"})
        await cards.get_best_round(player_id)
    except BaseException:
        limits.give_back_slot(address)
        raise

    async def stream():
        if state.game["role"] == "retired":
            # The game was handed to a new server while we were looking this player up.
            yield role.come_back()
            return
        queue = asyncio.Queue(maxsize=100)
        state.streams[queue] = player_id
        try:
            yield streams.sse("me", player)
            yield leaderboard.leaderboard_message(player)
            if player_id in state.best_rounds:
                yield cards.card_message(state.best_rounds[player_id], fresh=False)
            yield broadcast.catch_up()
            while True:
                item = await queue.get()
                if item is None:
                    return  # the game has been handed to a new server: see end_stream()
                message, tag = item
                yield message
                # We get here once the message has been handed to the network.
                if tag is not None:
                    streams.note_written(player_id, tag)
        finally:
            del state.streams[queue]  # the page has gone away
            player["seen"] = time.time()

    # The headers ask anything between the server and the browser not to hold events back.
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return PageStream(stream(), address, media_type="text/event-stream", headers=headers)


async def buy(request):
    """Buy one share on one side, at its current cost (see costs_for_net())."""
    data = await inputs.read_json(request)
    side = data.get("side")
    if side not in ("left", "right"):
        return JSONResponse({"error": "bad side."}, status_code=400)
    player_id = inputs.parse_id(data.get("player"))
    player = state.players.get(player_id)
    if player is None:
        return JSONResponse({"error": "unknown player."}, status_code=400)
    frame = market.current_frame()  # read the clock once, so the logged frame is the one the price was worked out at
    if not market.betting_open(frame):
        return JSONResponse({"error": "trading is closed."}, status_code=400)
    # At most MAX_BUYS_PER_SECOND in any one second, so a script can't flood the market. And at
    # most MAX_BUYS_PER_ADDRESS from all the players at one internet address together, so that
    # one person can't get round it, or push the price around with many players' free points.
    now = time.monotonic()
    times = state.recent_buys.setdefault(player_id, collections.deque())
    while times and times[0] <= now - 1:
        times.popleft()
    if len(times) >= config.MAX_BUYS_PER_SECOND:
        return JSONResponse({"error": "too many buys at once, slow down."}, status_code=429)
    address = limits.client_address(request)
    address_times = None
    if config.MAX_BUYS_PER_ADDRESS and address is not None:
        address_times = state.recent_address_buys.setdefault(address, collections.deque())
        while address_times and address_times[0] <= now - 1:
            address_times.popleft()
        if len(address_times) >= config.MAX_BUYS_PER_ADDRESS:
            return JSONResponse({"error": "too many buys from your network at once, slow down."}, status_code=429)
    times.append(now)
    if address_times is not None:
        address_times.append(now)
    player["seen"] = time.time()
    price = market.costs(frame)[side]
    if price > player["points"]:
        return JSONResponse({"error": "you don't have enough points."}, status_code=400)
    # For the round log. The page says which frame it was showing when the button was pressed,
    # which is a little behind the server's frame. It's the page's word, so only good for analysis.
    seen_frame = data.get("frame")
    if type(seen_frame) is not int or not 0 <= seen_frame < packing.NOT_SEEN:
        seen_frame = packing.NOT_SEEN
    state.game["buys"].append((frame, seen_frame, player_id, side, price, player["points"]))
    # For the round log's timing: when the server took this buy, and the newest broadcast it had
    # written to this player's pages by then, so analysis can tell how long the player took to
    # answer what they were sent, apart from where their page came in the order of sending.
    seen_index, seen_at = state.written_to.get(player_id, (None, None))
    state.game["buy_timing"].append((time.time() - state.game["started_at"], seen_at, seen_index))
    player["points"] = round(player["points"] - price, 2)
    player["spent"] = round(player["spent"] + price, 2)
    player["shares"][side] += 1
    state.game["shares"][side] += 1
    return JSONResponse(player)


async def set_name(request):
    """Give a player a name, which puts them on the leaderboard, or change the name they have
    to any other that nobody is using. Names are unique, ignoring capitals.

    A name must pass the rules in game/names.py (length, characters, reserved names and a word
    filter). They are checked only here, when a name is set or changed: names saved before a
    rule existed stay as they are, and the owner deals with any bad ones (see admin_rename()).
    A player whose name the owner has locked can't change it.
    """
    data = await inputs.read_json(request)
    player_id = inputs.parse_id(data.get("player"))
    if player_id not in state.players:
        return JSONResponse({"error": "unknown player."}, status_code=400)
    locked = JSONResponse({"error": "your username can't be changed."}, status_code=403)
    if state.players[player_id]["name_locked"]:
        return locked
    name = data.get("name")
    if not isinstance(name, str):
        return JSONResponse({"error": "bad username."}, status_code=400)
    name = name.strip()
    problem = names.name_problem(name)
    if problem:
        return JSONResponse({"error": problem}, status_code=400)

    # Make sure we know whether this player already has a saved name before writing a new one.
    sorry = JSONResponse({"error": "couldn't save your username. try again in a minute."}, status_code=503)
    player = await players.get_player(player_id)
    if not player["loaded"]:
        return sorry
    if player["name"] is None:
        # Ask the database once more before a first name is written, as that write replaces any
        # row with this id. get_player() may have known "no row" only from load_ranking()'s read
        # at startup, and during a deploy the previous server keeps serving for a little while:
        # a name claimed there after our read would be overwritten. If there is a row, the
        # player takes it up (name, points and lock), and what they asked for is a change of name.
        try:
            saved = await players.load_player(player_id)
        except httpx.HTTPError as error:
            print("Could not load a player:", repr(error))
            return sorry
        if saved and player["name"] is None:
            players.use_saved_row(player_id, saved)
            state.named_points.setdefault(saved["name"], saved["points"])
            streams.send_to_player(player_id, streams.sse("me", player))
            leaderboard.publish_leaderboard()
    try:
        async with state.saving_players:
            if state.game["role"] != "live":
                return role.moved(request)  # the game was handed to a new server while we waited our turn
            # Checked again here, as the owner may have locked the name while we waited our turn.
            if player["name_locked"]:
                return locked
            if name == player["name"]:
                return JSONResponse(player)
            old_name = player["name"]
            if old_name:
                # A change of name writes only the name. Their saved points stay as they are.
                response = await db.client.patch("/players", params={"id": "eq." + player_id}, json={"name": name})
                response.raise_for_status()
            else:
                await players.save_players([{"id": player_id, "name": name, "points": player["points"]}])
            player["name"] = name
            state.saved_ids.add(player_id)  # they have a row now, which get_player() must look up next time
            # On the leaderboard, the new name takes over the points shown beside the old one.
            state.named_points[name] = state.named_points.pop(old_name, player["points"])
    except httpx.HTTPError as error:
        # The database answers 409 when someone else already has the name.
        if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 409:
            return JSONResponse({"error": "sorry, that username is taken."}, status_code=409)
        print("Could not save a name:", repr(error))
        return sorry
    leaderboard.publish_leaderboard()
    return JSONResponse(player)


def minutes(seconds):
    """A wait as a person would say it, rounded up to whole minutes: "1 minute", "7 minutes"."""
    count = max(1, math.ceil(seconds / 60))
    return f"{count} minute" if count == 1 else f"{count} minutes"


async def reset_points(request):
    """Start a player over with 10 points, whatever they had before. Guests too, so someone
    who loses everything before claiming a name isn't stuck.

    Not while they hold shares in the round being played. Otherwise someone could spend all
    their points pushing a price around, reset, and do it again for free. And not too often:
    once every RESET_COOLDOWN_SECONDS for each player, and MAX_RESETS_PER_ADDRESS an hour for
    all the players at one internet address together. Players did spend their 10 points every
    round, many players at once, and reset between rounds, and the price they pushed around was
    there for others to take (see docs/limits.md).
    """
    data = await inputs.read_json(request)
    player_id = inputs.parse_id(data.get("player"))
    player = state.players.get(player_id)
    if player is None:
        return JSONResponse({"error": "unknown player."}, status_code=400)
    if player["spent"] > 0:
        return JSONResponse({"error": "you can reset once this round is over."}, status_code=400)
    now = time.time()
    wait = player["last_reset"] + config.RESET_COOLDOWN_SECONDS - now
    if config.RESET_COOLDOWN_SECONDS and wait > 0:
        return JSONResponse({"error": f"you can reset again in {minutes(wait)}."}, status_code=429)
    address = limits.client_address(request)
    if config.MAX_RESETS_PER_ADDRESS and address is not None:
        times = state.recent_resets.setdefault(address, collections.deque())
        while times and times[0] <= now - 3600:
            times.popleft()
        if len(times) >= config.MAX_RESETS_PER_ADDRESS:
            return JSONResponse({"error": f"too many resets from your network lately. try again in {minutes(times[0] + 3600 - now)}."}, status_code=429)
        times.append(now)
    player["last_reset"] = now
    player["points"] = 10
    if not player["name"]:
        # A guest has nothing saved in the database, and is on no leaderboard but their own,
        # so only their own pages need a new one.
        streams.send_to_player(player_id, leaderboard.leaderboard_message(player))
        return JSONResponse(player)
    state.named_points[player["name"]] = 10
    leaderboard.publish_leaderboard()
    try:
        async with state.saving_players:
            await players.save_players([{"id": player_id, "name": player["name"], "points": 10}])
    except httpx.HTTPError as error:
        # Like a round's save: these points are saved after the next round the player plays in.
        print("Could not save a reset:", repr(error))
    return JSONResponse(player)


async def clear_card(request):
    """Delete a player's card from their page, when they ask. Send {"player": <their id>}.

    Their record starts over: the next round that makes more than CARD_MIN_PROFIT makes a new
    card. Nothing is removed, though: every card they've had is marked cleared, so it no longer
    counts as their best, and its share link keeps working for anyone it was sent to.
    """
    data = await inputs.read_json(request)
    player_id = inputs.parse_id(data.get("player"))
    if player_id not in state.players:
        return JSONResponse({"error": "unknown player."}, status_code=400)
    async with state.saving_cards:  # so a save on its way can't land after this and bring a card back
        if state.game["role"] != "live":
            return role.moved(request)  # the game was handed to a new server while we waited our turn
        try:
            response = await db.client.patch(
                "/best_rounds", params={"player": "eq." + player_id, "cleared": "eq.false"}, json={"cleared": True})
            response.raise_for_status()
        except httpx.HTTPError as error:
            print("Could not clear a card:", repr(error))
            return JSONResponse({"error": "couldn't delete your card. try again in a minute."}, status_code=503)
        for card in state.unsaved_cards:
            if card["player"] == player_id:
                card["cleared"] = True
        state.best_rounds.pop(player_id, None)
        state.card_holders.discard(player_id)
        state.best_loaded.add(player_id)  # known now: no card, so a lookup still on its way mustn't bring one back
    streams.send_to_player(player_id, cards.card_message(None, fresh=False))  # their other open pages too
    return JSONResponse({"cleared": True})
