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
# The side walls lean inwards, so the board is a trapezoid: WIDTH across the top, and
# WALL_SLANT narrower on each side at the floor. The pins squeeze together as the walls close in.
WALL_SLANT = 60
WALL_LENGTH = math.hypot(WALL_SLANT, HEIGHT)

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
BROADCASTS_PER_SECOND = 10  # the most often every page is sent the state of the game; see broadcast()
FRAMES_PER_BROADCAST = 6  # 60 frames a second / 10 broadcasts a second
FINAL_STRETCH_FRAMES = 90  # for the last 1.5 seconds before betting closes, and until the ball lands, send every broadcast
EARLY_AHEAD_FRAMES = 48  # before that, send positions this far ahead (0.8 seconds), so a message is needed only every half second
POSITION_DECIMALS = 1  # ball positions are sent rounded to tenths of a pixel; 0 (whole pixels) is ~15% smaller but may look steppy
BUFFER_FRAMES = 12  # ball positions are sent this many frames early, so the page can play back smoothly
# The counterparty buys faster as the ball falls: every 60 frames (1 second) with the ball at
# the top, speeding up to every 6 frames (0.1 seconds) by the bottom of the betting zone.
COUNTERPARTY_SLOWEST_FRAMES = 60
COUNTERPARTY_FASTEST_FRAMES = 6
COUNTDOWN_SECONDS = 5  # the wait between one ball landing and the next one dropping
LEADERBOARD_SIZE = 10
NEIGHBORS = 2  # a player ranked below the top 10 also sees this many players either side of them
PAGE_SIZE = 1000  # the most rows Supabase returns from one request, unless its settings say otherwise
SEED_SHARES = 1  # pretend shares on each side, so the price starts at 0.50. Fewer makes early buys swing it harder


def wall_inset(y):
    """How far in from the canvas edge each side wall is at height y."""
    return WALL_SLANT * y / HEIGHT


def make_pins():
    """Row 0 has 2 pins, and each row below has one more, so the board gets denser.

    Each row's pins are spaced evenly between the walls, with a full gap between the outermost
    pin and the wall, so the ball can always fit past it instead of wedging against the wall.
    """
    pins = []
    for row in range(ROWS):
        count = row + 2
        y = FIRST_ROW_Y + row * ROW_GAP
        left = wall_inset(y)
        spacing = (WIDTH - 2 * left) / (count + 1)
        for i in range(count):
            pins.append((left + spacing * (i + 1), y))
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
    "counterparty_buys": [],  # the counterparty's buys this drop, as [(frame, side)], from counterparty_buys()
    "sent_frames": 0,  # how many frames of the path have been sent to the pages so far
    "shares": {"left": 0, "right": 0},  # all the shares players have bought this round, added up
    "last_bucket": None,  # where the previous ball landed
    "landed": {"left": 0, "right": 0},  # how many balls have landed in each bucket
    "ranking": [],  # every named player, most points first, as [{"name": ..., "points": ...}], made from named_points
    "positions": {},  # each name's index in "ranking", so a player can be found quickly
}

# One entry per player who has opened the page since the server started, keyed by the
# random id their browser sends:
# {"name": None, "points": 10, "shares": {"left": 0, "right": 0}, "spent": 0, "last_won": 0, "loaded": True}
# "name" is None until the player claims one. "shares" and "spent" are what the player has
# bought, and paid for it, in the current round. "loaded" is explained in get_player().
players = {}

# Every named player's points, {name: points}, for the leaderboard. Loaded from the database
# when the server starts, then updated here whenever a named player's points change.
named_points = {}

# One entry per open page: a queue of messages waiting to be sent to it, and whose page it is.
streams = {}  # {queue: player_id}


def simulate_drop():
    """Drop one ball. Returns its position at every frame, and the bucket it lands in."""
    # Drop from somewhere in the middle two-thirds, so the ball never just slides down a slanted wall.
    x, y = random.uniform(WIDTH / 6, WIDTH * 5 / 6), 0.0
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

        # Side walls. Each leans inwards, so each has its own normal: the direction pointing
        # straight off the wall into the board, (nx, ny), mostly sideways and slightly up.
        for nx, ny, distance in (
            (HEIGHT / WALL_LENGTH, -WALL_SLANT / WALL_LENGTH, (x * HEIGHT - y * WALL_SLANT) / WALL_LENGTH),
            (-HEIGHT / WALL_LENGTH, -WALL_SLANT / WALL_LENGTH, ((WIDTH - x) * HEIGHT - y * WALL_SLANT) / WALL_LENGTH),
        ):
            # distance is how far the ball's center is from the wall, measured along the normal.
            if distance < BALL_R:
                x, y = x + nx * (BALL_R - distance), y + ny * (BALL_R - distance)
                speed_into_wall = -(vx * nx + vy * ny)
                if speed_into_wall > 0:
                    vx += (1 + BOUNCE) * speed_into_wall * nx
                    vy += (1 + BOUNCE) * speed_into_wall * ny

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


