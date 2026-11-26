# /// script
# dependencies = ["httpx==0.28.1", "starlette==1.10.0", "uvicorn[standard]==0.54.0"]  # keep in sync with requirements.txt
# ///
import argparse
import asyncio
import bisect
import contextlib
import datetime
import hmac
import json
import math
import os
import random
import struct
import time
import uuid
import zlib

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from starlette.routing import Route

import names  # names.py, in this folder: the rules for usernames

# Board geometry, in pixels. (0, 0) is the top-left corner.
WIDTH, HEIGHT = 400, 560
BALL_R, PIN_R = 5, 3
ROWS = 12  # must be even, so the last row has a center pin that caps the divider
FIRST_ROW_Y, ROW_GAP = 120, 36
LAST_ROW_Y = FIRST_ROW_Y + (ROWS - 1) * ROW_GAP
# The side walls lean inwards, so the board is a trapezoid: WIDTH across the top, and
# WALL_SLANT narrower on each side at the floor. The pins squeeze together as the walls close in.
WALL_SLANT = 60
WALL_LENGTH = math.sqrt(WALL_SLANT**2 + HEIGHT**2)

# Physics. Gravity and bounce can be set on the command line, e.g. --bounce 0.3 (see the bottom of the file).
GRAVITY = 400.0  # pixels / second^2
BOUNCE = 0.85  # fraction of speed kept after hitting something, 0 to 1
DT = 1 / 240  # seconds per physics step
STEPS_PER_FRAME = 4  # the page is sent one ball position per frame
FRAME_SECONDS = STEPS_PER_FRAME * DT  # 1/60 second
BROADCASTS_PER_SECOND = 10  # the most often every page is sent the state of the game; see broadcast()
FRAMES_PER_BROADCAST = 6  # 60 frames a second / 10 broadcasts a second
FINAL_STRETCH_FRAMES = 90  # for the last 1.5 seconds before betting closes, and until the ball lands, send every broadcast
EARLY_AHEAD_FRAMES = 48  # before that, send positions this far ahead (0.8 seconds), so a message is needed only every half second
POSITION_DECIMALS = 1  # ball positions are sent rounded to tenths of a pixel; 0 (whole pixels) is ~15% smaller but may look steppy
BUFFER_FRAMES = 12  # ball positions are sent this many frames early, so the page can play back smoothly
# The counterparty buys faster as the ball falls: every 60 frames (1 second) with the ball at
# the top, speeding up to every 6 frames (0.1 seconds) by the bottom of the betting zone.
COUNTERPARTY_SLOWEST_FRAMES = 60
COUNTERPARTY_FASTEST_FRAMES = 6
COUNTDOWN_SECONDS = 5  # the wait between one ball landing and the next one dropping
LEADERBOARD_SIZE = 10
NEIGHBORS = 2  # a player ranked below the top 10 also sees this many players either side of them
PAGE_SIZE = 1000  # the most rows Supabase returns from one request, unless its settings say otherwise
SEED_SHARES = 3  # pretend shares on each side, so the price starts at 0.50. Fewer makes early buys swing it harder

# The round log: every round a player bought in is saved to the database, for analysis later.
# See "The round log" below, and analysis.py, which reads it back.
LOG_FORMAT = 1  # saved with each round; change it whenever pack_path() or pack_buys() change what they write
COMMIT = os.environ.get("RENDER_GIT_COMMIT", "local")  # which version of this code played the round; Render sets this
UNSAVED_ROUNDS_KEPT = 20  # if the database can't be reached, this many rounds wait in memory, and older ones are dropped
KEYS_PER_REQUEST = 500  # the most players whose keys (see player_keys) are asked for in one request to the database
NOT_SEEN = 0xFFFF  # stands in for the frame a page was showing when it didn't say


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

# Named players are kept in a Supabase database table, so their points survive a restart.
# We talk to it over its web API. The secret key gives full access, so it stays on the
# server (in an environment variable) and the page never talks to Supabase itself.
db = httpx.AsyncClient(
    base_url=os.environ["SUPABASE_URL"] + "/rest/v1",
    headers={"apikey": os.environ["SUPABASE_KEY"], "Authorization": "Bearer " + os.environ["SUPABASE_KEY"]},
    timeout=10,
)

# The owner's key for the moderation routes under /admin (see the admin functions below).
# Optional: without it those routes don't exist, as far as anyone outside can tell.
ADMIN_KEY = os.environ.get("ADMIN_KEY", "").strip()

# Everything below lives in memory, so it resets when the server restarts.
# The whole server runs on one thread (see the bottom of the file), so none of it needs locks.

# The one shared game that every player watches and bets on. Rounds repeat forever:
# a countdown, then a ball drops. "path" is the ball's position at every frame (None
# before the first drop) and "bucket" is where it lands. Pages are sent the path a few
# frames at a time, and the bucket when the ball lands.
game = {
    "dropping": False,
    "drops_at": time.time() + COUNTDOWN_SECONDS,  # when the countdown ends
    "started_at": 0,  # when the ball dropped
    "start_x": None,  # where along the top the ball dropped from. The whole path follows from this one number
    "path": None,
    "bucket": None,
    "close_frame": 0,  # betting closes when the ball reaches this frame
    "counterparty_buys": [],  # the counterparty's buys this drop, as [(frame, side)], from counterparty_buys()
    "sent_frames": 0,  # how many frames of the path have been sent to the pages so far
    "shares": {"left": 0, "right": 0},  # all the shares players have bought this round, added up
    "buys": [],  # every player buy this round, in order, for the round log. See buy()
    "last_bucket": None,  # where the previous ball landed
    "ranking": [],  # every named player, most points first, as [{"name": ..., "points": ...}], made from named_points
    "positions": {},  # each name's index in "ranking", so a player can be found quickly
    "minus_points": [],  # minus each player's points, in "ranking" order, so it ascends for bisect
    "ranking_loaded": False,  # whether load_ranking() has read the whole players table yet
    "update_at": None,  # when the owner has announced a system update for (see admin_notice()), or None
}

