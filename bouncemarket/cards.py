"""The best-round card. Each player has one: their best single round, for them to show off
outside the game.

A round becomes the best when it settles with a profit of more than CARD_MIN_PROFIT and more
than the player's best so far (a tie doesn't replace it). The card is a record of the round,
made here from the buys the server took itself: nothing a page sends can make or change one.
Each record is a row of the best_rounds table, and stays there when a better round replaces
it, so the link to it keeps working. frontend/card.js draws it, on the player's own
page and on the public page at /r/<share_id> (see web/share.py).
"""
import asyncio
import datetime
import re
import secrets

import httpx

from bouncemarket import config, db, state, streams
from bouncemarket.game import pricing

SHARE_ID = re.compile(r"[A-Za-z0-9_-]{8}")  # what secrets.token_urlsafe(6) makes: 6 random bytes as 8 characters
CARD_COLUMNS = "share_id,player,username,round_started_at,settled_at,outcome,frames,close_frame,trades,prices,cost,payout,profit,trajectory,cleared"


def price_line():
    """The left side's price over the round that just finished, from the drop until trading
    closed, as [[frame, price]]: the first frame, then every frame where the price changed.
    The right side's price is always 1 minus it. In a round with more than PRICE_POINTS
    changes, the price at PRICE_POINTS evenly spaced frames instead."""
    close = state.game["close_frame"]
    state.game["counterparty"].advance(close, state.game["buys"])  # all its buys, up to the close
    buys = sorted([*state.game["counterparty"].buys, *((buy[0], buy[3]) for buy in state.game["buys"])])
    shares = {"left": 0, "right": 0}
    left = []  # the left price at every frame, after that frame's buys
    taken = 0
    for frame in range(close + 1):
        while taken < len(buys) and buys[taken][0] <= frame:
            shares[buys[taken][1]] += 1
            taken += 1
        left.append(pricing.prices_for(shares["left"], shares["right"])["left"])
    points = [[frame, price] for frame, price in enumerate(left) if frame == 0 or price != left[frame - 1]]
    if len(points) > config.PRICE_POINTS:
        points = [[frame, left[frame]] for frame in (round(i * close / (config.PRICE_POINTS - 1)) for i in range(config.PRICE_POINTS))]
    return points


def public_record(record):
    """A card's record as anyone may see it: without the player's id, which works like a
    password, the trajectory, which only a replay of the ball would need, or whether the player
    has deleted it from their page."""
    return {key: value for key, value in record.items() if key not in ("player", "trajectory", "cleared")}


def card_message(record, fresh):
    """The "card" message for a player's own pages: their card, or None for no card. fresh says
    the round has only just become their best, which the page shows until the next round has
    been played."""
    return streams.sse("card", {"card": record and public_record(record), "fresh": fresh})


def new_best_rounds():
    """After a round is settled: make a card for every player whose round made more than
    CARD_MIN_PROFIT and more than their best round so far, and send it to their open pages.

    It works from game["buys"], the buys the server took itself, at the frame and price the
    server charged. The cost is everything a player paid this round, on both sides, and the
    payout is 1 for each of their shares on the side the ball landed on, so buys on both sides
    are netted into one profit. A losing round can never make a card, as its profit is below 0.
    """
    trades = {}  # {player_id: [[frame, side, price, shares], ...]}
    for frame, seen_frame, player_id, side, price, points in state.game["buys"]:
        trades.setdefault(player_id, []).append([frame, side, price, 1])
    settled_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    line = None  # worked out only if someone gets a card
    for player_id, theirs in trades.items():
        cost = round(sum(price * shares for frame, side, price, shares in theirs), 2)
        payout = sum(shares for frame, side, price, shares in theirs if side == state.game["bucket"])
        profit = round(payout - cost, 2)
        best = state.best_rounds.get(player_id)
        if profit <= config.CARD_MIN_PROFIT or (best and profit <= best["profit"]):
            continue
        line = line or price_line()
        record = {
            "share_id": secrets.token_urlsafe(6),
            "player": player_id,
            "username": state.players[player_id]["name"],
            "round_started_at": datetime.datetime.fromtimestamp(state.game["started_at"], datetime.timezone.utc).isoformat(),
            "settled_at": settled_at,
            "outcome": state.game["bucket"],
            "frames": len(state.game["path"]),
            "close_frame": state.game["close_frame"],
            "trades": theirs,
            "prices": line,
            "cost": cost,
            "payout": payout,
            "profit": profit,
            # The ball's whole path follows from these, so a replay can play it again (see simulate_drop()).
            "trajectory": {"start_x": state.game["start_x"], "gravity": state.game["gravity"], "bounce": state.game["bounce"],
                           "nudge": state.game["nudge"], "nudge_seed": state.game["nudge_seed"], "commit_id": state.game["commit"]},
            "cleared": False,  # True once the player deletes it from their page (see clear_card())
        }
        state.best_rounds[player_id] = record
        state.card_holders.add(player_id)
        state.unsaved_cards.append(record)
        del state.unsaved_cards[:-config.UNSAVED_CARDS_KEPT]
        streams.send_to_player(player_id, card_message(record, fresh=True))