async def load_ranking():
    """Read every named player's points from the database, once, when the server starts.

    After that the server keeps the ranking up to date itself. It is the only thing that
    changes anyone's points, so it already knows every change without asking the database.
    The database hands rows over at most PAGE_SIZE at a time, so we ask page by page.
    If the database can't be reached, we keep trying every 30 seconds. Players who play in
    the meantime are ranked with their newer points, which the database rows don't overwrite.
    """
    while True:
        rows = []
        try:
            while True:
                params = {"select": "name,points", "limit": PAGE_SIZE, "offset": len(rows), "order": "id"}
                response = await db.get("/players", params=params)
                response.raise_for_status()
                page = response.json()
                rows += page
                if len(page) < PAGE_SIZE:
                    break
        except httpx.HTTPError as error:
            print("Could not load the leaderboard, trying again in 30 seconds:", repr(error))
            await asyncio.sleep(30)
            continue
        for row in rows:
            named_points.setdefault(row["name"], row["points"])
        publish_leaderboard()
        return


def publish_leaderboard():
    """Rank the named players, most points first, and send each page its leaderboard.
    Players with the same points are ranked by name, so the order never shuffles."""
    ranking = [{"name": name, "points": points} for name, points in named_points.items()]
    ranking.sort(key=lambda row: (-row["points"], row["name"]))
    game["ranking"] = ranking
    game["positions"] = {row["name"]: i for i, row in enumerate(ranking)}
    for queue, player_id in streams.items():
        send(queue, leaderboard_message(players[player_id]))


def leaderboard_message(player):
    """The leaderboard as one player sees it: the top LEADERBOARD_SIZE players and, if this
    player is ranked below them, the NEIGHBORS players either side of them as well.
    Every row carries its rank, counting from 1."""
    ranking = game["ranking"]

    def rows(start, end):
        return [{"rank": i + 1, **ranking[i]} for i in range(start, min(end, len(ranking)))]

    around_me = []
    position = game["positions"].get(player["name"])
    if position is not None and position >= LEADERBOARD_SIZE:
        around_me = rows(max(LEADERBOARD_SIZE, position - NEIGHBORS), position + NEIGHBORS + 1)
    return sse("leaderboard", {"top": rows(0, LEADERBOARD_SIZE), "around_me": around_me})


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


def counterparty_buys(path, close_frame):
    """The frames the counterparty buys on this drop, and the side it buys each time.

    It buys one share at the drop, then keeps buying until betting closes, each time on
    whichever half of the board the ball is in at that moment. The lower the ball, the
    sooner the next buy: the wait shrinks in a straight line from COUNTERPARTY_SLOWEST_FRAMES
    with the ball at the top to COUNTERPARTY_FASTEST_FRAMES with the ball as low as it is
    when betting closes. So it piles in as the ball nears the bottom, when where the ball
    is says the most about where it will land. It never buys at close_frame itself.
    """
    bottom = max(1, path[close_frame][1])  # how far down the ball is when betting closes
    buys = []
    frame = 0
    while frame < close_frame:
        x, y = path[frame]
        buys.append((frame, "left" if x < WIDTH / 2 else "right"))
        depth = min(1, max(0, y / bottom))  # 0 at the top, 1 at the bottom of the betting zone
        frame += round(COUNTERPARTY_SLOWEST_FRAMES - (COUNTERPARTY_SLOWEST_FRAMES - COUNTERPARTY_FASTEST_FRAMES) * depth)
    return buys


def counterparty_shares():
    """How many shares the counterparty has bought on each side so far this round.

    It never collects on them. They only move the price, towards the side the ball has been
    spending its time on, and most of all towards where it has been lately.
    """
    shares = {"left": 0, "right": 0}
    if game["dropping"]:
        now = current_frame()
        for frame, side in game["counterparty_buys"]:
            if frame > now:
                break
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
    # No spaces after commas and colons: the same JSON, a little smaller to send to every page.
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


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
    for row in rows:
        named_points[row["name"]] = row["points"]
    for queue, player_id in streams.items():
        send(queue, sse("me", players[player_id]))
    return rows