# One entry per player who has opened the page since the server started, keyed by the
# random id their browser sends:
# {"name": None, "points": 10, "shares": {"left": 0, "right": 0}, "spent": 0, "last_won": 0,
#  "loaded": True, "name_locked": False}
# "name" is None until the player claims one. "shares" and "spent" are what the player has
# bought, and paid for it, in the current round. "loaded" is explained in get_player().
# "name_locked" is True once the owner has replaced the player's name (see admin_rename()),
# and stops them changing it themselves.
players = {}

# Every named player's points, {name: points}, for the leaderboard. Loaded from the database
# when the server starts, then updated here whenever a named player's points change.
named_points = {}

# The id of every player who has a row in the database: read by load_ranking() when the server
# starts, and added to whenever a player claims a name. Once the whole table has been read,
# an id that isn't here can't have a row, so get_player() doesn't need to ask the database.
saved_ids = set()

# One entry per open page: a queue of messages waiting to be sent to it, and whose page it is.
streams = {}  # {queue: player_id}

# The round log gives every player who has ever bought a share a small whole number, their
# key, and uses it in place of their long id, so each logged buy takes a few bytes.
# {player_id: key}, for the players we have asked the database about since the server started.
player_keys = {}

# Rounds waiting to be saved to the round log, oldest first, each as made by round_log().
unsaved_rounds = []

# Held while writing to the players table, so there is only ever one write on its way to the
# database, made from the names as they are at that moment. Without it, a round's points sent
# with a player's old name could arrive after their change of name, and undo it.
saving_players = asyncio.Lock()


def simulate_drop(start_x, gravity, bounce):
    """Drop one ball from start_x along the top. Returns its state at every frame, as
    [(x, y, vx, vy)] (position and velocity), and the bucket it lands in.

    The same start_x, gravity and bounce always give exactly the same drop, on any computer.
    The sums use only + - * / and square roots, which every computer rounds the same way
    (math.hypot, for one, is not promised to). That matters because the path is chaotic: the
    tiniest difference early on lands the ball in the other bucket. It is what lets the round
    log save one number for a drop and analysis.py replay it later, velocities included.
    """
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

    bucket = "left" if x < WIDTH / 2 else "right"
    return states, bucket


def positions(states):
    """The ball's position at every frame, rounded to tenths of a pixel: the path the game itself uses."""
    return [(round(x, 1), round(y, 1)) for x, y, vx, vy in states]


def parse_id(text):
    """Player ids are UUIDs, the random ids browsers make. Returns None for anything else,
    including something that isn't text at all, or is missing."""
    if not isinstance(text, str):
        return None
    try:
        return str(uuid.UUID(text))
    except ValueError:
        return None


async def read_json(request):
    """The JSON object a request sent, or an empty dict if it sent anything else (nothing,
    broken JSON, a list...). So a bad request gets the handler's own 400 and not a crash."""
    try:
        data = await request.json()
    except ValueError:  # broken JSON, or bytes that aren't text
        return {}
    return data if isinstance(data, dict) else {}


async def load_player(player_id):
    """Fetch a named player's row from the database, or None if this id never claimed a name.

    It asks for every column ("*") rather than naming them, so that it still works before the
    owner has added the name_locked column (see schema.sql): the row then just has no
    "name_locked", which counts as unlocked. Naming a missing column would make the database
    refuse every lookup, and no returning player could load their name.
    """
    response = await db.get("/players", params={"id": "eq." + player_id, "select": "*"})
    response.raise_for_status()
    rows = response.json()
    return rows[0] if rows else None


def use_saved_row(player_id, row):
    """Give a player in memory the name, points and lock from their database row."""
    player = players[player_id]
    player["name"], player["points"] = row["name"], row["points"]
    player["name_locked"] = bool(row.get("name_locked", False))  # missing until schema.sql adds the column
    player["loaded"] = True
    saved_ids.add(player_id)


async def save_players(rows):
    """Write rows of {"id", "name", "points"} to the database, replacing any with the same id."""
    if rows:
        response = await db.post("/players", json=rows, headers={"Prefer": "resolution=merge-duplicates"})
        response.raise_for_status()


async def read_all_players(columns):
    """Every row of the players table, with the columns asked for, e.g. "id,name,points".
    The database hands rows over at most PAGE_SIZE at a time, so we ask page by page."""
    rows = []
    while True:
        params = {"select": columns, "limit": PAGE_SIZE, "offset": len(rows), "order": "id"}
        response = await db.get("/players", params=params)
        response.raise_for_status()
        page = response.json()
        rows += page
        if len(page) < PAGE_SIZE:
            return rows


async def load_ranking():
    """Read every named player's id and points from the database, once, when the server starts.

    After that the server keeps the ranking up to date itself. It is the only thing that
    changes anyone's points, so it already knows every change without asking the database.
    If the database can't be reached, we keep trying every 30 seconds. Players who play in
    the meantime are ranked with their newer points, which the database rows don't overwrite.

    The ids go into saved_ids, so that from then on a new visitor's page can start without
    asking the database about them (see get_player()). They are added to what is already
    there, never put in its place: a player who claims a name while the pages are being read
    is added by set_name(), and their row may be too new to be on a page we have read.
    """
    while True:
        try:
            rows = await read_all_players("id,name,points")
        except httpx.HTTPError as error:
            print("Could not load the leaderboard, trying again in 30 seconds:", repr(error))
            await asyncio.sleep(30)
            continue
        for row in rows:
            player = players.get(row["id"])
            if player and player["loaded"]:
                # This player has loaded since the server started, so what we hold in memory is
                # newer than the row: they may have renamed themselves while we were reading,
                # and the row's old name mustn't come back onto the leaderboard.
                if player["name"]:
                    named_points.setdefault(player["name"], player["points"])
            else:
                named_points.setdefault(row["name"], row["points"])
        saved_ids.update(row["id"] for row in rows)
        game["ranking_loaded"] = True
        publish_leaderboard()
        return


