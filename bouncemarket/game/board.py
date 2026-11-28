"""The board: a trapezoid with pins. Sizes are in pixels, and (0, 0) is the top-left corner.

The physics, the counterparty and the pages all work from these numbers; the pages are sent
them by /board (see web/pages.py).
"""
import math

WIDTH, HEIGHT = 400, 560
BALL_R, PIN_R = 5, 3
ROWS = 12  # must be even, so the last row has a center pin that caps the divider
FIRST_ROW_Y, ROW_GAP = 120, 36
LAST_ROW_Y = FIRST_ROW_Y + (ROWS - 1) * ROW_GAP

# The side walls lean inwards, so the board is a trapezoid: WIDTH across the top, and
# WALL_SLANT narrower on each side at the floor. The pins squeeze together as the walls close in.
WALL_SLANT = 60
WALL_LENGTH = math.sqrt(WALL_SLANT**2 + HEIGHT**2)


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
