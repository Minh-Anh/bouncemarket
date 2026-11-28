"""The public "game" message every page is sent: the state of the game and, while the ball is
falling, its next positions.
"""
import asyncio
import random
import secrets
import time

from bouncemarket import config, market, state, streams

# The order pages are sent each broadcast in. Shuffling a thousand pages ten times a second
# with the operating system's randomness takes milliseconds each time, during which the game
# waits, so this is a Mersenne Twister, but its own, seeded from the operating system's
# randomness and used for nothing else: the order is never shown to anyone, and nothing about
# the ball could be learned from it.
send_order_random = random.Random(secrets.randbits(256))


def rounded(position):
    """A ball position rounded to POSITION_DECIMALS places, to keep messages short."""
    if config.POSITION_DECIMALS == 0:
        return [round(position[0]), round(position[1])]  # whole numbers, sent without a ".0"
    return [round(position[0], config.POSITION_DECIMALS), round(position[1], config.POSITION_DECIMALS)]


def public_state():
    """The state of the game that every page is sent, without the ball: the "game" message
    is this and, while the ball is falling, its next positions (see broadcast())."""
    message = {
        "dropping": state.game["dropping"],
        "seconds_to_drop": max(0, state.game["drops_at"] - time.time()),
        "prices": market.prices(),
        "costs": market.costs(),  # what the buy buttons charge
        "betting_open": market.betting_open(),
        "online": len(state.streams),
        "last_bucket": state.game["last_bucket"],
    }
    if state.game["update_at"] is not None:
        # Seconds until the announced update, which the page counts down on its own clock.
        message["update_in"] = max(0, round(state.game["update_at"] - time.time()))
    return message


def catch_up():
    """The "game" message for a page that has just connected: the game as it stands and, if the
    ball is falling, the positions from where it is now up to the last one everyone else has
    been sent. broadcast() only ever sends each position once, so without this a page that
    connects during a drop would be missing a stretch of the path: a page just opened would
    show no ball for a moment, and a page whose stream was cut and came back, as every page's
    is when the game moves to a new server, would see the ball freeze and jump. No position is
    sent here that hasn't gone to every other page already."""
    message = public_state()
    if state.game["dropping"]:
        frame = market.current_frame()
        start = min(frame, state.game["sent_frames"])
        message["frame"] = frame
        message["first_position"] = start
        message["positions"] = [rounded(position) for position in state.game["path"][start:state.game["sent_frames"]]]
    return streams.sse("game", message)


def parting_message():
    """The last "game" message the old server sends before it ends the streams: the ball's next
    RECONNECT_AHEAD_FRAMES positions, so that on every page the ball keeps falling while the
    browser connects to the new server. The new server sends these positions again; a page
    takes no harm from getting one twice.

    While betting is open this keeps to what broadcast() lets a page see, so a handover never
    shows anyone more of the path than they could have had without one. The end of the path
    before the close, past the furthest broadcast() sends early on, only ever goes out
    BUFFER_FRAMES ahead, and nothing past the last row goes out at all. So for a handover in
    the last second or so before the close, a page gets less than it needs to cover the wait,
    and the ball may stand still for a moment until it has connected to the new server."""
    message = public_state()
    if state.game["dropping"]:
        frame = market.current_frame()
        end = min(frame + config.RECONNECT_AHEAD_FRAMES + 1, len(state.game["path"]))
        if market.betting_open(frame):
            sent_early = state.game["close_frame"] - config.FINAL_STRETCH_FRAMES + config.EARLY_AHEAD_FRAMES
            end = min(end, max(frame + config.BUFFER_FRAMES + 1, sent_early), state.game["close_frame"] + config.BUFFER_FRAMES)
        start = min(state.game["sent_frames"], end)
        message["frame"] = frame
        message["first_position"] = start
        message["positions"] = [rounded(position) for position in state.game["path"][start:end]]
    return streams.sse("game", message)


async def broadcast():
    """Send every page the public state of the game: up to 10 times a second, but only as often as needed.

    The message is built once and the same text goes to everyone. While the ball is falling
    it carries the next positions on the path, ahead of time. The page plays them back on its
    own clock, so it always has some in hand and the ball moves smoothly even when the
    network delivers messages unevenly.

    The pages are sent it in a new random order every time. Each page's stream is written in
    the order its message was queued, and with a thousand pages open the last is written a
    noticeable moment after the first. In a fixed order, the page that connected first would
    always hear first, and a script that kept its stream open would always be a step ahead of
    everyone else. During a drop each broadcast is also numbered, and its timing kept for the
    round log (see pack_timing()).

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
        await asyncio.sleep(1 / config.BROADCASTS_PER_SECOND)
        if state.game["role"] == "retired":
            return  # the game has been handed to a new server, which sends these now
        ticks_since_sent += 1
        path = state.game["path"]
        dropping = state.game["dropping"]
        if dropping:
            frame = market.current_frame()
            final_stretch = frame >= state.game["close_frame"] - config.FINAL_STRETCH_FRAMES
            ahead = config.BUFFER_FRAMES if final_stretch else config.EARLY_AHEAD_FRAMES
            # Early on, send only when the pages would otherwise have less than BUFFER_FRAMES in hand by the next broadcast.
            running_low = state.game["sent_frames"] <= frame + config.BUFFER_FRAMES + config.FRAMES_PER_BROADCAST
            due = final_stretch or running_low
        else:
            due = ticks_since_sent >= config.BROADCASTS_PER_SECOND
        if not due and dropping == was_dropping:
            continue
        ticks_since_sent = 0
        was_dropping = dropping

        message = public_state()
        if dropping:
            # The positions the pages don't have yet, and the frame number of the first of them.
            # Never past close_frame + BUFFER_FRAMES (the last row) while betting is open, however early it is.
            end = min(frame + ahead + 1, len(path))
            if market.betting_open():
                end = min(end, state.game["close_frame"] + config.BUFFER_FRAMES)
            start = min(state.game["sent_frames"], end)
            message["frame"] = frame
            message["first_position"] = start
            message["positions"] = [rounded(position) for position in path[start:end]]
            state.game["sent_frames"] = max(state.game["sent_frames"], end)

        text = streams.sse("game", message)
        tag = None
        queues = list(state.streams)
        send_order_random.shuffle(queues)
        if dropping:
            tag = (state.game["started_at"], len(state.game["broadcasts"]))
            state.game["broadcasts"].append([time.time() - state.game["started_at"], None, None, len(queues), 0])
        for queue in queues:
            streams.send(queue, text, tag)
