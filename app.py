# /// script
# dependencies = ["httpx", "starlette", "uvicorn"]  # keep in sync with requirements.txt
# ///
import argparse
import asyncio
import contextlib
import json
import math
import os
import random
import time
import uuid

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Route

# Board geometry, in pixels. (0, 0) is the top-left corner.
WIDTH, HEIGHT = 400, 560
BALL_R, PIN_R = 5, 3
ROWS = 12  # must be even, so the last row has a center pin that caps the divider
FIRST_ROW_Y, ROW_GAP = 120, 36
LAST_ROW_Y = FIRST_ROW_Y + (ROWS - 1) * ROW_GAP

# Physics. Gravity and bounce can be set on the command line, e.g. --bounce 0.3
parser = argparse.ArgumentParser()
parser.add_argument("--gravity", type=float, default=400, help="pixels / second^2")
parser.add_argument("--bounce", type=float, default=0.8, help="fraction of speed kept after hitting something, 0 to 1")
args = parser.parse_args()
GRAVITY = args.gravity
BOUNCE = args.bounce
DT = 1 / 240  # seconds per physics step
STEPS_PER_FRAME = 4  # the page is sent one ball position per frame
FRAME_SECONDS = STEPS_PER_FRAME * DT  # 1/60 second
BROADCASTS_PER_SECOND = 10  # how often every page is sent the state of the game
BUFFER_FRAMES = 12  # ball positions are sent this many frames early, so the page can play back smoothly
COUNTERPARTY_BUY_FRAMES = 30  # the counterparty buys every 30 frames, which is 0.5 seconds
COUNTDOWN_SECONDS = 5  # the wait between one ball landing and the next one dropping
LEADERBOARD_SIZE = 10
SEED_SHARES = 2  # pretend shares on each side, so the price starts at 0.50 and early buys move it gently


def make_pins():
    """Row 0 has 2 pins, and each row below has one more, so the board gets denser."""
    pins = []
    for row in range(ROWS):
        count = row + 2
        spacing = WIDTH / count
        y = FIRST_ROW_Y + row * ROW_GAP
        for i in range(count):
            pins.append((spacing * (i + 0.5), y))
    return pins


PINS = make_pins()

# Named players are kept in a Supabase database table, so their points survive a restart.
# We talk to it over its web API. The secret key gives full access, so it stays on the
# server (in an environment variable) and the page never talks to Supabase itself.
db = httpx.AsyncClient(
    base_url=os.environ["SUPABASE_URL"] + "/rest/v1",
    headers={"apikey": os.environ["SUPABASE_KEY"], "Authorization": "Bearer " + os.environ["SUPABASE_KEY"]},
    timeout=10,
)

# Everything below lives in memory, so it resets when the server restarts.
# The whole server runs on one thread (see the bottom of the file), so none of it needs locks.

# The one shared game that every player watches and bets on. Rounds repeat forever:
# a countdown, then a ball drops. "path" is the ball's position at every frame (None
# before the first drop) and "bucket" is where it lands. Pages are sent the path a few
# frames at a time, and the bucket when the ball lands.
game = {
    "dropping": False,
    "drops_at": time.time() + COUNTDOWN_SECONDS,  # when the countdown ends
    "started_at": 0,  # when the ball dropped
    "path": None,
    "bucket": None,
    "close_frame": 0,  # betting closes when the ball reaches this frame
    "sent_frames": 0,  # how many frames of the path have been sent to the pages so far
    "shares": {"left": 0, "right": 0},  # all the shares players have bought this round, added up
    "last_bucket": None,  # where the previous ball landed
    "landed": {"left": 0, "right": 0},  # how many balls have landed in each bucket
    "leaderboard": [],  # the named players with the most points, as [{"name": ..., "points": ...}]
}

# One entry per player who has opened the page since the server started, keyed by the
# random id their browser sends:
# {"name": None, "points": 10, "shares": {"left": 0, "right": 0}, "spent": 0, "last_won": 0, "loaded": True}
# "name" is None until the player claims one. "shares" and "spent" are what the player has
# bought, and paid for it, in the current round. "loaded" is explained in get_player().
players = {}

# One entry per open page: a queue of messages waiting to be sent to it, and whose page it is.
streams = {}  # {queue: player_id}


