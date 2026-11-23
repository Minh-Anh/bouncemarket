# /// script
# dependencies = ["starlette", "uvicorn"]  # keep in sync with requirements.txt
# ///
import argparse
import asyncio
import contextlib
import json
import math
import os
import random
import time

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

# Everything below lives in memory for now, so it resets when the server restarts.
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
    "bets": {"left": 0, "right": 0},  # all the players' bets on this round, added up
    "last_bucket": None,  # where the previous ball landed
    "landed": {"left": 0, "right": 0},  # how many balls have landed in each bucket
}

# One entry per player, keyed by the random id their browser sends:
# {"points": 10, "bets": {"left": 0, "right": 0}, "last_won": 0}
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


def get_player(player_id):
    """Look up a player, creating them with 10 points the first time we see their id."""
    if player_id not in players:
        players[player_id] = {"points": 10, "bets": {"left": 0, "right": 0}, "last_won": 0}
    return players[player_id]


def current_frame():
    """How many frames have passed since the ball dropped."""
    return int((time.time() - game["started_at"]) / FRAME_SECONDS)


def counterparty_bets(path, frames):
    """How much the counterparty has bet on each bucket once the given number of frames have passed.

    It bets one point at the drop and one more every 0.5 seconds, each time on whichever
    half of the board the ball is in at that moment.
    """
    bets = {"left": 0, "right": 0}
    for frame in range(0, frames, COUNTERPARTY_BUY_FRAMES):
        side = "left" if path[frame][0] < WIDTH / 2 else "right"
        bets[side] += 1
    return bets


def sse(event, data):
    """Format one server-sent event, the browser's built-in format for a stream of messages."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def send(queue, message):
    """Queue a message for one page. A page that has fallen far behind misses it instead."""
    if not queue.full():
        queue.put_nowait(message)


def settle():
    """Pay out every player's bets on the ball that just landed, and tell each page its result.

    All bets, the players' and the counterparty's, go into one pot. The pot is split among
    the points bet on the winning bucket, so players can only win what others lose.
    """
    bucket = game["bucket"]
    theirs = counterparty_bets(game["path"], game["close_frame"])
    pot = theirs["left"] + theirs["right"] + game["bets"]["left"] + game["bets"]["right"]
    winning_points = theirs[bucket] + game["bets"][bucket]

    for player in players.values():
        my_total = player["bets"]["left"] + player["bets"]["right"]
        if winning_points == 0:
            payout = my_total  # nobody bet on the winning bucket, so everyone gets their bet back
        else:
            payout = pot * player["bets"][bucket] / winning_points
        player["last_won"] = round(payout - my_total, 2)
        player["points"] = round(player["points"] + player["last_won"], 2)
        player["bets"] = {"left": 0, "right": 0}

    game["bets"] = {"left": 0, "right": 0}
    game["last_bucket"] = bucket
    game["landed"][bucket] += 1
    for queue, player_id in streams.items():
        send(queue, sse("me", players[player_id]))


async def run_rounds():
    """The round loop: wait for the countdown, drop a ball, wait for it to land, pay out. Forever."""
    while True:
        await asyncio.sleep(COUNTDOWN_SECONDS)
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
        settle()
        game["dropping"] = False
        game["drops_at"] = time.time() + COUNTDOWN_SECONDS


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
            "totals": dict(game["bets"]),  # every bet on each side; the counterparty's are added below
            "online": len(streams),
            "last_bucket": game["last_bucket"],
            "landed": game["landed"],
        }
        if game["dropping"]:
            frame = current_frame()
            theirs = counterparty_bets(path, min(frame + 1, game["close_frame"]))
            message["totals"]["left"] += theirs["left"]
            message["totals"]["right"] += theirs["right"]
            message["betting_open"] = frame < game["close_frame"]
            # The positions the pages don't have yet, and the frame number of the first of them.
            end = min(frame + BUFFER_FRAMES + 1, len(path))
            message["frame"] = frame
            message["first_position"] = game["sent_frames"]
            message["positions"] = path[game["sent_frames"]:end]
            game["sent_frames"] = end
        else:
            message["betting_open"] = True
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

    The page gets a private "me" message (its points and bets) when it connects and after
    each round, and the public "game" message from broadcast().
    """
    player_id = request.query_params["player"][:64]
    player = get_player(player_id)

    async def stream():
        queue = asyncio.Queue(maxsize=100)
        streams[queue] = player_id
        try:
            yield sse("me", player)
            while True:
                yield await queue.get()
        finally:
            del streams[queue]  # the page has gone away

    # The headers ask anything between the server and the browser not to hold events back.
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)


async def bet(request):
    """Change a player's bet on one side by one point. While the ball is falling, bets can only go up."""
    data = await request.json()
    side, change = data["side"], data["change"]
    if side not in ("left", "right") or change not in (1, -1):
        return JSONResponse({"error": "Bad bet."}, status_code=400)
    player = get_player(data["player"][:64])
    bets = player["bets"]
    if game["dropping"] and change < 0:
        return JSONResponse({"error": "Bets can't be lowered once the ball drops."}, status_code=400)
    if game["dropping"] and current_frame() >= game["close_frame"]:
        return JSONResponse({"error": "Betting is closed."}, status_code=400)
    if bets[side] + change < 0:
        return JSONResponse({"error": "Bets can't be negative."}, status_code=400)
    if bets["left"] + bets["right"] + change > player["points"]:
        return JSONResponse({"error": "You can't bet more than your points."}, status_code=400)
    bets[side] += change
    game["bets"][side] += change
    return JSONResponse(player)


@contextlib.asynccontextmanager
async def lifespan(app):
    """Start the round loop and the broadcaster when the server starts."""
    tasks = [asyncio.create_task(run_rounds()), asyncio.create_task(broadcast())]
    yield
    for task in tasks:
        task.cancel()


app = Starlette(
    routes=[
        Route("/", index),
        Route("/board", board),
        Route("/events", events),
        Route("/bet", bet, methods=["POST"]),
    ],
    lifespan=lifespan,
)

if __name__ == "__main__":
    # uvicorn runs everything on one thread, switching between pages whenever one is waiting,
    # so thousands of open pages are cheap. Render tells us the port through the PORT variable.
    # The short shutdown timeout lets Ctrl-C work while pages still have streams open.
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), timeout_graceful_shutdown=1)
