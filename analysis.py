# /// script
# dependencies = ["httpx", "starlette", "uvicorn"]  # the same as app.py, which this imports
# ///
"""Read the round log back out of the database, for analysis.

    uv run --env-file .env analysis.py buys.csv

writes one line per buy that any player has ever made, with the state of the game at that
moment: where the ball was and how fast it was moving, what had been bought on each side,
the price, and how the round came out. End the file name in .gz to have it compressed.

Only where the ball dropped from is needed to know its state at every frame: the round is
played again here from that one number, by the same simulate_drop() the server uses, and
checked against the path the server saved. To work with the rounds directly, in Python:

    import analysis
    for entry in analysis.rounds():
        ...  # entry["path"], entry["states"], entry["buys"], entry["bucket"], ...
"""
import csv
import gzip
import sys

import httpx

import app

ROUNDS_PER_REQUEST = 50  # a busy round is tens of kilobytes, so they are fetched a few at a time

COLUMNS = [
    "round",  # the round's id in the rounds table
    "started_at",  # when the ball dropped
    "commit_id",  # the version of the code that played the round
    "buy",  # 0 for the first buy of the round, 1 for the next, and so on
    "player",  # the player's key in the player_keys table
    "name",  # their name, if they have claimed one
    "frame",  # the server's frame when it took the buy, at 60 frames a second
    "seen_frame",  # the frame the player's page said it was showing; empty if it didn't say
    "side",  # the side bought
    "price",  # the price paid
    "points",  # the player's points just before the buy
    "x", "y", "vx", "vy",  # the ball at "frame": position in pixels, velocity in pixels a second
    "seen_x", "seen_y", "seen_vx", "seen_vy",  # the same at "seen_frame": what the player was looking at
    "players_left", "players_right",  # shares all players had bought on each side this round, before this buy
    "bot_left", "bot_right",  # shares the counterparty had bought on each side by this frame
    "close_frame",  # the frame betting closed at
    "bucket",  # where the ball landed
    "won",  # 1 if this share paid out, 0 if not
    "profit",  # what the share made: 1 - price if it won, -price if not
]


def fetch(db, table, column, select="*", limit=app.PAGE_SIZE):
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
    """A connection to the database, set up the way app.py sets up its own."""
    return httpx.Client(base_url=app.db.base_url, headers=app.db.headers, timeout=60)


def rounds(db=None):
    """Every logged round, oldest first, with its path and buys unpacked.

    Each is the row from the rounds table, with "path" as [(x, y)] and "buys" as
    [(frame, seen_frame, key, side, price, points)], plus:
    - "states": the ball's exact [(x, y, vx, vy)] at every frame, from playing the drop again,
      or None if playing it again didn't give the path that was saved. That happens if the
      physics in app.py has changed since: check out the round's "commit_id" to replay it.
    - "bot_buys": the counterparty's buys as [(frame, side)], worked out from the path by
      today's app.py, so they are only right if its counterparty rules haven't changed since.
    """
    db = db or connect()
    for row in fetch(db, "rounds", "id", limit=ROUNDS_PER_REQUEST):
        if row["format"] != app.LOG_FORMAT:
            raise SystemExit(f"round {row['id']} was packed as format {row['format']}, which this version of app.py can't unpack")
        row["path"] = app.unpack_path(from_bytea(row["path"]))
        row["buys"] = app.unpack_buys(from_bytea(row["buys"]))
        states, bucket = app.simulate_drop(row["start_x"], row["gravity"], row["bounce"])
        row["states"] = states if app.positions(states) == row["path"] and bucket == row["bucket"] else None
        row["bot_buys"] = app.counterparty_buys(row["path"], row["close_frame"])
        yield row


def names(db=None):
    """The name of every player who has claimed one, by their key in the round log: {key: name}."""
    db = db or connect()
    name_by_id = {row["id"]: row["name"] for row in fetch(db, "players", "id", select="id,name")}
    return {row["key"]: name_by_id[row["id"]] for row in fetch(db, "player_keys", "key") if row["id"] in name_by_id}


def ball(entry, frame):
    """The ball at a frame of a round, as [x, y, vx, vy], rounded to hundredths to keep the
    spreadsheet small (entry["states"] has them exactly). The velocity is left empty if the
    round couldn't be replayed, and all four are if there is no such frame."""
    if frame is None or not 0 <= frame < len(entry["path"]):
        return ["", "", "", ""]
    if entry["states"] is None:
        return [*entry["path"][frame], "", ""]
    return [round(number, 2) for number in entry["states"][frame]]


def buy_lines(entry, name_by_key):
    """One line of the spreadsheet for each buy in a round, as lists in the order of COLUMNS.
    Also counts the buys whose price isn't what today's app.py would have charged."""
    lines = []
    odd_prices = 0
    player_shares = {"left": 0, "right": 0}
    for number, (frame, seen_frame, key, side, price, points) in enumerate(entry["buys"]):
        bot_shares = app.shares_by_frame(entry["bot_buys"], frame)
        expected = app.prices_for(player_shares["left"] + bot_shares["left"], player_shares["right"] + bot_shares["right"])
        odd_prices += expected[side] != price
        won = side == entry["bucket"]
        lines.append([
            entry["id"], entry["started_at"], entry["commit_id"], number, key, name_by_key.get(key, ""),
            frame, "" if seen_frame is None else seen_frame, side, price, points,
            *ball(entry, frame), *ball(entry, seen_frame),
            player_shares["left"], player_shares["right"], bot_shares["left"], bot_shares["right"],
            entry["close_frame"], entry["bucket"], int(won), round(won - price, 2),
        ])
        player_shares[side] += 1
    return lines, odd_prices


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: uv run --env-file .env analysis.py buys.csv   (or buys.csv.gz)")
    out_path = sys.argv[1]
    db = connect()
    name_by_key = names(db)
    n_rounds = n_buys = odd_prices = 0
    players = set()
    not_replayed = {}  # {commit_id: how many of its rounds didn't replay}
    opener = gzip.open if out_path.endswith(".gz") else open
    with opener(out_path, "wt", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(COLUMNS)
        for entry in rounds(db):
            lines, odd = buy_lines(entry, name_by_key)
            writer.writerows(lines)
            n_rounds += 1
            n_buys += len(lines)
            odd_prices += odd
            players.update(buy[2] for buy in entry["buys"])
            if entry["states"] is None:
                not_replayed[entry["commit_id"]] = not_replayed.get(entry["commit_id"], 0) + 1

    print(f"{n_buys} buys by {len(players)} players in {n_rounds} rounds, written to {out_path}")
    for commit_id, count in not_replayed.items():
        print(
            f"{count} rounds played by version {commit_id} didn't replay to the path that was saved, so their "
            "velocities are empty. The physics has changed since: check out that version to replay them."
        )
    if odd_prices:
        print(
            f"{odd_prices} buys weren't at the price today's app.py would charge, so the pricing or the counterparty "
            "has changed since they were made. Their price is right, but their bot_left and bot_right may not be."
        )


if __name__ == "__main__":
    main()
