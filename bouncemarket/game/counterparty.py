"""The counterparty: a few pretend traders who keep the market moving when few people are on.

Their buys follow from the ball's path, where it dropped from and the players' buys, so they
can be worked out again from the round log. Change how they buy, and RULES_VERSION (in this
package's __init__.py) with it.
"""
import math
import random
import struct

from bouncemarket.game.board import LAST_ROW_Y, WIDTH, wall_inset
from bouncemarket.game.pricing import prices_for_net

COUNTERPARTY_TRADERS = 5  # how many; one alone can't click fast enough to keep up with the ball near the bottom. Each player who buys takes one's place
COUNTERPARTY_FULL_LEAN = 0.5  # a ball this far from the middle towards a wall counts as fully on its side (see counterparty_guess())
COUNTERPARTY_EDGE = 0.03  # if a trader's guess and the price are closer than this, they let the price be
COUNTERPARTY_FIRST_LOOK_FRAMES = (8, 30)  # frames before a trader first looks after the drop, by chance between the two
COUNTERPARTY_CLICK_FRAMES = (7, 12)  # frames between a trader's clicks: 5 to 8.5 a second, a person's speed
COUNTERPARTY_PAUSE_FRAMES = (10, 30)  # frames a trader waits before looking again when the price looks right


def counterparty_guess(x, y):
    """The counterparty's guess at the chance the ball lands on the left, from where it is now.

    A naive rule: the further the ball is from the middle, and the lower it is, the surer it
    gets. lean is how far it is from the middle, from 0 in the middle to 1 once it's
    COUNTERPARTY_FULL_LEAN of the way to a wall (the walls lean in, so it's measured against the
    board's width at that height). depth is how far down it is, from 0 at the top to 1 at the last
    row of pins. It goes by the square of depth, which is roughly how the board behaves: high up,
    where the ball is says little, as it has many pins still to hit; low down, being even a
    little off the middle says a lot.
    """
    lean = min(1, abs(WIDTH / 2 - x) / (WIDTH / 2 - wall_inset(y)) / COUNTERPARTY_FULL_LEAN)
    depth = min(1, max(0, y / LAST_ROW_Y))
    sure = lean * depth ** 2 / 2
    return 0.5 + sure if x < WIDTH / 2 else 0.5 - sure


class Counterparty:
    """The counterparty for one drop: COUNTERPARTY_TRADERS pretend traders, so the game is never
    a dead market with nobody else on. Each does what a person playing might: look at the ball
    and the price; if the price is off from their guess (counterparty_guess()) by COUNTERPARTY_EDGE
    or more, click to buy one share of the side that's too cheap and look again a click later, as
    fast as a person clicks; otherwise look again after a short pause. Several clicks in a row
    make a burst, and two traders can click in the same frame. One trader alone can't click fast
    enough to keep up with the ball near the bottom; a few together can.

    They make way for real people: each different player who buys this round takes one trader's
    place, the highest-numbered trader first, so once COUNTERPARTY_TRADERS players have bought, the
    counterparty has stopped. A trader who has stopped stays stopped for the rest of the round, and
    the shares they bought stay in the price. Players are counted by their buys, not by who has the
    page open, as an open page may be nobody watching.

    Nobody collects on their shares; they only move the price. They never buy at close_frame or
    after. Each trader's timing has chance in it, from a generator seeded with exactly where the
    ball dropped and the trader's number, and the traders act in the order they come due (the
    lower number first in the same frame), each seeing every share bought before that frame. So
    the same drop with the same player buys always gives the same counterparty buys, and
    analysis/reader.py can work them out again from the round log.
    """

    def __init__(self, path, close_frame, start_x):
        self.path, self.close_frame = path, close_frame
        self.chances = [random.Random(struct.pack("<dB", start_x, number)) for number in range(COUNTERPARTY_TRADERS)]
        self.due = [self.between(number, COUNTERPARTY_FIRST_LOOK_FRAMES) for number in range(COUNTERPARTY_TRADERS)]
        self.buys = []  # every share the traders have bought so far, as [(frame, side)], in order
        self.net = 0  # shares bought on the left minus on the right, by the traders and the players counted so far
        self.players_counted = 0  # how many of the player buys are counted in net and buyers
        self.buyers = set()  # the different players among them

    def between(self, number, frames):
        """A whole number of frames from frames[0] to frames[1], by trader number's chance."""
        low, high = frames
        return low + int(self.chances[number].random() * (high - low + 1))

    def advance(self, frame, player_buys):
        """Make every look and click that's due by frame, in order. player_buys are the round's
        player buys so far, in the round log's form, (frame, _, player, side, ...), where player is
        anything that tells players apart (their id here, their key in the analysis): a trader
        acting at frame f sees only those made before f, so the answer is the same whenever it's
        asked. The traders act in frame order and a player buy is only ever made at the frame it is
        now, so the player buys are counted as we go, never going back over them."""
        while True:
            due, number = min((due, number) for number, due in enumerate(self.due))
            if due > frame or due >= self.close_frame:
                return
            while self.players_counted < len(player_buys) and player_buys[self.players_counted][0] < due:
                buy = player_buys[self.players_counted]
                self.net += 1 if buy[3] == "left" else -1
                self.buyers.add(buy[2])
                self.players_counted += 1
            if len(self.buyers) >= COUNTERPARTY_TRADERS - number:
                self.due[number] = math.inf  # a player has taken this trader's place
                continue
            gap = counterparty_guess(*self.path[due]) - prices_for_net(self.net)["left"]
            if abs(gap) < COUNTERPARTY_EDGE:
                self.due[number] = due + self.between(number, COUNTERPARTY_PAUSE_FRAMES)
                continue
            side = "left" if gap > 0 else "right"
            self.buys.append((due, side))
            self.net += 1 if side == "left" else -1
            self.due[number] = due + self.between(number, COUNTERPARTY_CLICK_FRAMES)


def shares_by_frame(buys, frame):
    """How many shares have been bought on each side up to and including a frame, given the
    buys so far as [(frame, side)] in frame order, the form Counterparty.buys has."""
    shares = {"left": 0, "right": 0}
    for buy_frame, side in buys:
        if buy_frame > frame:
            break
        shares[side] += 1
    return shares