def publish_leaderboard():
    """Rank the named players, most points first, and send each page its leaderboard.
    Players with the same points are ranked by name, so the order never shuffles."""
    ranking = [{"name": name, "points": points} for name, points in named_points.items()]
    ranking.sort(key=lambda row: (-row["points"], row["name"]))
    game["ranking"] = ranking
    game["positions"] = {row["name"]: i for i, row in enumerate(ranking)}
    game["minus_points"] = [-row["points"] for row in ranking]  # ascending, for finding where a guest fits
    for queue, player_id in streams.items():
        send(queue, leaderboard_message(players[player_id]))


def leaderboard_message(player):
    """The leaderboard as one player sees it: the top LEADERBOARD_SIZE players and, if this
    player is ranked below them, the NEIGHBORS players either side of them as well.
    Every row carries its rank, counting from 1, and the player's own row is marked "me".

    Everyone is ranked, named or not. A guest (or a named player not in the ranking yet) is
    slotted in where their points would put them, after anyone with the same points, and
    the players below them move down one, just for them. Other guests are never shown.
    """
    ranking = game["ranking"]
    my_position = game["positions"].get(player["name"])
    slotted_in = my_position is None
    if slotted_in:
        my_position = bisect.bisect_right(game["minus_points"], -player["points"])
        mine = {"name": player["name"], "points": player["points"]}
    total = len(ranking) + slotted_in

    def row(i):
        """The row at index i of the ranking as this player sees it."""
        if slotted_in and i == my_position:
            entry = mine
        else:
            entry = ranking[i - 1 if slotted_in and i > my_position else i]
        return {"rank": i + 1, **entry, **({"me": True} if i == my_position else {})}

    def rows(start, end):
        return [row(i) for i in range(start, min(end, total))]

    around_me = []
    if my_position >= LEADERBOARD_SIZE:
        around_me = rows(max(LEADERBOARD_SIZE, my_position - NEIGHBORS), my_position + NEIGHBORS + 1)
    return sse("leaderboard", {"top": rows(0, LEADERBOARD_SIZE), "around_me": around_me})


async def get_player(player_id):
    """Look up a player, creating them with 10 points the first time we see their id.

    If they claimed a name before, the database has their name and points, and we load those.
    If the database can't be reached they can still play, without a name for now, and we
    look again the next time they connect. "loaded" records whether that lookup has worked.

    Most new ids are new visitors, who have never claimed a name. Once load_ranking() has read
    the whole players table, saved_ids holds every id that has a row, so for any other id we
    already know the answer is "no row" and don't ask. Until then (just after a restart, or
    while the database is down) we ask about every player, as there is no other way to know.
    A player who does have a row is still looked up here, the first time they connect: the
    database is always at least as up to date as what load_ranking() read, as the server
    writes every change there, and the lookup also brings their name_locked.
    """
    if player_id not in players:
        players[player_id] = {
            "name": None,
            "points": 10,
            "shares": {"left": 0, "right": 0},
            "spent": 0,
            "last_won": 0,
            "loaded": False,
            "name_locked": False,
        }
    player = players[player_id]
    if not player["loaded"] and game["ranking_loaded"] and player_id not in saved_ids:
        player["loaded"] = True
    if not player["loaded"]:
        try:
            saved = await load_player(player_id)
        except httpx.HTTPError as error:
            print("Could not load a player:", repr(error))
            return player
        # Another request for the same player may have finished first, or the owner may have
        # renamed them meanwhile (see admin_rename()). Either way the newer answer is already
        # in memory, and this one mustn't overwrite it.
        if not player["loaded"]:
            if saved:
                use_saved_row(player_id, saved)
            else:
                player["loaded"] = True
    return player


def current_frame():
    """How many frames have passed since the ball dropped."""
    return int((time.time() - game["started_at"]) / FRAME_SECONDS)


def betting_open(frame=None):
    """Shares can be bought from the moment the ball drops until it reaches close_frame.
    Asks about the frame given, or about right now if none is."""
    if frame is None:
        frame = current_frame()
    return game["dropping"] and frame < game["close_frame"]


def counterparty_buys(path, close_frame):
    """The frames the counterparty buys on this drop, and the side it buys each time.

    It buys one share at the drop, then keeps buying until betting closes, each time on
    whichever half of the board the ball is in at that moment. The lower the ball, the
    sooner the next buy: the wait shrinks in a straight line from COUNTERPARTY_SLOWEST_FRAMES
    with the ball at the top to COUNTERPARTY_FASTEST_FRAMES with the ball as low as it is
    when betting closes. So it piles in as the ball nears the bottom, when where the ball
    is says the most about where it will land. It never buys at close_frame itself.
    """
    bottom = max(1, path[close_frame][1])  # how far down the ball is when betting closes
    buys = []
    frame = 0
    while frame < close_frame:
        x, y = path[frame]
        buys.append((frame, "left" if x < WIDTH / 2 else "right"))
        depth = min(1, max(0, y / bottom))  # 0 at the top, 1 at the bottom of the betting zone
        frame += round(COUNTERPARTY_SLOWEST_FRAMES - (COUNTERPARTY_SLOWEST_FRAMES - COUNTERPARTY_FASTEST_FRAMES) * depth)
    return buys


def shares_by_frame(buys, frame):
    """How many shares have been bought on each side up to and including a frame, given the
    buys so far as [(frame, side)] in frame order, the form counterparty_buys() returns."""
    shares = {"left": 0, "right": 0}
    for buy_frame, side in buys:
        if buy_frame > frame:
            break
        shares[side] += 1
    return shares


def counterparty_shares(frame=None):
    """How many shares the counterparty has bought on each side so far this round: up to the
    frame given, or up to right now if none is.

    It never collects on them. They only move the price, towards the side the ball has been
    spending its time on, and most of all towards where it has been lately.
    """
    if not game["dropping"]:
        return {"left": 0, "right": 0}
    return shares_by_frame(game["counterparty_buys"], current_frame() if frame is None else frame)


def prices_for(left, right):
    """What one share on each side costs once this many shares have been bought on each side.
    A share pays 1 point if its side wins.

    The price of a side is its fraction of all the shares bought this round, by players and
    by the counterparty. So a side that everyone is buying gets expensive, and the two
    prices always add up to 1.
    """
    left, right = left + SEED_SHARES, right + SEED_SHARES
    left_price = round(left / (left + right), 2)
    left_price = min(0.99, max(0.01, left_price))  # never free, and never a sure loss
    return {"left": left_price, "right": round(1 - left_price, 2)}


