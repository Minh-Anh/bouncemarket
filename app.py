# /// script
# dependencies = ["flask", "waitress"]  # keep in sync with requirements.txt
# ///
import argparse
import json
import math
import os
import random
import threading
import time

from flask import Flask, Response, jsonify, request, send_file
from waitress import serve

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
BUFFER_FRAMES = 5  # each frame is sent this many frames early, so the page can play back smoothly
COUNTERPARTY_BUY_FRAMES = 30  # the counterparty buys every 30 frames, which is 0.5 seconds


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

# Game state. Lives in memory for now, so it resets when the server restarts.
# "points" is the player's balance; "left" and "right" count the balls landed in each bucket;
# "bets" is how many points the player has bet on each bucket for the current drop;
# "last_bucket" and "last_won" are the result of the most recent drop.
state = {
    "points": 10,
    "left": 0,
    "right": 0,
    "bets": {"left": 0, "right": 0},
    "last_bucket": None,
    "last_won": 0,
}

# The ball in play. "path" is its position at every frame, or None if there is no ball.
# "bucket" is where it will land, and betting closes when it reaches "close_frame".
# The page only learns the path one frame at a time, and the bucket when the ball lands.
ball = {"path": None, "bucket": None, "close_frame": 0, "started_at": 0}

settle_lock = threading.Lock()  # stops two requests paying out the same ball at once


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


app = Flask(__name__)


@app.get("/")
def index():
    return send_file("index.html")


def seconds_until(frame):
    """How long until the ball in play reaches the given frame. Negative if it already has."""
    return ball["started_at"] + frame * FRAME_SECONDS - time.time()


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


def settle():
    """If the ball in play has landed, pay out the bets on it.

    All bets, the player's and the counterparty's, go into one pot. The pot is split among
    the points bet on the winning bucket, so the player can only win what the counterparty loses.
    """
    with settle_lock:
        path, bucket = ball["path"], ball["bucket"]
        if path is None or seconds_until(len(path)) > 0:
            return
        bets = state["bets"]
        theirs = counterparty_bets(path, ball["close_frame"])
        my_total = bets["left"] + bets["right"]
        pot = my_total + theirs["left"] + theirs["right"]
        winning_points = bets[bucket] + theirs[bucket]
        if winning_points == 0:
            payout = my_total  # nobody bet on the winning bucket, so everyone gets their bet back
        else:
            payout = pot * bets[bucket] / winning_points
        won = round(payout - my_total, 2)
        state["points"] = round(state["points"] + won, 2)
        state[bucket] += 1
        state["bets"] = {"left": 0, "right": 0}
        state["last_bucket"], state["last_won"] = bucket, won
        ball["path"] = None


def sse(event, data):
    """Format one server-sent event, the browser's built-in format for a stream of messages."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/board")
def board():
    settle()  # in case a ball landed while nobody was watching
    return jsonify(
        width=WIDTH,
        height=HEIGHT,
        ball_r=BALL_R,
        pin_r=PIN_R,
        last_row_y=LAST_ROW_Y,
        pins=PINS,
        state=state,
    )


@app.get("/drop")
def drop():
    """Drop a ball and stream it to the page as it falls.

    The page gets a "frame" event 60 times a second, then one "result" event when the ball
    lands. Each frame is sent BUFFER_FRAMES early, so the page always has a few in hand and
    can play them back smoothly even when the network delivers them unevenly. That is as far
    ahead of time as the page ever sees.
    """
    settle()  # in case a ball landed while nobody was watching
    if ball["path"] is not None:
        return jsonify(error="A ball is already falling."), 409
    path, bucket = simulate_drop()
    close_frame = next(i for i, (x, y) in enumerate(path) if y > LAST_ROW_Y)
    ball.update(path=path, bucket=bucket, close_frame=close_frame, started_at=time.time())

    def stream():
        for frame, (x, y) in enumerate(path):
            time.sleep(max(0, seconds_until(frame - BUFFER_FRAMES)))
            yield sse("frame", {
                "x": x,
                "y": y,
                "betting_open": frame < close_frame,
                "theirs": counterparty_bets(path, min(frame + 1, close_frame)),
            })
        time.sleep(max(0, seconds_until(len(path))))
        settle()
        yield sse("result", state)

    # The headers ask anything between the server and the browser not to hold events back.
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return Response(stream(), mimetype="text/event-stream", headers=headers)


@app.post("/bet")
def bet():
    """Change the bet on one side by one point. While a ball is falling, bets can only go up."""
    settle()  # in case a ball landed while nobody was watching
    side, change = request.json["side"], request.json["change"]
    bets = state["bets"]
    if side not in ("left", "right") or change not in (1, -1):
        return jsonify(error="Bad bet."), 400
    if ball["path"] is not None and change < 0:
        return jsonify(error="Bets can't be lowered once the ball drops."), 400
    if ball["path"] is not None and seconds_until(ball["close_frame"]) < 0:
        return jsonify(error="Betting is closed."), 400
    if bets[side] + change < 0:
        return jsonify(error="Bets can't be negative."), 400
    if bets["left"] + bets["right"] + change > state["points"]:
        return jsonify(error="You can't bet more than your points."), 400
    bets[side] += change
    return jsonify(state=state)


if __name__ == "__main__":
    # Render tells us which port to listen on through the PORT environment variable.
    # Each falling ball holds one thread for the whole drop, so allow plenty.
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), threads=16)