def simulate_drop():
    """Drop one ball. Returns its position at every frame, and the bucket it lands in."""
    x, y = random.uniform(BALL_R, WIDTH - BALL_R), 0.0
    vx, vy = 0.0, 0.0
    path = []
    step = 0
    while y < HEIGHT - BALL_R:
        if step % STEPS_PER_FRAME == 0:
            path.append((round(x, 1), round(y, 1)))
        step += 1

        vy += GRAVITY * DT
        x += vx * DT
        y += vy * DT

        # Side walls.
        if x < BALL_R:
            x, vx = BALL_R, abs(vx) * BOUNCE
        if x > WIDTH - BALL_R:
            x, vx = WIDTH - BALL_R, -abs(vx) * BOUNCE

        # Divider between the buckets: a wall from the last row's center pin to the floor.
        if y > LAST_ROW_Y and abs(x - WIDTH / 2) < BALL_R:
            side = 1 if x > WIDTH / 2 else -1
            x, vx = WIDTH / 2 + side * BALL_R, side * abs(vx) * BOUNCE

        # Pins.
        for pin_x, pin_y in PINS:
            dx, dy = x - pin_x, y - pin_y
            dist = math.hypot(dx, dy)
            if dist < BALL_R + PIN_R:
                # (nx, ny) points from the pin to the ball.
                nx, ny = dx / dist, dy / dist
                # Move the ball back out so it just touches the pin.
                x, y = pin_x + nx * (BALL_R + PIN_R), pin_y + ny * (BALL_R + PIN_R)
                # If the ball is moving into the pin, reflect that part of its velocity.
                speed_into_pin = -(vx * nx + vy * ny)
                if speed_into_pin > 0:
                    vx += (1 + BOUNCE) * speed_into_pin * nx
                    vy += (1 + BOUNCE) * speed_into_pin * ny

    bucket = "left" if x < WIDTH / 2 else "right"
    return path, bucket


def parse_id(text):
    """Player ids are UUIDs, the random ids browsers make. Returns None for anything else."""
    try:
        return str(uuid.UUID(text))
    except ValueError:
        return None


async def load_player(player_id):
    """Fetch a named player's row from the database, or None if this id never claimed a name."""
    response = await db.get("/players", params={"id": "eq." + player_id, "select": "name,points"})
    response.raise_for_status()
    rows = response.json()
    return rows[0] if rows else None


async def save_players(rows):
    """Write rows of {"id", "name", "points"} to the database, replacing any with the same id."""
    if rows:
        response = await db.post("/players", json=rows, headers={"Prefer": "resolution=merge-duplicates"})
        response.raise_for_status()


async def refresh_leaderboard():
    """Fetch the top named players from the database and send the list to every page."""
    params = {"select": "name,points", "order": "points.desc", "limit": LEADERBOARD_SIZE}
    try:
        response = await db.get("/players", params=params)
        response.raise_for_status()
    except httpx.HTTPError as error:
        print("Could not load the leaderboard:", repr(error))  # keep showing the last one we got
        return
    game["leaderboard"] = response.json()
    for queue in streams:
        send(queue, sse("leaderboard", game["leaderboard"]))


async def get_player(player_id):
    """Look up a player, creating them with 10 points the first time we see their id.

    If they claimed a name before, the database has their name and points, and we load those.
    If the database can't be reached they can still play, without a name for now, and we
    look again the next time they connect. "loaded" records whether that lookup has worked.
    """
    if player_id not in players:
        players[player_id] = {
            "name": None,
            "points": 10,
            "shares": {"left": 0, "right": 0},
            "spent": 0,
            "last_won": 0,
            "loaded": False,
        }
    player = players[player_id]
    if not player["loaded"]:
        try:
            saved = await load_player(player_id)
        except httpx.HTTPError as error:
            print("Could not load a player:", repr(error))
            return player
        if not player["loaded"]:  # another request for the same player may have finished first
            player["loaded"] = True
            if saved:
                player["name"], player["points"] = saved["name"], saved["points"]
    return player


def current_frame():
    """How many frames have passed since the ball dropped."""
    return int((time.time() - game["started_at"]) / FRAME_SECONDS)


def betting_open():
    """Shares can be bought from the moment the ball drops until it reaches close_frame."""
    return game["dropping"] and current_frame() < game["close_frame"]