def prices(frame=None):
    """What one share on each side costs at the frame given, or right now if none is."""
    theirs = counterparty_shares(frame)
    return prices_for(game["shares"]["left"] + theirs["left"], game["shares"]["right"] + theirs["right"])


def sse(event, data):
    """Format one server-sent event, the browser's built-in format for a stream of messages."""
    # No spaces after commas and colons: the same JSON, a little smaller to send to every page.
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def send(queue, message):
    """Queue a message for one page. A page that has fallen far behind misses it instead."""
    if not queue.full():
        queue.put_nowait(message)


def send_to_player(player_id, message):
    """Queue a message for every page this player has open (they may have more than one tab)."""
    for queue, owner in streams.items():
        if owner == player_id:
            send(queue, message)


def settle():
    """Pay 1 point for every share on the bucket the ball landed in, and tell each page its result.

    Players paid for their shares when they bought them. The counterparty is on the other
    side of every share: it kept what players paid, and it pays out the winners.
    Returns the database rows that need saving: the named players who played this round.
    """
    bucket = game["bucket"]
    rows = []
    for player_id, player in players.items():
        payout = player["shares"][bucket]
        if player["name"] and player["spent"] > 0:
            rows.append({"id": player_id, "name": player["name"], "points": round(player["points"] + payout, 2)})
        player["last_won"] = round(payout - player["spent"], 2)
        player["points"] = round(player["points"] + payout, 2)
        player["shares"] = {"left": 0, "right": 0}
        player["spent"] = 0

    game["shares"] = {"left": 0, "right": 0}
    game["last_bucket"] = bucket
    for row in rows:
        named_points[row["name"]] = row["points"]
    for queue, player_id in streams.items():
        send(queue, sse("me", players[player_id]))
    return rows


# The round log. Every round that at least one player bought in becomes one row of the
# "rounds" table: where the ball dropped from, its path, where it landed, and every buy.
# The path and the buys are packed into a few bytes each, because the buys are nearly all of
# what gets stored: a thousand players buying three shares a round is about 8 million buys in
# ten hours. analysis.py reads the log back and unpacks it.


def pack_path(path):
    """A path as bytes. Every position is in tenths of a pixel, which makes it a whole number,
    and is stored as its change from the frame before (the first as it is): x then y, each a
    2-byte signed integer, low byte first. The changes are small and repetitive, so zlib then
    squeezes a typical path to under a kilobyte."""
    tenths = [(round(x * 10), round(y * 10)) for x, y in path]
    numbers = list(tenths[0])
    for before, after in zip(tenths, tenths[1:]):
        numbers += [after[0] - before[0], after[1] - before[1]]
    return zlib.compress(struct.pack(f"<{len(numbers)}h", *numbers), 9)


def unpack_path(data):
    """The path that pack_path() packed, as [(x, y)]."""
    data = zlib.decompress(data)
    numbers = struct.unpack(f"<{len(data) // 2}h", data)
    path = []
    x = y = 0
    for i in range(0, len(numbers), 2):
        x, y = x + numbers[i], y + numbers[i + 1]
        path.append((x / 10, y / 10))
    return path


# How pack_buys() stores each fact about a buy: one column per fact, each holding one number
# per buy, in the order the buys were made. The letter is the size of the number, as the
# struct module spells it. All the frames, then all the lags, and so on: zlib squeezes columns
# of alike numbers much smaller than it would the same numbers buy by buy.
BUY_COLUMNS = (
    ("frames", "H"),  # 2 bytes. The server's frame at the buy, minus the frame of the buy before (the first as it is)
    ("lags", "h"),  # 2 bytes, signed. How many frames behind that the page said it was showing; NO_LAG if it didn't say
    ("keys", "I"),  # 4 bytes. The player's key, from the player_keys table
    ("sides", "B"),  # 1 byte. 0 for left, 1 for right
    ("prices", "B"),  # 1 byte. The price paid, in hundredths of a point
    ("points", "I"),  # 4 bytes. The player's points just before the buy, in hundredths
)
BUY_FORMAT = "<" + "".join("{n}" + letter for name, letter in BUY_COLUMNS)  # "<" means low byte first, no padding
BUY_BYTES = struct.calcsize(BUY_FORMAT.format(n=1))  # 14 bytes a buy, before zlib
NO_LAG = -0x8000


def pack_buys(buys):
    """A round's buys as bytes, given as [(frame, seen_frame, key, side, price, points)] in the
    order they were made. BUY_COLUMNS says how. Numbers too big for their column are capped."""
    columns = {name: [] for name, letter in BUY_COLUMNS}
    last_frame = 0
    for frame, seen_frame, key, side, price, points in buys:
        columns["frames"].append(min(0xFFFF, max(0, frame - last_frame)))
        last_frame = max(last_frame, frame)
        columns["lags"].append(NO_LAG if seen_frame == NOT_SEEN else min(0x7FFF, max(-0x7FFF, frame - seen_frame)))
        columns["keys"].append(key)
        columns["sides"].append(0 if side == "left" else 1)
        columns["prices"].append(round(price * 100))
        columns["points"].append(min(0xFFFFFFFF, max(0, round(points * 100))))
    numbers = [number for name, letter in BUY_COLUMNS for number in columns[name]]
    return zlib.compress(struct.pack(BUY_FORMAT.format(n=len(buys)), *numbers), 9)


def unpack_buys(data):
    """The buys that pack_buys() packed, in the same form. seen_frame is None where the page didn't say."""
    data = zlib.decompress(data)
    n = len(data) // BUY_BYTES
    numbers = struct.unpack(BUY_FORMAT.format(n=n), data)
    frames, lags, keys, sides, cents, points = (numbers[i * n:(i + 1) * n] for i in range(len(BUY_COLUMNS)))
    buys = []
    frame = 0
    for i in range(n):
        frame += frames[i]
        seen_frame = None if lags[i] == NO_LAG else frame - lags[i]
        buys.append((frame, seen_frame, keys[i], "right" if sides[i] else "left", cents[i] / 100, points[i] / 100))
    return buys


