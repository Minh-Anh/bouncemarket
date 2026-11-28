"""Messages to the open pages. Each page holds one stream of server-sent events (see events()
in web/play.py), and each stream has a queue here of what is waiting to be sent on it.
"""
import json
import time

from bouncemarket import state


def sse(event, data):
    """Format one server-sent event, the browser's built-in format for a stream of messages."""
    # No spaces after commas and colons: the same JSON, a little smaller to send to every page.
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def send(queue, message, tag=None):
    """Queue a message for one page. A page that has fallen far behind misses it instead.
    tag is for broadcasts only (see streams)."""
    if not queue.full():
        queue.put_nowait((message, tag))


def send_to_player(player_id, message):
    """Queue a message for every page this player has open (they may have more than one tab)."""
    for queue, owner in state.streams.items():
        if owner == player_id:
            send(queue, message)


def note_written(player_id, tag):
    """Note that a broadcast has just been written to one of this player's pages: handed to
    the network, as events() only goes on to the next message once the last one is sent."""
    started_at, index = tag
    if not state.game["dropping"] or state.game["started_at"] != started_at:
        return  # a broadcast of a round that has ended
    now = time.time() - started_at
    record = state.game["broadcasts"][index]
    record[1] = now if record[1] is None else min(record[1], now)
    record[2] = now if record[2] is None else max(record[2], now)
    record[4] += 1
    newest = state.written_to.get(player_id)
    if newest is None or now >= newest[1]:
        state.written_to[player_id] = (index, now)