def counterparty_shares():
    """How many shares the counterparty has bought on each side so far this round.

    It buys one share at the drop and one more every 0.5 seconds until betting closes, each
    time on whichever half of the board the ball is in at that moment. It never collects on
    them. They only move the price, towards the side the ball has been spending its time on.
    """
    shares = {"left": 0, "right": 0}
    if game["dropping"]:
        frames = min(current_frame() + 1, game["close_frame"])
        for frame in range(0, frames, COUNTERPARTY_BUY_FRAMES):
            side = "left" if game["path"][frame][0] < WIDTH / 2 else "right"
            shares[side] += 1
    return shares


def prices():
    """What one share on each side costs right now. A share pays 1 point if its side wins.

    The price of a side is its fraction of all the shares bought this round, by players and
    by the counterparty. So a side that everyone is buying gets expensive, and the two
    prices always add up to 1.
    """
    theirs = counterparty_shares()
    left = game["shares"]["left"] + theirs["left"] + SEED_SHARES
    right = game["shares"]["right"] + theirs["right"] + SEED_SHARES
    left_price = round(left / (left + right), 2)
    left_price = min(0.99, max(0.01, left_price))  # never free, and never a sure loss
    return {"left": left_price, "right": round(1 - left_price, 2)}


def sse(event, data):
    """Format one server-sent event, the browser's built-in format for a stream of messages."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def send(queue, message):
    """Queue a message for one page. A page that has fallen far behind misses it instead."""
    if not queue.full():
        queue.put_nowait(message)


def settle():
    """Pay 1 point for every share on the bucket the ball landed in, and tell each page its result.

    Players paid for their shares when they bought them. The counterparty is on the other
    side of every share: it kept what players paid, and it pays out the winners.
    Returns the database rows that need saving: the named players who played this round.
    """
    bucket = game["bucket"]
    rows = []
    for player_id, player in players.items():
        payout = player["shares"][bucket]
        if player["name"] and player["spent"] > 0:
            rows.append({"id": player_id, "name": player["name"], "points": round(player["points"] + payout, 2)})
        player["last_won"] = round(payout - player["spent"], 2)
        player["points"] = round(player["points"] + payout, 2)
        player["shares"] = {"left": 0, "right": 0}
        player["spent"] = 0

    game["shares"] = {"left": 0, "right": 0}
    game["last_bucket"] = bucket
    game["landed"][bucket] += 1
    for queue, player_id in streams.items():
        send(queue, sse("me", players[player_id]))
    return rows


async def run_rounds():
    """The round loop: wait for the countdown, drop a ball, wait for it to land, pay out. Forever."""
    while True:
        await asyncio.sleep(max(0, game["drops_at"] - time.time()))
        path, bucket = simulate_drop()
        last_row_frame = next(i for i, (x, y) in enumerate(path) if y > LAST_ROW_Y)
        game.update(
            dropping=True,
            started_at=time.time(),
            path=path,
            bucket=bucket,
            # Pages see BUFFER_FRAMES ahead, so betting closes that long before the ball
            # passes the last row. Nobody can bet after seeing which side of the divider it took.
            close_frame=max(0, last_row_frame - BUFFER_FRAMES),
            sent_frames=0,
        )
        await asyncio.sleep(len(path) * FRAME_SECONDS)
        rows = settle()
        game["dropping"] = False
        game["drops_at"] = time.time() + COUNTDOWN_SECONDS
        try:
            await save_players(rows)
        except httpx.HTTPError as error:
            # The game carries on. These players' points are saved after the next round they play.
            print("Could not save points:", repr(error))
        await refresh_leaderboard()


async def broadcast():
    """Send every page the public state of the game, several times a second.

    The message is built once and the same text goes to everyone. While the ball is falling
    it carries the next few positions on the path, up to BUFFER_FRAMES ahead of now. The page
    plays them back on its own clock, so it always has some in hand and the ball moves
    smoothly even when the network delivers messages unevenly.
    """
    while True:
        await asyncio.sleep(1 / BROADCASTS_PER_SECOND)
        path = game["path"]
        message = {
            "dropping": game["dropping"],
            "seconds_to_drop": max(0, game["drops_at"] - time.time()),
            "prices": prices(),
            "betting_open": betting_open(),
            "online": len(streams),
            "last_bucket": game["last_bucket"],
            "landed": game["landed"],
        }
        if game["dropping"]:
            frame = current_frame()
            # The positions the pages don't have yet, and the frame number of the first of them.
            end = min(frame + BUFFER_FRAMES + 1, len(path))
            message["frame"] = frame
            message["first_position"] = game["sent_frames"]
            message["positions"] = path[game["sent_frames"]:end]
            game["sent_frames"] = end
        else:
            message["ball"] = path[-1] if path else None  # between drops the ball rests where it landed

        text = sse("game", message)
        for queue in streams:
            send(queue, text)


async def index(request):
    return FileResponse("index.html")


async def board(request):
    return JSONResponse({
        "width": WIDTH,
        "height": HEIGHT,
        "ball_r": BALL_R,
        "pin_r": PIN_R,
        "last_row_y": LAST_ROW_Y,
        "pins": PINS,
    })


async def events(request):
    """Stream messages to one player's page for as long as it stays open.

    The page gets a private "me" message (its name, points and shares) when it connects and
    after each round, the public "game" message from broadcast(), and a "leaderboard"
    message when it connects and whenever the leaderboard is refreshed.
    """
    player_id = parse_id(request.query_params["player"])
    if player_id is None:
        return JSONResponse({"error": "Bad player id."}, status_code=400)
    player = await get_player(player_id)

    async def stream():
        queue = asyncio.Queue(maxsize=100)
        streams[queue] = player_id
        try:
            yield sse("me", player)
            yield sse("leaderboard", game["leaderboard"])
            while True:
                yield await queue.get()
        finally:
            del streams[queue]  # the page has gone away

    # The headers ask anything between the server and the browser not to hold events back.
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)


async def buy(request):
    """Buy one share on one side, at the current price."""
    data = await request.json()
    side = data["side"]
    if side not in ("left", "right"):
        return JSONResponse({"error": "Bad side."}, status_code=400)
    player = players.get(parse_id(data["player"]))
    if player is None:
        return JSONResponse({"error": "Unknown player."}, status_code=400)
    if not betting_open():
        return JSONResponse({"error": "Betting is closed."}, status_code=400)
    price = prices()[side]
    if price > player["points"]:
        return JSONResponse({"error": "You don't have enough points."}, status_code=400)
    player["points"] = round(player["points"] - price, 2)
    player["spent"] = round(player["spent"] + price, 2)
    player["shares"][side] += 1
    game["shares"][side] += 1
    return JSONResponse(player)


async def claim_name(request):
    """Give a player a name, which puts them on the leaderboard. Names are unique and permanent."""
    data = await request.json()
    player_id = parse_id(data["player"])
    if player_id not in players:
        return JSONResponse({"error": "Unknown player."}, status_code=400)
    name = data["name"].strip()
    if not 1 <= len(name) <= 16 or not name.isprintable():
        return JSONResponse({"error": "Names must be 1 to 16 characters."}, status_code=400)

    # Make sure we know whether this player already has a saved name before writing a new one.
    sorry = JSONResponse({"error": "Couldn't save your name. Try again in a minute."}, status_code=503)
    player = await get_player(player_id)
    if player["name"]:
        return JSONResponse(player)
    if not player["loaded"]:
        return sorry
    try:
        await save_players([{"id": player_id, "name": name, "points": player["points"]}])
    except httpx.HTTPError as error:
        # The database answers 409 when someone else already has the name.
        if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 409:
            return JSONResponse({"error": "That name is taken."}, status_code=409)
        print("Could not save a name:", repr(error))
        return sorry
    player["name"] = name
    await refresh_leaderboard()
    return JSONResponse(player)


@contextlib.asynccontextmanager
async def lifespan(app):
    """Load the leaderboard, then start the round loop and the broadcaster, when the server starts."""
    await refresh_leaderboard()
    tasks = [asyncio.create_task(run_rounds()), asyncio.create_task(broadcast())]
    yield
    for task in tasks:
        task.cancel()


app = Starlette(
    routes=[
        Route("/", index),
        Route("/board", board),
        Route("/events", events),
        Route("/buy", buy, methods=["POST"]),
        Route("/name", claim_name, methods=["POST"]),
    ],
    lifespan=lifespan,
)

if __name__ == "__main__":
    # uvicorn runs everything on one thread, switching between pages whenever one is waiting,
    # so thousands of open pages are cheap. Render tells us the port through the PORT variable.
    # The short shutdown timeout lets Ctrl-C work while pages still have streams open.
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), timeout_graceful_shutdown=1)