def to_bytea(data):
    """Bytes in the text form the database takes for a bytea (raw bytes) column: \\x and then hexadecimal."""
    return "\\x" + data.hex()


def round_log():
    """The round that just finished, as a row for the "rounds" table, or None if no player
    bought anything in it. Rounds nobody played aren't logged: they are most rounds on a quiet
    day, and analysis.py can make as many drops of its own as it likes.

    "buys" is still the list buy() made, with each player's long id. save_rounds() packs it,
    once it knows every buyer's key.
    """
    if not game["buys"]:
        return None
    return {
        "started_at": datetime.datetime.fromtimestamp(game["started_at"], datetime.timezone.utc).isoformat(),
        "commit_id": COMMIT,
        "gravity": GRAVITY,
        "bounce": BOUNCE,
        "start_x": game["start_x"],
        "frames": len(game["path"]),
        "close_frame": game["close_frame"],
        "bucket": game["bucket"],
        "format": LOG_FORMAT,
        "path": to_bytea(pack_path(game["path"])),
        "buys": game["buys"],
    }


async def save_rounds():
    """Write every round in unsaved_rounds to the database, in two steps.

    First, ask the database for the key of every buyer we don't know the key of yet. It makes
    up a new key for anyone it hasn't seen before, and hands back the old one for anyone it has.
    Then write the rounds, each with its buys packed. No two rounds start at the same moment,
    and the database skips a round it already has, so trying again after a failure that was
    really a success doesn't save a round twice.
    """
    logs = list(unsaved_rounds)  # more may finish while we wait for the database; they are saved next time
    new_ids = sorted({buy[2] for log in logs for buy in log["buys"]} - player_keys.keys())
    for start in range(0, len(new_ids), KEYS_PER_REQUEST):
        response = await db.post(
            "/player_keys",
            params={"on_conflict": "id"},
            json=[{"id": player_id} for player_id in new_ids[start:start + KEYS_PER_REQUEST]],
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        )
        response.raise_for_status()
        for row in response.json():
            player_keys[row["id"]] = row["key"]

    rows = []
    for log in logs:
        buys = [(frame, seen, player_keys[player_id], *rest) for frame, seen, player_id, *rest in log["buys"]]
        rows.append({**log, "n_buys": len(buys), "buys": to_bytea(pack_buys(buys))})
    response = await db.post(
        "/rounds",
        params={"on_conflict": "started_at"},
        json=rows,
        headers={"Prefer": "resolution=ignore-duplicates"},
    )
    response.raise_for_status()
    saved = {log["started_at"] for log in logs}
    unsaved_rounds[:] = [log for log in unsaved_rounds if log["started_at"] not in saved]


async def save_round_logs():
    """Save each logged round soon after it finishes, for as long as the server runs.

    If the database can't be reached, the rounds wait in unsaved_rounds (the newest
    UNSAVED_ROUNDS_KEPT of them) and we try again every 30 seconds.
    """
    while True:
        await asyncio.sleep(1)
        if not unsaved_rounds:
            continue
        try:
            await save_rounds()
        except (httpx.HTTPError, KeyError) as error:  # KeyError: the database didn't hand back a key we asked for
            print("Could not save the round log, trying again in 30 seconds:", repr(error))
            await asyncio.sleep(30)


async def run_rounds():
    """The round loop: wait for the countdown, drop a ball, wait for it to land, pay out. Forever."""
    while True:
        await asyncio.sleep(max(0, game["drops_at"] - time.time()))
        # Drop from somewhere in the middle two-thirds, so the ball never just slides down a slanted wall.
        start_x = random.uniform(WIDTH / 6, WIDTH * 5 / 6)
        states, bucket = simulate_drop(start_x, GRAVITY, BOUNCE)
        path = positions(states)
        last_row_frame = next(i for i, (x, y) in enumerate(path) if y > LAST_ROW_Y)
        # Pages see BUFFER_FRAMES ahead, so betting closes that long before the ball
        # passes the last row. Nobody can bet after seeing which side of the divider it took.
        close_frame = max(0, last_row_frame - BUFFER_FRAMES)
        game.update(
            dropping=True,
            started_at=time.time(),
            start_x=start_x,
            path=path,
            bucket=bucket,
            close_frame=close_frame,
            counterparty_buys=counterparty_buys(path, close_frame),
            sent_frames=0,
            buys=[],
        )
        await asyncio.sleep(len(path) * FRAME_SECONDS)
        rows = settle()
        log = round_log()
        if log:
            # save_round_logs() writes it to the database in its own time, so a slow database never holds up the game.
            unsaved_rounds.append(log)
            del unsaved_rounds[:-UNSAVED_ROUNDS_KEPT]
        game["dropping"] = False
        game["drops_at"] = time.time() + COUNTDOWN_SECONDS
        try:
            async with saving_players:
                for row in rows:
                    row["name"] = players[row["id"]]["name"]  # they may have changed it since settle() made the row
                await save_players(rows)
        except httpx.HTTPError as error:
            # The game carries on. These players' points are saved after the next round they play.
            print("Could not save points:", repr(error))
        publish_leaderboard()


def rounded(position):
    """A ball position rounded to POSITION_DECIMALS places, to keep messages short."""
    if POSITION_DECIMALS == 0:
        return [round(position[0]), round(position[1])]  # whole numbers, sent without a ".0"
    return [round(position[0], POSITION_DECIMALS), round(position[1], POSITION_DECIMALS)]


