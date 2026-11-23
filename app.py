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
BUFFER_FRAMES = 5  # the ball's position is sent this many frames early, so the page can play back smoothly
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

# The one shared game that every player watches and bets on. Rounds repeat forever:
# a countdown, then a ball drops. "path" is the ball's position at every frame (None
# before the first drop), "bucket" is where it lands, and betting closes when it reaches
# "close_frame". Players only learn the path one frame at a time, and the bucket when
# the ball lands.
game = {
    "dropping": False,
    "drops_at": time.time() + COUNTDOWN_SECONDS,  # when the countdown ends
    "started_at": 0,  # when the ball dropped
    "path": None,
    "bucket": None,
    "close_frame": 0,
    "last_bucket": None,  # where the previous ball landed
    "landed": {"left": 0, "right": 0},  # how many balls have landed in each bucket
}

# One entry per player, keyed by the random id their browser sends:
# {"points": 10, "bets": {"left": 0, "right": 0}, "last_won": 0, "seen_at": 0}
players = {}

# Requests and the round loop run on different threads. Each takes this lock before
# touching game or players, so they never see each other's half-finished changes.
lock = threading.Lock()


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


def get_player(player_id):
    """Look up a player, creating them with 10 points the first time we see their id."""
    if player_id not in players:
        players[player_id] = {"points": 10, "bets": {"left": 0, "right": 0}, "last_won": 0, "seen_at": 0}
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


def settle():
    """Pay out every player's bets on the ball that just landed.

    All bets, the players' and the counterparty's, go into one pot. The pot is split among
    the points bet on the winning bucket, so players can only win what others lose.
    """
    bucket = game["bucket"]
    theirs = counterparty_bets(game["path"], game["close_frame"])
    pot = theirs["left"] + theirs["right"]
    winning_points = theirs[bucket]
    for player in players.values():
        pot += player["bets"]["left"] + player["bets"]["right"]
        winning_points += player["bets"][bucket]

    for player in players.values():
        my_total = player["bets"]["left"] + player["bets"]["right"]
        if winning_points == 0:
            payout = my_total  # nobody bet on the winning bucket, so everyone gets their bet back
        else:
            payout = pot * player["bets"][bucket] / winning_points
        player["last_won"] = round(payout - my_total, 2)
        player["points"] = round(player["points"] + player["last_won"], 2)
        player["bets"] = {"left": 0, "right": 0}

    game["last_bucket"] = bucket
    game["landed"][bucket] += 1


def run_rounds():
    """The round loop: wait for the countdown, drop a ball, wait for it to land, pay out. Forever."""
    while True:
        time.sleep(COUNTDOWN_SECONDS)
        path, bucket = simulate_drop()
        close_frame = next(i for i, (x, y) in enumerate(path) if y > LAST_ROW_Y)
        with lock:
            game.update(dropping=True, started_at=time.time(), path=path, bucket=bucket, close_frame=close_frame)
        time.sleep(len(path) * FRAME_SECONDS)
        with lock:
            settle()
            game["dropping"] = False
            game["drops_at"] = time.time() + COUNTDOWN_SECONDS


def snapshot(player_id):
    """Everything one player's page shows.

    While the ball is falling, the snapshot describes the game BUFFER_FRAMES ahead of now.
    The page holds each snapshot until its frame is due, so it always has a few in hand and
    can play them back smoothly even when the network delivers them unevenly. That is as
    far ahead of time as a page ever sees.
    """
    with lock:
        now = time.time()
        player = get_player(player_id)
        player["seen_at"] = now
        path, frame = game["path"], current_frame()
        if game["dropping"]:
            frame += BUFFER_FRAMES

        # "others" is what everyone except this player has bet: the counterparty plus the other players.
        if game["dropping"]:
            others = counterparty_bets(path, min(frame + 1, game["close_frame"]))
        else:
            others = {"left": 0, "right": 0}
        for other in players.values():
            if other is not player:
                others["left"] += other["bets"]["left"]
                others["right"] += other["bets"]["right"]

        return {
            "dropping": game["dropping"],
            "frame": frame,  # the frame this snapshot describes
            "seconds_to_drop": max(0, game["drops_at"] - now),
            # Between drops the ball rests where it landed.
            "ball": path[min(frame, len(path) - 1)] if path else None,
            "betting_open": not game["dropping"] or frame < game["close_frame"],
            "points": player["points"],
            "bets": player["bets"],
            "others": others,
            "online": sum(1 for p in players.values() if now - p["seen_at"] < 2),
            "last_bucket": game["last_bucket"],
            "last_won": player["last_won"],
            "landed": game["landed"],
        }


@app.get("/board")
def board():
    return jsonify(
        width=WIDTH,
        height=HEIGHT,
        ball_r=BALL_R,
        pin_r=PIN_R,
        last_row_y=LAST_ROW_Y,
        pins=PINS,
        buffer_frames=BUFFER_FRAMES,
    )


@app.get("/events")
def events():
    """Stream snapshots to one player's page for as long as it stays open.

    These are server-sent events, the browser's built-in format for a stream of messages.
    While the ball is falling we send 60 snapshots a second; otherwise 10 is plenty.
    """
    player_id = request.args["player"]

    def stream():
        while True:
            yield f"data: {json.dumps(snapshot(player_id))}\n\n"
            time.sleep(FRAME_SECONDS if game["dropping"] else 0.1)

    # The headers ask anything between the server and the browser not to hold events back.
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return Response(stream(), mimetype="text/event-stream", headers=headers)


@app.post("/bet")
def bet():
    """Change a player's bet on one side by one point. While the ball is falling, bets can only go up."""
    side, change = request.json["side"], request.json["change"]
    if side not in ("left", "right") or change not in (1, -1):
        return jsonify(error="Bad bet."), 400
    with lock:
        player = get_player(request.json["player"])
        bets = player["bets"]
        if game["dropping"] and change < 0:
            return jsonify(error="Bets can't be lowered once the ball drops."), 400
        if game["dropping"] and current_frame() >= game["close_frame"]:
            return jsonify(error="Betting is closed."), 400
        if bets[side] + change < 0:
            return jsonify(error="Bets can't be negative."), 400
        if bets["left"] + bets["right"] + change > player["points"]:
            return jsonify(error="You can't bet more than your points."), 400
        bets[side] += change
    return jsonify(ok=True)


if __name__ == "__main__":
    threading.Thread(target=run_rounds, daemon=True).start()
    # Render tells us which port to listen on through the PORT environment variable.
    # Every open page holds one thread for its stream, so this is the most players at once.
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), threads=100)
