# /// script
# dependencies = ["flask"]
# ///
import argparse
import math
import random

from flask import Flask, jsonify, send_file

# Board geometry, in pixels. (0, 0) is the top-left corner.
WIDTH, HEIGHT = 400, 560
BALL_R, PIN_R = 5, 3
ROWS = 12  # must be even, so the last row has a center pin that caps the divider
FIRST_ROW_Y, ROW_GAP = 120, 36
LAST_ROW_Y = FIRST_ROW_Y + (ROWS - 1) * ROW_GAP

# Physics. Gravity and bounce can be set on the command line, e.g. --bounce 0.3
parser = argparse.ArgumentParser()
parser.add_argument("--gravity", type=float, default=1200, help="pixels / second^2")
parser.add_argument("--bounce", type=float, default=0.5, help="fraction of speed kept after hitting something, 0 to 1")
args = parser.parse_args()
GRAVITY = args.gravity
BOUNCE = args.bounce
DT = 1 / 240  # seconds per physics step
STEPS_PER_FRAME = 4  # the page plays the path back at 60 frames per second


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
counts = {"left": 0, "right": 0}


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


@app.get("/board")
def board():
    return jsonify(
        width=WIDTH,
        height=HEIGHT,
        ball_r=BALL_R,
        pin_r=PIN_R,
        last_row_y=LAST_ROW_Y,
        pins=PINS,
        counts=counts,
    )


@app.post("/drop")
def drop():
    path, bucket = simulate_drop()
    counts[bucket] += 1
    return jsonify(path=path, bucket=bucket, counts=counts)


if __name__ == "__main__":
    app.run(debug=True, port=8000)