async def broadcast():
    """Send every page the public state of the game: up to 10 times a second, but only as often as needed.

    The message is built once and the same text goes to everyone. While the ball is falling
    it carries the next positions on the path, ahead of time. The page plays them back on its
    own clock, so it always has some in hand and the ball moves smoothly even when the
    network delivers messages unevenly.

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
        await asyncio.sleep(1 / BROADCASTS_PER_SECOND)
        ticks_since_sent += 1
        path = game["path"]
        dropping = game["dropping"]
        if dropping:
            frame = current_frame()
            final_stretch = frame >= game["close_frame"] - FINAL_STRETCH_FRAMES
            ahead = BUFFER_FRAMES if final_stretch else EARLY_AHEAD_FRAMES
            # Early on, send only when the pages would otherwise have less than BUFFER_FRAMES in hand by the next broadcast.
            running_low = game["sent_frames"] <= frame + BUFFER_FRAMES + FRAMES_PER_BROADCAST
            due = final_stretch or running_low
        else:
            due = ticks_since_sent >= BROADCASTS_PER_SECOND
        if not due and dropping == was_dropping:
            continue
        ticks_since_sent = 0
        was_dropping = dropping

        message = {
            "dropping": game["dropping"],
            "seconds_to_drop": max(0, game["drops_at"] - time.time()),
            "prices": prices(),
            "betting_open": betting_open(),
            "online": len(streams),
            "last_bucket": game["last_bucket"],
        }
        if game["update_at"] is not None:
            # Seconds until the announced update, which the page counts down on its own clock.
            message["update_in"] = max(0, round(game["update_at"] - time.time()))
        if dropping:
            # The positions the pages don't have yet, and the frame number of the first of them.
            # Never past close_frame + BUFFER_FRAMES (the last row) while betting is open, however early it is.
            end = min(frame + ahead + 1, len(path))
            if betting_open():
                end = min(end, game["close_frame"] + BUFFER_FRAMES)
            start = min(game["sent_frames"], end)
            message["frame"] = frame
            message["first_position"] = start
            message["positions"] = [rounded(position) for position in path[start:end]]
            game["sent_frames"] = max(game["sent_frames"], end)

        text = sse("game", message)
        for queue in streams:
            send(queue, text)


async def index(request):
    return FileResponse("index.html")


async def board(request):
    return JSONResponse({
        "width": WIDTH,
        "height": HEIGHT,
        "ball_r": BALL_R,
        "pin_r": PIN_R,
        "last_row_y": LAST_ROW_Y,
        "pins": PINS,
        "wall_slant": WALL_SLANT,
        "countdown_seconds": COUNTDOWN_SECONDS,  # the page fades the landing highlight over this long
    })


async def events(request):
    """Stream messages to one player's page for as long as it stays open.

    The page gets a private "me" message (its name, points and shares) when it connects and
    after each round, the public "game" message from broadcast(), and its own "leaderboard"
    message (the top players, plus the players around it) when it connects and whenever the
    leaderboard is refreshed.
    """
    player_id = parse_id(request.query_params.get("player"))
    if player_id is None:
        return JSONResponse({"error": "bad player id."}, status_code=400)
    player = await get_player(player_id)

    async def stream():
        queue = asyncio.Queue(maxsize=100)
        streams[queue] = player_id
        try:
            yield sse("me", player)
            yield leaderboard_message(player)
            while True:
                yield await queue.get()
        finally:
            del streams[queue]  # the page has gone away

    # The headers ask anything between the server and the browser not to hold events back.
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)


async def buy(request):
    """Buy one share on one side, at the current price."""
    data = await read_json(request)
    side = data.get("side")
    if side not in ("left", "right"):
        return JSONResponse({"error": "bad side."}, status_code=400)
    player_id = parse_id(data.get("player"))
    player = players.get(player_id)
    if player is None:
        return JSONResponse({"error": "unknown player."}, status_code=400)
    frame = current_frame()  # read the clock once, so the logged frame is the one the price was worked out at
    if not betting_open(frame):
        return JSONResponse({"error": "trading is closed."}, status_code=400)
    price = prices(frame)[side]
    if price > player["points"]:
        return JSONResponse({"error": "you don't have enough points."}, status_code=400)
    # For the round log. The page says which frame it was showing when the button was pressed,
    # which is a little behind the server's frame. It's the page's word, so only good for analysis.
    seen_frame = data.get("frame")
    if type(seen_frame) is not int or not 0 <= seen_frame < NOT_SEEN:
        seen_frame = NOT_SEEN
    game["buys"].append((frame, seen_frame, player_id, side, price, player["points"]))
    player["points"] = round(player["points"] - price, 2)
    player["spent"] = round(player["spent"] + price, 2)
    player["shares"][side] += 1
    game["shares"][side] += 1
    return JSONResponse(player)


async def set_name(request):
    """Give a player a name, which puts them on the leaderboard, or change the name they have
    to any other that nobody is using. Names are unique, ignoring capitals.

    A name must pass the rules in names.py (length, characters, reserved names and a word
    filter). They are checked only here, when a name is set or changed: names saved before a
    rule existed stay as they are, and the owner deals with any bad ones (see admin_rename()).
    A player whose name the owner has locked can't change it.
    """
    data = await read_json(request)
    player_id = parse_id(data.get("player"))
    if player_id not in players:
        return JSONResponse({"error": "unknown player."}, status_code=400)
    locked = JSONResponse({"error": "your username can't be changed."}, status_code=403)
    if players[player_id]["name_locked"]:
        return locked
    name = data.get("name")
    if not isinstance(name, str):
        return JSONResponse({"error": "bad username."}, status_code=400)
    name = name.strip()
    problem = names.name_problem(name)
    if problem:
        return JSONResponse({"error": problem}, status_code=400)

    # Make sure we know whether this player already has a saved name before writing a new one.
    sorry = JSONResponse({"error": "couldn't save your username. try again in a minute."}, status_code=503)
    player = await get_player(player_id)
    if not player["loaded"]:
        return sorry
    if player["name"] is None:
        # Ask the database once more before a first name is written, as that write replaces any
        # row with this id. get_player() may have known "no row" only from load_ranking()'s read
        # at startup, and during a deploy the previous server keeps serving for a little while:
        # a name claimed there after our read would be overwritten. If there is a row, the
        # player takes it up (name, points and lock), and what they asked for is a change of name.
        try:
            saved = await load_player(player_id)
        except httpx.HTTPError as error:
            print("Could not load a player:", repr(error))
            return sorry
        if saved and player["name"] is None:
            use_saved_row(player_id, saved)
            named_points.setdefault(saved["name"], saved["points"])
            send_to_player(player_id, sse("me", player))
            publish_leaderboard()
    try:
        async with saving_players:
            # Checked again here, as the owner may have locked the name while we waited our turn.
            if player["name_locked"]:
                return locked
            if name == player["name"]:
                return JSONResponse(player)
            old_name = player["name"]
            if old_name:
                # A change of name writes only the name. Their saved points stay as they are.
                response = await db.patch("/players", params={"id": "eq." + player_id}, json={"name": name})
                response.raise_for_status()
            else:
                await save_players([{"id": player_id, "name": name, "points": player["points"]}])
            player["name"] = name
            saved_ids.add(player_id)  # they have a row now, which get_player() must look up next time
            # On the leaderboard, the new name takes over the points shown beside the old one.
            named_points[name] = named_points.pop(old_name, player["points"])
    except httpx.HTTPError as error:
        # The database answers 409 when someone else already has the name.
        if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 409:
            return JSONResponse({"error": "sorry, that username is taken."}, status_code=409)
        print("Could not save a name:", repr(error))
        return sorry
    publish_leaderboard()
    return JSONResponse(player)


async def reset_points(request):
    """Start a player over with 10 points, whatever they had before. Guests too, so someone
    who loses everything before claiming a name isn't stuck.

    Not while they hold shares in the round being played. Otherwise someone could spend all
    their points pushing a price around, reset, and do it again for free.
    """
    data = await read_json(request)
    player_id = parse_id(data.get("player"))
    player = players.get(player_id)
    if player is None:
        return JSONResponse({"error": "unknown player."}, status_code=400)
    if player["spent"] > 0:
        return JSONResponse({"error": "you can reset once this round is over."}, status_code=400)
    player["points"] = 10
    if not player["name"]:
        # A guest has nothing saved in the database, and is on no leaderboard but their own,
        # so only their own pages need a new one.
        send_to_player(player_id, leaderboard_message(player))
        return JSONResponse(player)
    named_points[player["name"]] = 10
    publish_leaderboard()
    try:
        async with saving_players:
            await save_players([{"id": player_id, "name": player["name"], "points": 10}])
    except httpx.HTTPError as error:
        # Like a round's save: these points are saved after the next round the player plays in.
        print("Could not save a reset:", repr(error))
    return JSONResponse(player)


# Moderation, for the owner. If a bad name reaches the leaderboard, the owner can give it a
# neutral name straight away, without a restart, and lock it so the player can't change it
# back. The lock is the name_locked column of the players table, so it survives a restart.
# These routes need the ADMIN_KEY environment variable, sent as "Authorization: Bearer <key>".
# The README has the commands to use them.


def admin_refusal(request):
    """None if the request carries the owner's key. Otherwise the response to send instead:
    "Not Found" if no ADMIN_KEY is set, just as for a page that doesn't exist, or a 401 for
    a missing or wrong key. compare_digest takes as long whatever the key sent, so the time
    a wrong guess takes gives away nothing about how close it was."""
    if not ADMIN_KEY:
        return PlainTextResponse("Not Found", status_code=404)
    given = request.headers.get("authorization", "")
    if not hmac.compare_digest(given.encode(), ("Bearer " + ADMIN_KEY).encode()):
        return JSONResponse({"error": "wrong or missing admin key."}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
    return None


def database_problem(error):
    """What went wrong talking to the database, in a few words, for the owner."""
    if isinstance(error, httpx.HTTPStatusError):
        text = error.response.text[:300]
        hint = " run schema.sql in the supabase sql editor to add it." if "name_locked" in text else ""
        return f"the database answered {error.response.status_code}: {text}{hint}"
    return "the database couldn't be reached: " + repr(error)


def stored_name(wanted):
    """The name as the leaderboard has it, matching wanted ignoring capitals, or wanted itself
    if no name on the leaderboard matches (the database then has the last word)."""
    wanted = wanted.strip()
    for name in named_points:
        if name.lower() == wanted.lower():
            return name
    return wanted


def made_up_name():
    """A neutral name nobody has, such as player-4821, or None in the unlikely case that every
    one we try is taken."""
    taken = {name.lower() for name in named_points}
    for attempt in range(100):
        name = f"player-{random.randint(1000, 99999)}"
        if name not in taken:
            return name
    return None


async def change_player_by_name(name, changes):
    """Write changes to the database row of the player with this exact name. Returns the rows
    changed, as they are now: one, or none if nobody has that name."""
    response = await db.patch(
        "/players",
        params={"name": "eq." + name},
        json=changes,
        headers={"Prefer": "return=representation"},
    )
    response.raise_for_status()
    return response.json()


def moderated(player_id, row):
    """After the owner changed a player's row, bring the player in memory up to date, if they
    are there, and send their open pages their new "me" message."""
    player = players.get(player_id)
    if player is None:
        return
    if player["loaded"] and player["name"]:
        player["name"], player["name_locked"] = row["name"], bool(row.get("name_locked", False))
    else:
        # Their lookup failed, or may still be on its way back with the row as it was before.
        # Use the row as it is now, and mark them loaded so an older answer is ignored.
        use_saved_row(player_id, row)
    send_to_player(player_id, sse("me", player))


async def admin_names(request):
    """Every named player, most points first, with whether their name is locked: so the owner
    can look over names beyond the top 10. The points are the server's own, which may be newer
    than the database's. "lock_column" says whether the database has the name_locked column
    yet (null if there are no players to tell from)."""
    refusal = admin_refusal(request)
    if refusal:
        return refusal
    try:
        rows = await read_all_players("*")  # "*" so this works before the name_locked column exists
    except httpx.HTTPError as error:
        return JSONResponse({"error": "couldn't read the players. " + database_problem(error)}, status_code=503)
    listing = [
        {
            "name": row["name"],
            "points": named_points.get(row["name"], row["points"]),
            "name_locked": bool(row.get("name_locked", False)),
        }
        for row in rows
    ]
    listing.sort(key=lambda row: (-row["points"], row["name"]))
    lock_column = ("name_locked" in rows[0]) if rows else None
    return JSONResponse({"players": listing, "count": len(listing), "lock_column": lock_column})


async def admin_rename(request):
    """Give a player a neutral name and lock it. Send {"name": <their name now>} and, if you
    like, "to": the new name; without one the server makes one up, such as player-4821.
    Their points stay with them.

    It works whether or not the player has the page open: the database row is changed first,
    by name (ignoring capitals), and only if that works is the server's memory changed to match.
    """
    refusal = admin_refusal(request)
    if refusal:
        return refusal
    data = await read_json(request)
    wanted, to = data.get("name"), data.get("to")
    if not isinstance(wanted, str) or not wanted.strip():
        return JSONResponse({"error": 'say whose name to change, as "name".'}, status_code=400)
    if to is not None:
        if not isinstance(to, str):
            return JSONResponse({"error": '"to" must be text.'}, status_code=400)
        to = to.strip()
        problem = names.name_problem(to)
        if problem:
            return JSONResponse({"error": problem}, status_code=400)
    if not game["ranking_loaded"]:
        # Until then the startup load could still put the old name back on the leaderboard.
        return JSONResponse({"error": "the leaderboard hasn't loaded from the database yet. try again in a minute."}, status_code=503)

    async with saving_players:  # so no round's points are saved under the old name meanwhile
        old_name = stored_name(wanted)
        for attempt in range(5):
            new_name = to if to is not None else made_up_name()
            if new_name is None:
                return JSONResponse({"error": 'couldn\'t make up a free name. give one as "to".'}, status_code=503)
            try:
                rows = await change_player_by_name(old_name, {"name": new_name, "name_locked": True})
                break
            except httpx.HTTPError as error:
                taken = isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 409
                if taken and to is None:
                    continue  # someone has the name we made up, though we didn't know: make up another
                if taken:
                    return JSONResponse({"error": "that username is taken."}, status_code=409)
                return JSONResponse({"error": "nothing has changed. " + database_problem(error)}, status_code=503)
        else:
            return JSONResponse({"error": 'couldn\'t find a free name. give one as "to".'}, status_code=503)
        if not rows:
            return JSONResponse({"error": "nobody is called that."}, status_code=404)
        row = rows[0]
        points = named_points.pop(old_name, row["points"])
        named_points[new_name] = points
        saved_ids.add(row["id"])
        moderated(row["id"], row)
    publish_leaderboard()
    return JSONResponse({"renamed": old_name, "to": new_name, "points": points, "name_locked": True})


async def admin_unlock(request):
    """Lift the lock on a name, so its player can change it again. Send {"name": <their name>}."""
    refusal = admin_refusal(request)
    if refusal:
        return refusal
    data = await read_json(request)
    wanted = data.get("name")
    if not isinstance(wanted, str) or not wanted.strip():
        return JSONResponse({"error": 'say whose name to unlock, as "name".'}, status_code=400)
    async with saving_players:
        name = stored_name(wanted)
        try:
            rows = await change_player_by_name(name, {"name_locked": False})
        except httpx.HTTPError as error:
            return JSONResponse({"error": "nothing has changed. " + database_problem(error)}, status_code=503)
        if not rows:
            return JSONResponse({"error": "nobody is called that."}, status_code=404)
        moderated(rows[0]["id"], rows[0])
    return JSONResponse({"unlocked": rows[0]["name"], "name_locked": False})


MAX_NOTICE_MINUTES = 24 * 60  # the furthest ahead an update can be announced


async def admin_notice(request):
    """Announce a system update to everyone. Send {"minutes": N}: every page shows a banner
    counting down N minutes, telling guests their points may be lost and to choose a username
    to keep them. Send {"minutes": 0} to take the banner down. Sending a new number restarts it.

    It lives in memory only, so a restart (which the update itself usually is) takes it down.
    Once the time is up the banner says the update is starting now, until it is taken down.
    """
    refusal = admin_refusal(request)
    if refusal:
        return refusal
    data = await read_json(request)
    minutes = data.get("minutes")
    # type() rather than isinstance(), because True and False count as numbers to isinstance().
    if type(minutes) not in (int, float) or not math.isfinite(minutes) or not 0 <= minutes <= MAX_NOTICE_MINUTES:
        return JSONResponse(
            {"error": f'"minutes" must be a number from 0 to {MAX_NOTICE_MINUTES}. 0 takes the notice down.'},
            status_code=400,
        )
    if minutes == 0:
        game["update_at"] = None
        return JSONResponse({"notice": None})
    game["update_at"] = time.time() + minutes * 60
    update_at = datetime.datetime.fromtimestamp(game["update_at"], datetime.timezone.utc).isoformat(timespec="seconds")
    return JSONResponse({"notice": "published", "minutes": minutes, "update_at": update_at})


@contextlib.asynccontextmanager
async def lifespan(app):
    """When the server starts: load the leaderboard, and start the round loop, the broadcaster
    and the saving of the round log.
    The game starts straight away, even if the leaderboard takes a few tries to load."""
    jobs = [load_ranking(), run_rounds(), broadcast(), save_round_logs()]
    tasks = [asyncio.create_task(job) for job in jobs]
    yield
    for task in tasks:
        task.cancel()


app = Starlette(
    routes=[
        Route("/", index),
        Route("/board", board),
        Route("/events", events),
        Route("/buy", buy, methods=["POST"]),
        Route("/name", set_name, methods=["POST"]),
        Route("/reset", reset_points, methods=["POST"]),
        Route("/admin/names", admin_names),
        Route("/admin/rename", admin_rename, methods=["POST"]),
        Route("/admin/unlock", admin_unlock, methods=["POST"]),
        Route("/admin/notice", admin_notice, methods=["POST"]),
    ],
    lifespan=lifespan,
)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--gravity", type=float, default=GRAVITY, help="pixels / second^2")
    parser.add_argument("--bounce", type=float, default=BOUNCE, help="fraction of speed kept after hitting something, 0 to 1")
    args = parser.parse_args()
    GRAVITY, BOUNCE = args.gravity, args.bounce
    # uvicorn runs everything on one thread, switching between pages whenever one is waiting,
    # so thousands of open pages are cheap. Render tells us the port through the PORT variable.
    # The short shutdown timeout lets Ctrl-C work while pages still have streams open.
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), timeout_graceful_shutdown=1)
