"""The physics of one drop: where the ball is at every frame, and the bucket it lands in.

A drop follows entirely from the five numbers it is started with, on any computer, which is
what lets a round be saved as those numbers and played again later (see docs/randomness.md).
Change anything here and old rounds no longer replay under the new code, only under the
commit they were played by.
"""
import math
import random

from bouncemarket.game.board import BALL_R, HEIGHT, LAST_ROW_Y, PINS, PIN_R, WALL_LENGTH, WALL_SLANT, WIDTH

DT = 1 / 240  # seconds per physics step
STEPS_PER_FRAME = 4  # the page is sent one ball position per frame
FRAME_SECONDS = STEPS_PER_FRAME * DT  # 1/60 second


def simulate_drop(start_x, gravity, bounce, nudge=0.0, nudge_seed=None):
    """Drop one ball from start_x along the top. Returns its state at every frame, as
    [(x, y, vx, vy)] (position and velocity), and the bucket it lands in.

    At each pin bounce the ball's direction is turned by a small random angle, typically nudge
    radians either way, drawn from a generator seeded with nudge_seed. The seed is a secret
    until the round is over. Without the nudges, the whole drop follows from where the ball
    starts, and players worked that out from the path the pages are sent: by about halfway down,
    a script could know the winner for certain (see docs/randomness.md). The
    path is chaotic, so even this small a turn, too small to see, soon makes the rest of the
    drop impossible to work out from what has been seen of it. With no seed, or a nudge of 0,
    there are no turns, as in every drop played before they were added.

    The same start_x, gravity, bounce, nudge and nudge_seed always give exactly the same drop,
    on any computer. The sums use only + - * / and square roots, which every computer rounds
    the same way (math.hypot, for one, is not promised to, nor are math.cos and math.sin, so the
    turn is made without them). The generator's random() is promised to give the same numbers
    from the same seed in every version of Python. That matters because the path is chaotic:
    the tiniest difference early on lands the ball in the other bucket. It is what lets the
    round log save a few numbers for a drop and the analysis replay it later, velocities included.
    """
    turns = random.Random(nudge_seed) if nudge and nudge_seed is not None else None
    # Each turn is by 2 * atan(t), for t drawn evenly from -reach to reach: about 2t, as t is
    # small, so the angle is spread evenly over twice that and its standard deviation is nudge.
    reach = nudge * math.sqrt(3) / 2
    x, y = start_x, 0.0
    vx, vy = 0.0, 0.0
    states = []
    step = 0
    while y < HEIGHT - BALL_R:
        if step % STEPS_PER_FRAME == 0:
            states.append((x, y, vx, vy))
        step += 1

        vy += gravity * DT
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
                    vx += (1 + bounce) * speed_into_wall * nx
                    vy += (1 + bounce) * speed_into_wall * ny

        # Divider between the buckets: a wall from the last row's center pin to the floor.
        if y > LAST_ROW_Y and abs(x - WIDTH / 2) < BALL_R:
            side = 1 if x > WIDTH / 2 else -1
            x, vx = WIDTH / 2 + side * BALL_R, side * abs(vx) * bounce

        # Pins.
        for pin_x, pin_y in PINS:
            dx, dy = x - pin_x, y - pin_y
            dist = math.sqrt(dx * dx + dy * dy)
            if dist < BALL_R + PIN_R:
                # (nx, ny) points from the pin to the ball.
                nx, ny = dx / dist, dy / dist
                # Move the ball back out so it just touches the pin.
                x, y = pin_x + nx * (BALL_R + PIN_R), pin_y + ny * (BALL_R + PIN_R)
                # If the ball is moving into the pin, reflect that part of its velocity.
                speed_into_pin = -(vx * nx + vy * ny)
                if speed_into_pin > 0:
                    vx += (1 + bounce) * speed_into_pin * nx
                    vy += (1 + bounce) * speed_into_pin * ny
                    if turns:
                        # Turn (vx, vy) by the angle whose half has tangent t, which keeps the
                        # speed the same: cos and sin of that angle, from t alone.
                        t = (2 * turns.random() - 1) * reach
                        cos, sin = (1 - t * t) / (1 + t * t), 2 * t / (1 + t * t)
                        vx, vy = vx * cos - vy * sin, vx * sin + vy * cos

    bucket = "left" if x < WIDTH / 2 else "right"
    return states, bucket


def replay_drop(numbers):
    """simulate_drop() for a drop already played, from the numbers it was saved with: a round
    in the round log, or a card's trajectory. Drops played before the nudges have none saved,
    and replay without them, as they were played."""
    return simulate_drop(numbers["start_x"], numbers["gravity"], numbers["bounce"],
                         numbers.get("nudge") or 0.0, numbers.get("nudge_seed"))


def positions(states):
    """The ball's position at every frame, rounded to tenths of a pixel: the path the game itself uses."""
    return [(round(x, 1), round(y, 1)) for x, y, vx, vy in states]
