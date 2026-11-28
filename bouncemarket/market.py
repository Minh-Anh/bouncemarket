"""The market of the round being played: whether shares can be bought right now, and at what price.

The prices themselves are worked out in game/pricing.py; this applies them to the shares the
players and the counterparty have bought so far.
"""
import time

from bouncemarket import state
from bouncemarket.game import counterparty, physics, pricing


def current_frame():
    """How many frames have passed since the ball dropped."""
    return int((time.time() - state.game["started_at"]) / physics.FRAME_SECONDS)


def betting_open(frame=None):
    """Shares can be bought from the moment the ball drops until it reaches close_frame.
    Asks about the frame given, or about right now if none is."""
    if frame is None:
        frame = current_frame()
    return state.game["dropping"] and frame < state.game["close_frame"]


def counterparty_shares(frame=None):
    """How many shares the counterparty has bought on each side so far this round: up to the
    frame given, or up to right now if none is.

    It never collects on them. They only move the price, towards its guess at where the ball
    will land. Its buys are worked out as they come due, here, before any price is given.
    """
    if not state.game["dropping"]:
        return {"left": 0, "right": 0}
    frame = current_frame() if frame is None else frame
    state.game["counterparty"].advance(frame, state.game["buys"])
    return counterparty.shares_by_frame(state.game["counterparty"].buys, frame)


def market(frame=None):
    """The shares bought on each side at the frame given, or right now if none is, by players and
    the counterparty together."""
    theirs = counterparty_shares(frame)
    return state.game["shares"]["left"] + theirs["left"], state.game["shares"]["right"] + theirs["right"]


def prices(frame=None):
    """Each side's price at the frame given, or right now if none is: the chance the market gives
    it. What a share is charged is costs(), a little more, as buying moves the price."""
    return pricing.prices_for(*market(frame))


def costs(frame=None):
    """What buying the next share on each side is charged at the frame given, or right now if none is."""
    return pricing.costs_for(*market(frame))
