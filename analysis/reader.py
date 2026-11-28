"""Reads the round log back out of the database, for analysis (see docs/round-log.md).

    from analysis import reader
    for entry in reader.rounds():
        ...  # entry["path"], entry["states"], entry["buys"], entry["bucket"], ...

Only where the ball dropped from, and the seed of its hidden nudges, are needed to know its
state at every frame: the round is played again here from those, by the same simulate_drop()
the server uses, and checked against the path the server saved. Rounds played by older rules
are read by those rules, which are kept here.
"""
import httpx

from bouncemarket import config, packing
from bouncemarket.game import RULES_VERSION, board, counterparty, physics, pricing

ROUNDS_PER_REQUEST = 50  # a busy round is tens of kilobytes, so they are fetched a few at a time


def fetch(db, table, column, select="*", limit=config.PAGE_SIZE):
    """Every row of a table, in order of a column whose values are all different. The database
    hands over a limited number of rows at a time, so each request asks for the rows after the
    last one we were given."""
    params = {"select": select, "order": column, "limit": limit}
    while True:
        response = db.get("/" + table, params=params)
        response.raise_for_status()
        rows = response.json()
        yield from rows
        if len(rows) < limit:
            return
        params[column] = f"gt.{rows[-1][column]}"


def from_bytea(text):
    """The bytes of a bytea (raw bytes) column, which the database sends as \\x and then hexadecimal."""
    return bytes.fromhex(text[2:])


def connect():
    """A connection to the database, set up the way the server sets up its own (bouncemarket/db.py)."""
    # Imported here, not at the top: it needs the database's settings, which a script that only
    # reads a local copy of the rounds can do without.
    from bouncemarket import db as server
    return httpx.Client(base_url=server.client.base_url, headers=server.client.headers, timeout=60)


def rounds(db=None):
    """Every logged round, oldest first, with its path and buys unpacked.

    Each is the row from the rounds table, with "path" as [(x, y)] and "buys" as
    [(frame, seen_frame, key, side, price, points)], plus:
    - "states": the ball's exact [(x, y, vx, vy)] at every frame, from playing the drop again,
      or None if playing it again didn't give the path that was saved. That happens if the
      physics (bouncemarket/game/) has changed since: check out the round's "commit_id" to replay it.
    - "bot_buys": the counterparty's buys as [(frame, side)], worked out again under the rules
      the round was played by (its "rules" version): from the path alone for version 1, and
      from the path, start_x and the player buys from version 2 on, today's Counterparty.
      They are only right if those rules haven't changed since without a new version.
    - "prices_for": what a share was charged in the round, by the rules it was played by, as a
      function like pricing.costs_for(): each side's fraction of the shares for version 1, the price
      before the buy for version 2, the market maker's cost of the buy for version 3 (never more
      than 0.99), and today's cost for version 4.
    - "timing": the round's timing, as packing.unpack_timing() gives it: when each broadcast was made
      and written to the pages, and for each buy, lined up with "buys", when it was taken and
      the newest broadcast the buyer had been written by then. None for rounds logged before it.
    """
    db = db or connect()
    for row in fetch(db, "rounds", "id", limit=ROUNDS_PER_REQUEST):
        if row["format"] != packing.LOG_FORMAT:
            raise SystemExit(f"round {row['id']} was packed as format {row['format']}, which this version of the code can't unpack")
        unpack(row)
        row["prices_for"] = {1: prices_v1, 2: pricing.prices_for, 3: costs_v3, 4: pricing.costs_for}[rules(row)]
        yield row


def unpack(row):
    """Unpack a row of the rounds table where it stands: its "path", "buys" and "timing", and
    add its "states" and "bot_buys" (see rounds()). What it leaves is plain data, which
    fetch_rounds.py keeps in a file."""
    row["path"] = packing.unpack_path(from_bytea(row["path"]))
    row["buys"] = packing.unpack_buys(from_bytea(row["buys"]))
    states, bucket = physics.replay_drop(row)
    row["states"] = states if physics.positions(states) == row["path"] and bucket == row["bucket"] else None
    row["bot_buys"] = bot_buys(row)
    row["timing"] = packing.unpack_timing(from_bytea(row["timing"])) if row.get("timing") else None


def rules(row):
    """Which pricing and counterparty rules a logged round was played by. Rounds logged before
    the column existed were version 1. Versions 2 to 4 have the same counterparty; version 3
    charges each buy the market maker's cost (pricing.costs_for_net()), not the price before it, and
    version 4 lets that cost go up to 1.00, where 3 stopped at 0.99."""
    version = row.get("rules", 1)
    if version not in (1, 2, 3, 4) or version > RULES_VERSION:
        raise SystemExit(f"round {row['id']} was played by rules version {version}, which this version of the code doesn't have")
    return version


def bot_buys(row):
    """The counterparty's buys in a logged round, as [(frame, side)], under the rules it was played by."""
    if rules(row) == 1:
        return counterparty_v1(row["path"], row["close_frame"])
    bot = counterparty.Counterparty(row["path"], row["close_frame"], row["start_x"])
    bot.advance(row["close_frame"], row["buys"])
    return bot.buys


def prices_v1(left, right):
    """The pricing of version 1 rounds: each side's fraction of all the shares bought, counting
    3 pretend shares on each side so both started at 0.50."""
    left, right = left + 3, right + 3
    left_price = min(0.99, max(0.01, round(left / (left + right), 2)))
    return {"left": left_price, "right": round(1 - left_price, 2)}


def costs_v3(left, right):
    """What a share was charged in version 3 rounds: today's cost, but never more than 0.99."""
    return {side: min(0.99, cost) for side, cost in pricing.costs_for(left, right).items()}


def costs_for_net(version, net):
    """What a share was charged in a round played by rules version 3 or later, as
    pricing.costs_for_net() gives it for today's rules."""
    return costs_v3(net, 0) if version == 3 else pricing.costs_for_net(net)


def counterparty_v1(path, close_frame):
    """The counterparty's buys under its first rules, which played every round logged before the
    counterparty column existed: one share at the drop, then one at a time on whichever half of
    the board the ball was in, every 60 frames with the ball at the top, speeding up to every 6
    by its height when betting closed."""
    bottom = max(1, path[close_frame][1])
    buys = []
    frame = 0
    while frame < close_frame:
        x, y = path[frame]
        buys.append((frame, "left" if x < board.WIDTH / 2 else "right"))
        depth = min(1, max(0, y / bottom))
        frame += round(60 - (60 - 6) * depth)
    return buys


def names(db=None):
    """The name of every player who has claimed one, by their key in the round log: {key: name}."""
    db = db or connect()
    name_by_id = {row["id"]: row["name"] for row in fetch(db, "players", "id", select="id,name")}
    return {row["key"]: name_by_id[row["id"]] for row in fetch(db, "player_keys", "key") if row["id"] in name_by_id}