async def load_card_holders():
    """Read the id of every player with a card they haven't deleted, once, when the server
    starts, so a new visitor's page can start without asking the database whether they have one
    (see get_best_round()). If the database can't be reached, we keep trying every 30 seconds."""
    while True:
        try:
            rows = await db.read_all("player", table="/best_rounds", order="share_id", where={"cleared": "eq.false"})
        except httpx.HTTPError as error:
            print("Could not load who has a best-round card, trying again in 30 seconds:", repr(error))
            await asyncio.sleep(30)
            continue
        state.card_holders.update(row["player"] for row in rows)
        state.game["card_holders_loaded"] = True
        return


async def get_best_round(player_id):
    """Make sure we know a player's best round, if they have one, asking the database the first
    time we see them since the server started. Their best is their row with the most profit, the
    earliest of any with the same, as a later round replaces the best only if it made more,
    leaving out any they've deleted from their page (see clear_card()).

    As in get_player(), once load_card_holders() has read who has a card, anyone else is known
    to have none without asking. If the database can't be reached, we ask again the next time
    the player connects; meanwhile a new best they make is still kept and shown.
    """
    if player_id in state.best_loaded:
        return
    if state.game["card_holders_loaded"] and player_id not in state.card_holders:
        state.best_loaded.add(player_id)
        return
    try:
        response = await db.client.get("/best_rounds", params={
            "player": "eq." + player_id,
            "cleared": "eq.false",
            "select": CARD_COLUMNS,
            "order": "profit.desc,settled_at.asc",
            "limit": 1,
        })
        response.raise_for_status()
    except httpx.HTTPError as error:
        print("Could not load a best round:", repr(error))
        return
    rows = response.json()
    if player_id in state.best_loaded:
        return  # another request for the same player finished first
    state.best_loaded.add(player_id)
    # A round played while we waited may already have made a better card.
    if rows and (player_id not in state.best_rounds or rows[0]["profit"] > state.best_rounds[player_id]["profit"]):
        state.best_rounds[player_id] = rows[0]


async def save_cards():
    """Write every card in unsaved_cards to the best_rounds table. The database skips a card
    it already has, so trying again after a failure that was really a success is harmless."""
    async with state.saving_cards:
        cards = list(state.unsaved_cards)  # more may be made while we wait for the database; they are saved next time
        response = await db.client.post(
            "/best_rounds",
            params={"on_conflict": "share_id"},
            json=cards,
            headers={"Prefer": "resolution=ignore-duplicates"},
        )
        response.raise_for_status()
        saved = {card["share_id"] for card in cards}
        state.unsaved_cards[:] = [card for card in state.unsaved_cards if card["share_id"] not in saved]


async def save_cards_forever():
    """Save each new card a moment after it's made, for as long as the server runs. If the
    database can't be reached, the cards wait in unsaved_cards and we try every 30 seconds.
    A card that isn't saved yet still works: share pages look in unsaved_cards first."""
    while True:
        await asyncio.sleep(1)
        if not state.unsaved_cards:
            continue
        try:
            await save_cards()
        except httpx.HTTPError as error:
            print("Could not save best-round cards, trying again in 30 seconds:", repr(error))
            await asyncio.sleep(30)


def remember(cache, key, value, kept):
    """Keep value in cache, a dict used oldest first, dropping the oldest beyond kept."""
    cache.pop(key, None)
    cache[key] = value
    while len(cache) > kept:
        del cache[next(iter(cache))]


async def find_card(share_id):
    """The card with this share id, or None if there isn't one. Looks in memory first, at the
    cards not saved yet and the ones share pages asked for lately, then asks the database.
    Raises httpx.HTTPError if it has to ask and the database can't be reached."""
    if not SHARE_ID.fullmatch(share_id):
        return None
    if share_id in state.shared_records:
        return state.shared_records[share_id]
    for card in state.unsaved_cards:
        if card["share_id"] == share_id:
            return card
    response = await db.client.get("/best_rounds", params={"share_id": "eq." + share_id, "select": CARD_COLUMNS})
    response.raise_for_status()
    rows = response.json()
    if not rows:
        return None
    remember(state.shared_records, share_id, rows[0], config.SHARED_KEPT)
    return rows[0]


async def rename_cards(player_id, name):
    """After the owner has renamed a player, put the new name on all their best-round cards too,
    old ones included, so a bad name doesn't live on in share pages and link previews. A card
    made while the player was a guest has no name, and keeps none. Returns "renamed", or what
    went wrong; the player's rename itself has worked either way."""
    for card in [state.best_rounds.get(player_id), *state.unsaved_cards, *state.shared_records.values()]:
        if card and card["player"] == player_id and card["username"] is not None:
            card["username"] = name
    state.shared_images.clear()  # pictures of the old name; they are drawn again when asked for
    try:
        response = await db.client.patch(
            "/best_rounds",
            params={"player": "eq." + player_id, "username": "not.is.null"},
            json={"username": name},
        )
        response.raise_for_status()
    except httpx.HTTPError as error:
        return "the cards in the database still have the old name; rename them again to try again. " + db.database_problem(error)
    if player_id in state.best_rounds:
        streams.send_to_player(player_id, card_message(state.best_rounds[player_id], fresh=False))
    return "renamed"
