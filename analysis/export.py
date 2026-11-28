"""Writes the whole round log as a spreadsheet, one line per buy.

    uv run --env-file .env -m analysis.export buys.csv

Each line is one buy that any player has ever made, with the state of the game at that
moment: where the ball was and how fast it was moving, what had been bought on each side,
the price, and how the round came out. End the file name in .gz to have it compressed.
"""
import csv
import gzip
import sys

from analysis import reader
from bouncemarket.game import counterparty

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
    # From the round's timing; empty for rounds logged before it existed (see db/README.md):
    "taken_ms",  # when the server took the buy, in milliseconds since the drop: "frame", to the microsecond
    "answer_ms",  # how long after the newest broadcast was written to the player's pages the buy was taken: network both ways plus the player's own reaction
    "send_delay_ms",  # how long after that broadcast was made it was written to the player: their place in that broadcast's random order
]


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
    Also counts the buys whose price isn't what today's rules would have charged."""
    lines = []
    odd_prices = 0
    player_shares = {"left": 0, "right": 0}
    for number, (frame, seen_frame, key, side, price, points) in enumerate(entry["buys"]):
        bot_shares = counterparty.shares_by_frame(entry["bot_buys"], frame)
        expected = entry["prices_for"](player_shares["left"] + bot_shares["left"], player_shares["right"] + bot_shares["right"])
        odd_prices += expected[side] != price
        won = side == entry["bucket"]
        lines.append([
            entry["id"], entry["started_at"], entry["commit_id"], number, key, name_by_key.get(key, ""),
            frame, "" if seen_frame is None else seen_frame, side, price, points,
            *ball(entry, frame), *ball(entry, seen_frame),
            player_shares["left"], player_shares["right"], bot_shares["left"], bot_shares["right"],
            entry["close_frame"], entry["bucket"], int(won), round(won - price, 2),
            *timing_cells(entry, number),
        ])
        player_shares[side] += 1
    return lines, odd_prices


def timing_cells(entry, number):
    """The taken_ms, answer_ms and send_delay_ms cells for a round's buy number, each empty when
    the round has no timing or the buyer hadn't been written a broadcast of the round yet."""
    if entry["timing"] is None:
        return ["", "", ""]
    taken, seen, broadcast = entry["timing"]["buys"][number]
    ms = lambda seconds: round(seconds * 1000, 3)
    if seen is None or broadcast is None:
        return [ms(taken), "", ""]
    built = entry["timing"]["broadcasts"][broadcast][0]
    return [ms(taken), ms(taken - seen), ms(seen - built)]


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: uv run --env-file .env -m analysis.export buys.csv   (or buys.csv.gz)")
    out_path = sys.argv[1]
    db = reader.connect()
    name_by_key = reader.names(db)
    n_rounds = n_buys = odd_prices = 0
    players = set()
    not_replayed = {}  # {commit_id: how many of its rounds didn't replay}
    opener = gzip.open if out_path.endswith(".gz") else open
    with opener(out_path, "wt", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(COLUMNS)
        for entry in reader.rounds(db):
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
            "velocities are empty. Either the physics has changed since (check out that version to replay them), "
            "or they were played before start_x was rounded to what the database keeps, and can't be replayed exactly."
        )
    if odd_prices:
        print(
            f"{odd_prices} buys weren't at the price today's rules would charge, so the pricing or the counterparty "
            "has changed since they were made. Their price is right, but their bot_left and bot_right may not be."
        )


if __name__ == "__main__":
    main()