async def run_rounds():
    """The round loop: wait for the countdown, drop a ball, wait for it to land, pay out. Forever."""
    while True:
        await asyncio.sleep(max(0, game["drops_at"] - time.time()))
        path, bucket = simulate_drop()
        last_row_frame = next(i for i, (x, y) in enumerate(path) if y > LAST_ROW_Y)
        # Pages see BUFFER_FRAMES ahead, so betting closes that long before the ball
        # passes the last row. Nobody can bet after seeing which side of the divider it took.
        close_frame = max(0, last_row_frame - BUFFER_FRAMES)
        game.update(
            dropping=True,
            started_at=time.time(),
            path=path,
            bucket=bucket,
            close_frame=close_frame,
            counterparty_buys=counterparty_buys(path, close_frame),
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
        publish_leaderboard()


def rounded(position):
    """A ball position rounded to POSITION_DECIMALS places, to keep messages short."""
    if POSITION_DECIMALS == 0:
        return [round(position[0]), round(position[1])]  # whole numbers, sent without a ".0"
    return [round(position[0], POSITION_DECIMALS), round(position[1], POSITION_DECIMALS)]


async def broadcast():
    """Send every page the public state of the game: up to 10 times a second, but only as often as needed.

    The message is built once and the same text goes to everyone. While the ball is falling
    it carries the next positions on the path, ahead of time. The page plays them back on its
    own clock, so it always has some in hand and the ball moves smoothly even when the
    network delivers messages unevenly.

    Most of the cost of a big game is these messages, so we send as few as we can:
    - Early in the drop, positions go out EARLY_AHEAD_FRAMES ahead, about twice a second.
    - For the last FINAL_STRETCH_FRAMES before betting closes, and until the ball lands, they go
      out only BUFFER_FRAMES ahead, every broadcast. Betting closes BUFFER_FRAMES before the ball
      passes the last row, so no page is ever sent the ball's side of the divider while it can still bet.
    - Between drops, once a second, just for the countdown and the price.
    - Straight away whenever a drop starts or the ball lands.
    """
    ticks_since_sent = 0
    was_dropping = None
    while True:
        await asyncio.sleep(1 / BROADCASTS_PER_SECOND)
        ticks_since_sent += 1
        path = game["path"]
        dropping = game["dropping"]
        if dropping:
            frame = current_frame()
            final_stretch = frame >= game["close_frame"] - FINAL_STRETCH_FRAMES
            ahead = BUFFER_FRAMES if final_stretch else EARLY_AHEAD_FRAMES
            # Early on, send only when the pages would otherwise have less than BUFFER_FRAMES in hand by the next broadcast.
            running_low = game["sent_frames"] <= frame + BUFFER_FRAMES + FRAMES_PER_BROADCAST
            due = final_stretch or running_low
        else:
            due = ticks_since_sent >= BROADCASTS_PER_SECOND
        if not due and dropping == was_dropping:
            continue
        ticks_since_sent = 0
        was_dropping = dropping

        message = {
            "dropping": game["dropping"],
            "seconds_to_drop": max(0, game["drops_at"] - time.time()),
            "prices": prices(),
            "betting_open": betting_open(),
            "online": len(streams),
            "last_bucket": game["last_bucket"],
            "landed": game["landed"],
        }
        if dropping:
            # The positions the pages don't have yet, and the frame number of the first of them.
            # Never past close_frame + BUFFER_FRAMES (the last row) while betting is open, however early it is.
            end = min(frame + ahead + 1, len(path))
            if betting_open():
                end = min(end, game["close_frame"] + BUFFER_FRAMES)
            start = min(game["sent_frames"], end)
            message["frame"] = frame
            message["first_position"] = start
            message["positions"] = [rounded(position) for position in path[start:end]]
            game["sent_frames"] = max(game["sent_frames"], end)

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
        "wall_slant": WALL_SLANT,
        "countdown_seconds": COUNTDOWN_SECONDS,  # the page fades the landing highlight over this long
    })


async def events(request):
    """Stream messages to one player's page for as long as it stays open.

    The page gets a private "me" message (its name, points and shares) when it connects and
    after each round, the public "game" message from broadcast(), and its own "leaderboard"
    message (the top players, plus the players around it) when it connects and whenever the
    leaderboard is refreshed.
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
            yield leaderboard_message(player)
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
            return JSONResponse({"error": "Sorry, that name is taken."}, status_code=409)
        print("Could not save a name:", repr(error))
        return sorry
    player["name"] = name
    named_points[name] = player["points"]
    publish_leaderboard()
    return JSONResponse(player)


async def reset_points(request):
    """Start a named player over with 10 points, whatever they had before.

    Not while they hold shares in the round being played. Otherwise someone could spend all
    their points pushing a price around, reset, and do it again for free.
    """
    data = await request.json()
    player = players.get(parse_id(data["player"]))
    if player is None or not player["name"]:
        return JSONResponse({"error": "Unknown player."}, status_code=400)
    if player["spent"] > 0:
        return JSONResponse({"error": "You can reset once this round is over."}, status_code=400)
    player["points"] = 10
    named_points[player["name"]] = 10
    publish_leaderboard()
    try:
        await save_players([{"id": parse_id(data["player"]), "name": player["name"], "points": 10}])
    except httpx.HTTPError as error:
        # Like a round's save: these points are saved after the next round the player plays in.
        print("Could not save a reset:", repr(error))
    return JSONResponse(player)


@contextlib.asynccontextmanager
async def lifespan(app):
    """When the server starts: load the leaderboard, and start the round loop and the broadcaster.
    The game starts straight away, even if the leaderboard takes a few tries to load."""
    tasks = [asyncio.create_task(load_ranking()), asyncio.create_task(run_rounds()), asyncio.create_task(broadcast())]
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
        Route("/reset", reset_points, methods=["POST"]),
    ],
    lifespan=lifespan,
)

if __name__ == "__main__":
    # uvicorn runs everything on one thread, switching between pages whenever one is waiting,
    # so thousands of open pages are cheap. Render tells us the port through the PORT variable.
    # The short shutdown timeout lets Ctrl-C work while pages still have streams open.
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), timeout_graceful_shutdown=1)
