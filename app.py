# /// script
# dependencies = ["httpx==0.28.1", "pillow==12.0.0", "starlette==1.10.0", "uvicorn[standard]==0.54.0"]  # keep in sync with requirements.txt
# ///
import argparse
import asyncio
import bisect
import collections
import contextlib
import datetime
import hmac
import html
import json
import math
import os
import random
import re
import secrets
import struct
import time
import uuid
import zlib

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route

import card_image  # card_image.py, in this folder: draws the best-round card as a picture for link previews
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
RULES_VERSION = 3  # saved with each round; change it whenever the pricing or the counterparty changes, so analysis.py replays each round under its own rules
PRICE_SOFTNESS = 7  # how many more shares one side needs than the other for its price to reach 0.73 (see prices_for()); higher makes each share move the price less
MAX_BUYS_PER_SECOND = 10  # the most buys one player can make in any one second; a person clicks 5 to 8.5 times a second, a script far faster
# The counterparty (see Counterparty): a few pretend traders, buying the way a person would.
COUNTERPARTY_TRADERS = 5  # how many; one alone can't click fast enough to keep up with the ball near the bottom. Each player who buys takes one's place
COUNTERPARTY_FULL_LEAN = 0.5  # a ball this far from the middle towards a wall counts as fully on its side (see counterparty_guess())
COUNTERPARTY_EDGE = 0.03  # if a trader's guess and the price are closer than this, they let the price be
COUNTERPARTY_FIRST_LOOK_FRAMES = (8, 30)  # frames before a trader first looks after the drop, by chance between the two
COUNTERPARTY_CLICK_FRAMES = (7, 12)  # frames between a trader's clicks: 5 to 8.5 a second, a person's speed
COUNTERPARTY_PAUSE_FRAMES = (10, 30)  # frames a trader waits before looking again when the price looks right
COUNTDOWN_SECONDS = 5  # the wait between one ball landing and the next one dropping
LEADERBOARD_SIZE = 10
NEIGHBORS = 2  # a player ranked below the top 10 also sees this many players either side of them
PAGE_SIZE = 1000  # the most rows Supabase returns from one request, unless its settings say otherwise

# The round log: every round a player bought in is saved to the database, for analysis later.
# See "The round log" below, and analysis.py, which reads it back.
LOG_FORMAT = 1  # saved with each round; change it whenever pack_path() or pack_buys() change what they write
COMMIT = os.environ.get("RENDER_GIT_COMMIT", "local")  # which version of this code played the round; Render sets this
UNSAVED_ROUNDS_KEPT = 20  # if the database can't be reached, this many rounds wait in memory, and older ones are dropped
KEYS_PER_REQUEST = 500  # the most players whose keys (see player_keys) are asked for in one request to the database
NOT_SEEN = 0xFFFF  # stands in for the frame a page was showing when it didn't say
TIMING_FORMAT = 1  # saved inside each round's timing; change it whenever pack_timing() changes what it writes
NO_TIME = 0xFFFFFFFF  # stands in for a time in the timing log that there isn't one of
NO_BROADCAST = 0xFFFF  # stands in for a broadcast in the timing log that there isn't one of

# The best-round card: each player's best single round, which they can share. See "The
# best-round card" below, card.js, which draws it, and card_image.py, which draws its picture.
CARD_MIN_PROFIT = 1.00  # a round makes a card only if it made more than this
PRICE_POINTS = 200  # the most points of a round's price line a card keeps
UNSAVED_CARDS_KEPT = 1000  # if the database can't be reached, this many cards wait in memory, and older ones are dropped
SHARED_KEPT = 500  # how many cards that share pages asked for are kept in memory, so a popular link doesn't ask the database every time
IMAGES_KEPT = 100  # how many link-preview pictures are kept in memory once drawn
PATHS_KEPT = 100  # how many replays' ball paths are kept in memory once worked out
SITE = "bouncemarket.minhanhday.com"  # where to play, written on the card's picture
SHARE_ID = re.compile(r"[A-Za-z0-9_-]{8}")  # what secrets.token_urlsafe(6) makes: 6 random bytes as 8 characters

# The handover: during an update the old server hands the whole game to the new one, so nobody
# playing notices. See "The handover" below, and "Updating without stopping the game" in the README.
INSTANCE = secrets.token_urlsafe(8)  # this running server's own name, made up afresh every time it starts
# The address the site is reached at from outside, which is how the old and the new server find
# each other. Render sets RENDER_EXTERNAL_URL; without either, there are no handovers.
PUBLIC_URL = (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
HANDOVER_FORMAT = 1  # change it whenever handover_snapshot() changes what it sends in a way take_over() can't read
HELLO_SECONDS = 5  # how often a new server says hello again while it waits for the game
PROBE_SECONDS = 0.5  # how often the old server asks the site's address who answers there, once a new server has said hello
WATCH_SECONDS = 20 * 60  # how long it keeps asking; Render gives up on a deploy after 15 minutes
HOLD_SECONDS = 5  # how long a request waits for a handover to finish before it is answered anyway
HAND_OVER_SECONDS = 8  # how long the old server keeps trying to deliver the game before carrying on with it itself
HAND_OVER_TRIES = 3  # how many times it tries that in one update
LAST_CHANCE_SECONDS = 3  # how long it tries once more as it shuts down, if it still has the game
RECONNECT_MS = (50, 500)  # each page is told to come back after a wait between these, by chance, so they don't all arrive at once
RECONNECT_AHEAD_FRAMES = 90  # and is first sent this much of the ball's path (1.5 seconds), to play while it does
GUEST_KEPT_SECONDS = 24 * 60 * 60  # a guest who has been away longer than this isn't handed over, so the players don't pile up for ever


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

# How the old and the new server talk during an update: each asks the site's own address, which
# reaches whichever of them is taking requests just then (see "The handover" below). The key
# is worked out from the database key, which both have, so there is nothing more to set up, and
# the database key itself is never sent. No connection is kept open between requests, so each
# one is sure to be routed afresh.
HANDOVER_KEY = hmac.new(os.environ["SUPABASE_KEY"].encode(), b"bouncemarket handover", "sha256").hexdigest()
peer = httpx.AsyncClient(
    base_url=PUBLIC_URL or "http://localhost",  # never used without a PUBLIC_URL
    headers={"Authorization": "Bearer " + HANDOVER_KEY},
    timeout=3,
    limits=httpx.Limits(max_keepalive_connections=0),
)

# Everything below lives in memory, so it resets when the server restarts.
# The whole server runs on one thread (see the bottom of the file), so none of it needs locks.

# The one shared game that every player watches and bets on. Rounds repeat forever:
# a countdown, then a ball drops. "path" is the ball's position at every frame (None
# before the first drop) and "bucket" is where it lands. Pages are sent the path a few
# frames at a time, and the bucket when the ball lands.
game = {
    # Whose game it is (see "The handover" below): "waiting" on a new server until the old one
    # hands the game over, "live" on the one server running it, "moving" for the moment the old
    # server is handing it over, and "retired" on the old server from then on.
    "role": "waiting",
    "dropping": False,
    "drops_at": time.time() + COUNTDOWN_SECONDS,  # when the countdown ends
    "started_at": 0,  # when the ball dropped
    "start_x": None,  # where along the top the ball dropped from. The whole path follows from this one number
    "path": None,
    "bucket": None,
    "close_frame": 0,  # betting closes when the ball reaches this frame
    # The code version and physics options of the server that dropped this ball. They are this
    # server's own, unless the round was handed over part-way through by the one before.
    "commit": COMMIT,
    "gravity": GRAVITY,
    "bounce": BOUNCE,
    "counterparty": None,  # the counterparty for this drop, a Counterparty, which keeps its own buys
    "sent_frames": 0,  # how many frames of the path have been sent to the pages so far
    "shares": {"left": 0, "right": 0},  # all the shares players have bought this round, added up
    "buys": [],  # every player buy this round, in order, for the round log. See buy()
    # For the round log's timing (see pack_timing()), in seconds since the drop:
    # one [built, first written, last written, pages queued, pages written] per broadcast this
    # round, and one (taken, seen, seen broadcast) per buy, in the same order as "buys".
    "broadcasts": [],
    "buy_timing": [],
    "last_bucket": None,  # where the previous ball landed
    "ranking": [],  # every named player, most points first, as [{"name": ..., "points": ...}], made from named_points
    "positions": {},  # each name's index in "ranking", so a player can be found quickly
    "minus_points": [],  # minus each player's points, in "ranking" order, so it ascends for bisect
    "ranking_loaded": False,  # whether load_ranking() has read the whole players table yet
    "update_at": None,  # when the owner has announced a system update for (see admin_notice()), or None
    "card_holders_loaded": False,  # whether load_card_holders() has read the best_rounds table yet
}

# One entry per player who has opened the page since the server started, keyed by the
# random id their browser sends:
# {"name": None, "points": 10, "shares": {"left": 0, "right": 0}, "spent": 0, "last_won": 0,
#  "loaded": True, "name_locked": False, "seen": 1700000000.0}
# "name" is None until the player claims one. "shares" and "spent" are what the player has
# bought, and paid for it, in the current round. "loaded" is explained in get_player().
# "name_locked" is True once the owner has replaced the player's name (see admin_rename()),
# and stops them changing it themselves. "seen" is when they were last here (seconds since
# 1970): when they first came, last bought, or last closed the page (see handover_snapshot()).
players = {}

# Every named player's points, {name: points}, for the leaderboard. Loaded from the database
# when the server starts, then updated here whenever a named player's points change.
named_points = {}

# The id of every player who has a row in the database: read by load_ranking() when the server
# starts, and added to whenever a player claims a name. Once the whole table has been read,
# an id that isn't here can't have a row, so get_player() doesn't need to ask the database.
saved_ids = set()

# When each player made their buys in the last second, {player_id: deque of time.monotonic()},
# so buy() can turn down a player buying faster than MAX_BUYS_PER_SECOND. Emptied every round.
recent_buys = {}

# One entry per open page: a queue of messages waiting to be sent to it, and whose page it is.
# Each message in a queue is (text, tag): tag is (started_at, index) for a broadcast during a
# drop, so events() can note when it was written (see written_to), and None for anything else.
streams = {}  # {queue: player_id}

# The newest broadcast of this round written to each player's pages, and when, in seconds since
# the drop: {player_id: (index in game["broadcasts"], seconds)}. If a player has more than one
# page open, it is the latest write to any of them. Emptied at every drop. buy() logs it.
written_to = {}

# The round log gives every player who has ever bought a share a small whole number, their
# key, and uses it in place of their long id, so each logged buy takes a few bytes.
# {player_id: key}, for the players we have asked the database about since the server started.
player_keys = {}

# Rounds waiting to be saved to the round log, oldest first, each as made by round_log().
unsaved_rounds = []
# Whether the rounds table has its timing column, as far as we know: save_rounds() stops
# sending the timing if the database says it doesn't (schema.sql not run since it was added).
timing_column = True

# The best-round card. Each player's best round so far, {player_id: record}, for the players
# whose best we know, each record as made by new_best_rounds(). Only a round that made more
# than CARD_MIN_PROFIT is ever here.
best_rounds = {}
# The players whose best round get_best_round() has looked up since the server started, or
# knows they have none, so the database is asked about each player at most once.
best_loaded = set()
# The id of every player with a row in the best_rounds table: read by load_card_holders() when
# the server starts, and added to whenever a player gets a card. Like saved_ids, it lets a new
# visitor's page start without asking the database whether they have a card.
card_holders = set()
# Cards waiting to be saved to the best_rounds table, oldest first.
unsaved_cards = []
# Cards that share pages have asked the database for lately, {share_id: record}, oldest first,
# and the link-preview pictures drawn from them, {share_id: PNG bytes}.
shared_records = {}
shared_images = {}
# The ball's path for cards whose replay was asked for lately, {share_id: [[x, y], ...]}.
shared_paths = {}

# Held while writing to the players table, so there is only ever one write on its way to the
# database, made from the names as they are at that moment. Without it, a round's points sent
# with a player's old name could arrive after their change of name, and undo it.
saving_players = asyncio.Lock()

# Held while writing best-round cards, so a player deleting their card (clear_card()) and a save
# of new cards (save_cards()) take turns, and a save can't land after a delete and undo it.
saving_cards = asyncio.Lock()


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


async def read_all_players(columns, table="/players", order="id", where=None):
    """Every row of the players table (or another table), with the columns asked for, e.g.
    "id,name,points", in the order of a column that is unique, so no row is missed or read twice.
    where picks rows, in the database's form, e.g. {"cleared": "eq.false"}.
    The database hands rows over at most PAGE_SIZE at a time, so we ask page by page."""
    rows = []
    while True:
        params = {**(where or {}), "select": columns, "limit": PAGE_SIZE, "offset": len(rows), "order": order}
        response = await db.get(table, params=params)
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


def new_player():
    """A player as they are the first time we see their id: a guest with 10 points."""
    return {
        "name": None,
        "points": 10,
        "shares": {"left": 0, "right": 0},
        "spent": 0,
        "last_won": 0,
        "loaded": False,
        "name_locked": False,
        "seen": time.time(),
    }


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
        players[player_id] = new_player()
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
    analysis.py can work them out again from the round log.
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
        anything that tells players apart (their id here, their key in analysis.py): a trader
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


def counterparty_shares(frame=None):
    """How many shares the counterparty has bought on each side so far this round: up to the
    frame given, or up to right now if none is.

    It never collects on them. They only move the price, towards its guess at where the ball
    will land. Its buys are worked out as they come due, here, before any price is given.
    """
    if not game["dropping"]:
        return {"left": 0, "right": 0}
    frame = current_frame() if frame is None else frame
    game["counterparty"].advance(frame, game["buys"])
    return shares_by_frame(game["counterparty"].buys, frame)


def prices_for(left, right):
    """What one share on each side costs once this many shares have been bought on each side,
    by players and the counterparty. A share pays 1 point if its side wins. See prices_for_net()."""
    return prices_for_net(left - right)


def prices_for_net(net):
    """What one share on each side costs when net more shares have been bought on the left than
    on the right (fewer, if net is below 0).

    Only the difference counts, as in a real market, where buying one side is the same as
    someone selling it to you: a buy on the left and a buy on the right cancel out. So a share
    moves the price as much at the end of a round as at the start, however much has been bought
    before. The two sides start at 0.50, and a side PRICE_SOFTNESS shares ahead costs 0.73, one
    twice that far ahead 0.88 (a logistic curve, the one prediction-market makers use). The two
    prices always add up to 1.
    """
    left_price = round(1 / (1 + math.exp(-net / PRICE_SOFTNESS)), 2)
    left_price = min(0.99, max(0.01, left_price))  # never free, and never a sure loss
    return {"left": left_price, "right": round(1 - left_price, 2)}


def costs_for(left, right):
    """What buying the next share on each side is charged, once this many shares have been bought
    on each side. See costs_for_net()."""
    return costs_for_net(left - right)


def costs_for_net(net):
    """What buying the next share on each side is charged, when net more shares have been bought
    on the left than on the right.

    A buy moves the price, so the share is charged the average of the price along that move,
    not the price before it: the cost a logarithmic market maker charges, PRICE_SOFTNESS * ln(1 - p
    + p * e^(1 / PRICE_SOFTNESS)) for a side whose price is p. Charging the price before the move
    gave away half a price step on every share, and made a Left share and then a Right share cost
    less than the 1 point the pair is sure to pay.

    At this cost, buying both sides gains nothing. Equal shares on both sides bring the price back
    where it started, so the buys pair off: a Left buy at some net with a Right buy back from one
    above it. The two exact costs add up to exactly 1, and rounded to the cent they still do, as one
    is 1 minus the other. So any set of buys that's sure to pay costs exactly what it pays, and
    rounding to the nearest cent, not up, keeps the house from taking a cut. At 0.50 a side, a
    share costs 0.52.
    """
    left_chance = 1 / (1 + math.exp(-net / PRICE_SOFTNESS))
    step = math.exp(1 / PRICE_SOFTNESS)

    def cost(chance):
        exact = PRICE_SOFTNESS * math.log(1 - chance + chance * step)
        return min(0.99, max(0.01, round(exact, 2)))  # never free, and never a sure loss

    return {"left": cost(left_chance), "right": cost(1 - left_chance)}


def market(frame=None):
    """The shares bought on each side at the frame given, or right now if none is, by players and
    the counterparty together."""
    theirs = counterparty_shares(frame)
    return game["shares"]["left"] + theirs["left"], game["shares"]["right"] + theirs["right"]


def prices(frame=None):
    """Each side's price at the frame given, or right now if none is: the chance the market gives
    it. What a share is charged is costs(), a little more, as buying moves the price."""
    return prices_for(*market(frame))


def costs(frame=None):
    """What buying the next share on each side is charged at the frame given, or right now if none is."""
    return costs_for(*market(frame))


def sse(event, data):
    """Format one server-sent event, the browser's built-in format for a stream of messages."""
    # No spaces after commas and colons: the same JSON, a little smaller to send to every page.
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def send(queue, message, tag=None):
    """Queue a message for one page. A page that has fallen far behind misses it instead.
    tag is for broadcasts only (see streams)."""
    if not queue.full():
        queue.put_nowait((message, tag))


def note_written(player_id, tag):
    """Note that a broadcast has just been written to one of this player's pages: handed to
    the network, as events() only goes on to the next message once the last one is sent."""
    started_at, index = tag
    if not game["dropping"] or game["started_at"] != started_at:
        return  # a broadcast of a round that has ended
    now = time.time() - started_at
    record = game["broadcasts"][index]
    record[1] = now if record[1] is None else min(record[1], now)
    record[2] = now if record[2] is None else max(record[2], now)
    record[4] += 1
    newest = written_to.get(player_id)
    if newest is None or now >= newest[1]:
        written_to[player_id] = (index, now)


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
    recent_buys.clear()  # nobody can buy between rounds, so nothing in it is still in the last second
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


# The round's timing, for working out how fast each player answers what they are sent. All
# times are in microseconds since the drop, by the clock of the server that took them.
TIMING_BROADCAST_COLUMNS = (
    ("built", "I"),  # when broadcast() made the message and queued it for every page
    ("first", "I"),  # when it was handed to the network for the first page; NO_TIME if for none
    ("last", "I"),  # and for the last page
    ("queued", "I"),  # how many pages it was queued for
    ("written", "I"),  # how many it was written to before the round ended
)
TIMING_BUY_COLUMNS = (
    ("taken", "I"),  # when buy() took the buy, a moment after the request reached the server
    ("seen", "I"),  # when the newest broadcast of this round was written to the buyer's pages; NO_TIME if none had been
    ("seen_broadcast", "H"),  # which broadcast that was, counting from 0; NO_BROADCAST if none
)
TIMING_HEADER = "<BII"  # TIMING_FORMAT, how many broadcasts, how many buys


def pack_timing(broadcasts, buy_timing):
    """A round's timing as bytes, from game["broadcasts"] and game["buy_timing"], packed by
    column like pack_buys()."""
    def micro(seconds):
        return NO_TIME if seconds is None else min(NO_TIME - 1, max(0, round(seconds * 1_000_000)))

    numbers = [micro(b[0]) for b in broadcasts] + [micro(b[1]) for b in broadcasts] + [micro(b[2]) for b in broadcasts]
    numbers += [b[3] for b in broadcasts] + [b[4] for b in broadcasts]
    numbers += [micro(t[0]) for t in buy_timing] + [micro(t[1]) for t in buy_timing]
    numbers += [NO_BROADCAST if t[2] is None else min(NO_BROADCAST - 1, t[2]) for t in buy_timing]
    layout = TIMING_HEADER + "".join(f"{len(broadcasts)}{letter}" for name, letter in TIMING_BROADCAST_COLUMNS)
    layout += "".join(f"{len(buy_timing)}{letter}" for name, letter in TIMING_BUY_COLUMNS)
    return zlib.compress(struct.pack(layout, TIMING_FORMAT, len(broadcasts), len(buy_timing), *numbers), 9)


def unpack_timing(data):
    """The timing that pack_timing() packed, as {"broadcasts": [(built, first, last, queued,
    written)], "buys": [(taken, seen, seen_broadcast)]}, the times in seconds since the drop and
    None where there isn't one. "buys" lines up with the round's buys, in the same order."""
    data = zlib.decompress(data)
    version, n_broadcasts, n_buys = struct.unpack_from(TIMING_HEADER, data)
    if version != TIMING_FORMAT:
        raise ValueError(f"timing format {version}, which this version of app.py can't unpack")
    layout = "<" + "".join(f"{n_broadcasts}{letter}" for name, letter in TIMING_BROADCAST_COLUMNS)
    layout += "".join(f"{n_buys}{letter}" for name, letter in TIMING_BUY_COLUMNS)
    numbers = struct.unpack_from(layout, data, struct.calcsize(TIMING_HEADER))
    columns, at = [], 0
    for count in [n_broadcasts] * len(TIMING_BROADCAST_COLUMNS) + [n_buys] * len(TIMING_BUY_COLUMNS):
        columns.append(numbers[at:at + count])
        at += count
    seconds = lambda micro: None if micro == NO_TIME else micro / 1_000_000
    built, first, last, queued, written, taken, seen, seen_broadcast = columns
    return {
        "broadcasts": [(seconds(b), seconds(f), seconds(l), q, w) for b, f, l, q, w in zip(built, first, last, queued, written)],
        "buys": [(seconds(t), seconds(s), None if i == NO_BROADCAST else i) for t, s, i in zip(taken, seen, seen_broadcast)],
    }


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
        "commit_id": game["commit"],
        "gravity": game["gravity"],
        "bounce": game["bounce"],
        "start_x": game["start_x"],
        "frames": len(game["path"]),
        "close_frame": game["close_frame"],
        "bucket": game["bucket"],
        "format": LOG_FORMAT,
        "rules": RULES_VERSION,
        "path": to_bytea(pack_path(game["path"])),
        "buys": game["buys"],
        # None if the timing doesn't line up with the buys, which shouldn't happen.
        "timing": to_bytea(pack_timing(game["broadcasts"], game["buy_timing"]))
        if len(game["buy_timing"]) == len(game["buys"]) else None,
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

    global timing_column
    rows = []
    for log in logs:
        buys = [(frame, seen, player_keys[player_id], *rest) for frame, seen, player_id, *rest in log["buys"]]
        row = {**log, "n_buys": len(buys), "buys": to_bytea(pack_buys(buys))}
        if not timing_column:
            row.pop("timing", None)
        rows.append(row)

    async def write(rows):
        return await db.post(
            "/rounds",
            params={"on_conflict": "started_at"},
            json=rows,
            headers={"Prefer": "resolution=ignore-duplicates"},
        )

    response = await write(rows)
    # PGRST204: a column the database doesn't have. The rounds are saved without their timing
    # rather than not at all, until the server restarts after schema.sql has been run.
    if response.status_code == 400 and timing_column and "PGRST204" in response.text and "timing" in response.text:
        timing_column = False
        print("The rounds table has no timing column: run schema.sql. Saving rounds without their timing until then.")
        response = await write([{key: value for key, value in row.items() if key != "timing"} for row in rows])
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


# The best-round card. Each player has one: their best single round, for them to show off
# outside the game. A round becomes the best when it settles with a profit of more than
# CARD_MIN_PROFIT and more than the player's best so far (a tie doesn't replace it). The card is
# a record of the round, made here from the buys the server took itself: nothing a page sends
# can make or change one. Each record is a row of the best_rounds table, and stays there when a
# better round replaces it, so the link to it keeps working. card.js draws it, on the player's
# own page and on the public page at /r/<share_id>.


def price_line():
    """The left side's price over the round that just finished, from the drop until trading
    closed, as [[frame, price]]: the first frame, then every frame where the price changed.
    The right side's price is always 1 minus it. In a round with more than PRICE_POINTS
    changes, the price at PRICE_POINTS evenly spaced frames instead."""
    close = game["close_frame"]
    game["counterparty"].advance(close, game["buys"])  # all its buys, up to the close
    buys = sorted([*game["counterparty"].buys, *((buy[0], buy[3]) for buy in game["buys"])])
    shares = {"left": 0, "right": 0}
    left = []  # the left price at every frame, after that frame's buys
    taken = 0
    for frame in range(close + 1):
        while taken < len(buys) and buys[taken][0] <= frame:
            shares[buys[taken][1]] += 1
            taken += 1
        left.append(prices_for(shares["left"], shares["right"])["left"])
    points = [[frame, price] for frame, price in enumerate(left) if frame == 0 or price != left[frame - 1]]
    if len(points) > PRICE_POINTS:
        points = [[frame, left[frame]] for frame in (round(i * close / (PRICE_POINTS - 1)) for i in range(PRICE_POINTS))]
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
    return sse("card", {"card": record and public_record(record), "fresh": fresh})


def new_best_rounds():
    """After a round is settled: make a card for every player whose round made more than
    CARD_MIN_PROFIT and more than their best round so far, and send it to their open pages.

    It works from game["buys"], the buys the server took itself, at the frame and price the
    server charged. The cost is everything a player paid this round, on both sides, and the
    payout is 1 for each of their shares on the side the ball landed on, so buys on both sides
    are netted into one profit. A losing round can never make a card, as its profit is below 0.
    """
    trades = {}  # {player_id: [[frame, side, price, shares], ...]}
    for frame, seen_frame, player_id, side, price, points in game["buys"]:
        trades.setdefault(player_id, []).append([frame, side, price, 1])
    settled_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    line = None  # worked out only if someone gets a card
    for player_id, theirs in trades.items():
        cost = round(sum(price * shares for frame, side, price, shares in theirs), 2)
        payout = sum(shares for frame, side, price, shares in theirs if side == game["bucket"])
        profit = round(payout - cost, 2)
        best = best_rounds.get(player_id)
        if profit <= CARD_MIN_PROFIT or (best and profit <= best["profit"]):
            continue
        line = line or price_line()
        record = {
            "share_id": secrets.token_urlsafe(6),
            "player": player_id,
            "username": players[player_id]["name"],
            "round_started_at": datetime.datetime.fromtimestamp(game["started_at"], datetime.timezone.utc).isoformat(),
            "settled_at": settled_at,
            "outcome": game["bucket"],
            "frames": len(game["path"]),
            "close_frame": game["close_frame"],
            "trades": theirs,
            "prices": line,
            "cost": cost,
            "payout": payout,
            "profit": profit,
            # The ball's whole path follows from these, so a replay can play it again (see simulate_drop()).
            "trajectory": {"start_x": game["start_x"], "gravity": game["gravity"], "bounce": game["bounce"], "commit_id": game["commit"]},
            "cleared": False,  # True once the player deletes it from their page (see clear_card())
        }
        best_rounds[player_id] = record
        card_holders.add(player_id)
        unsaved_cards.append(record)
        del unsaved_cards[:-UNSAVED_CARDS_KEPT]
        send_to_player(player_id, card_message(record, fresh=True))


CARD_COLUMNS = "share_id,player,username,round_started_at,settled_at,outcome,frames,close_frame,trades,prices,cost,payout,profit,trajectory,cleared"


async def load_card_holders():
    """Read the id of every player with a card they haven't deleted, once, when the server
    starts, so a new visitor's page can start without asking the database whether they have one
    (see get_best_round()). If the database can't be reached, we keep trying every 30 seconds."""
    while True:
        try:
            rows = await read_all_players("player", table="/best_rounds", order="share_id", where={"cleared": "eq.false"})
        except httpx.HTTPError as error:
            print("Could not load who has a best-round card, trying again in 30 seconds:", repr(error))
            await asyncio.sleep(30)
            continue
        card_holders.update(row["player"] for row in rows)
        game["card_holders_loaded"] = True
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
    if player_id in best_loaded:
        return
    if game["card_holders_loaded"] and player_id not in card_holders:
        best_loaded.add(player_id)
        return
    try:
        response = await db.get("/best_rounds", params={
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
    if player_id in best_loaded:
        return  # another request for the same player finished first
    best_loaded.add(player_id)
    # A round played while we waited may already have made a better card.
    if rows and (player_id not in best_rounds or rows[0]["profit"] > best_rounds[player_id]["profit"]):
        best_rounds[player_id] = rows[0]


async def save_cards():
    """Write every card in unsaved_cards to the best_rounds table. The database skips a card
    it already has, so trying again after a failure that was really a success is harmless."""
    async with saving_cards:
        cards = list(unsaved_cards)  # more may be made while we wait for the database; they are saved next time
        response = await db.post(
            "/best_rounds",
            params={"on_conflict": "share_id"},
            json=cards,
            headers={"Prefer": "resolution=ignore-duplicates"},
        )
        response.raise_for_status()
        saved = {card["share_id"] for card in cards}
        unsaved_cards[:] = [card for card in unsaved_cards if card["share_id"] not in saved]


async def clear_card(request):
    """Delete a player's card from their page, when they ask. Send {"player": <their id>}.

    Their record starts over: the next round that makes more than CARD_MIN_PROFIT makes a new
    card. Nothing is removed, though: every card they've had is marked cleared, so it no longer
    counts as their best, and its share link keeps working for anyone it was sent to.
    """
    data = await read_json(request)
    player_id = parse_id(data.get("player"))
    if player_id not in players:
        return JSONResponse({"error": "unknown player."}, status_code=400)
    async with saving_cards:  # so a save on its way can't land after this and bring a card back
        if game["role"] != "live":
            return moved(request)  # the game was handed to a new server while we waited our turn
        try:
            response = await db.patch(
                "/best_rounds", params={"player": "eq." + player_id, "cleared": "eq.false"}, json={"cleared": True})
            response.raise_for_status()
        except httpx.HTTPError as error:
            print("Could not clear a card:", repr(error))
            return JSONResponse({"error": "couldn't delete your card. try again in a minute."}, status_code=503)
        for card in unsaved_cards:
            if card["player"] == player_id:
                card["cleared"] = True
        best_rounds.pop(player_id, None)
        card_holders.discard(player_id)
        best_loaded.add(player_id)  # known now: no card, so a lookup still on its way mustn't bring one back
    send_to_player(player_id, card_message(None, fresh=False))  # their other open pages too
    return JSONResponse({"cleared": True})


async def save_cards_forever():
    """Save each new card a moment after it's made, for as long as the server runs. If the
    database can't be reached, the cards wait in unsaved_cards and we try every 30 seconds.
    A card that isn't saved yet still works: share pages look in unsaved_cards first."""
    while True:
        await asyncio.sleep(1)
        if not unsaved_cards:
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
    if share_id in shared_records:
        return shared_records[share_id]
    for card in unsaved_cards:
        if card["share_id"] == share_id:
            return card
    response = await db.get("/best_rounds", params={"share_id": "eq." + share_id, "select": CARD_COLUMNS})
    response.raise_for_status()
    rows = response.json()
    if not rows:
        return None
    remember(shared_records, share_id, rows[0], SHARED_KEPT)
    return rows[0]


def by_side(record):
    """Each side a card's player bought, in the order left, right: how many shares, and the
    average price paid, as [(side, shares, average)]."""
    sides = []
    for name in ("left", "right"):
        bought = [(price, shares) for frame, side, price, shares in record["trades"] if side == name]
        if bought:
            count = sum(shares for price, shares in bought)
            sides.append((name, count, sum(price * shares for price, shares in bought) / count))
    return sides


def two_places(number):
    """A number to two decimal places, halves rounded up, the same as card.js does it."""
    return f"{math.floor(number * 100 + 0.5) / 100:.2f}"


def what_happened(record):
    """What a card's player bought and where the ball landed, e.g.
    "bought Left x6 at avg 0.14, landed Left", or with both sides,
    "bought Left x5 at avg 0.23 and Right x2 at avg 0.50, landed Left"."""
    bought = " and ".join(f"{side.capitalize()} x{shares} at avg {two_places(average)}" for side, shares, average in by_side(record))
    return f"bought {bought}, landed {record['outcome'].capitalize()}"


def share_text(record):
    """The line the share button sends before the link, as card.js writes it:
    "bouncemarket: bought Left x6 at avg 0.14, landed Left, +5.16 in one round"."""
    return f"bouncemarket: {what_happened(record)}, +{two_places(record['profit'])} in one round"


def site_origin(request):
    """The address the site was reached at, such as https://bouncemarket.minhanhday.com, for the
    links in a share page's preview tags, which must be whole addresses. Render answers every
    request over https, but passes it on to us as plain http, saying so in X-Forwarded-Proto."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    return f"{scheme}://{request.headers.get('host', request.url.netloc)}"


async def share_page(request):
    """The public page for one card, /r/<share_id>: the card, read-only, and a button to play.
    Anyone can open it, with nothing remembered. Its preview tags give chat and social apps a
    title, a line of text and a picture of the card (see share_image()) for when the link is posted."""
    share_id = request.path_params["share_id"]
    try:
        record = await find_card(share_id)
    except httpx.HTTPError as error:
        print("Could not load a shared card:", repr(error))
        record, status = None, 503
    else:
        status = 200 if record else 404
    origin = site_origin(request)
    if record:
        title = f"+{two_places(record['profit'])} in one round on bouncemarket"
        description = what_happened(record) + "."
        image = f"{origin}/r/{share_id}.png"
    else:
        title, description, image = "bouncemarket", "a ball will fall. trade on where it lands.", ""
    message = {404: "this round can't be found.", 503: "this round can't be loaded right now. try again in a minute."}.get(status, "")
    with open("share.html") as file:
        page = file.read()
    # The card goes in as JSON, with "<" written as < so nothing in it can end the script tag early.
    card = json.dumps(public_record(record) if record else None).replace("<", "\\u003c")
    for key, value in {
        "title": title, "description": description, "url": f"{origin}/r/{share_id}", "image": image, "message": message,
    }.items():
        page = page.replace("{{" + key + "}}", html.escape(value))
    page = page.replace("{{image_tags}}", "" if not record else (
        f'<meta property="og:image" content="{html.escape(image)}">\n'
        '  <meta property="og:image:width" content="1200">\n'
        '  <meta property="og:image:height" content="630">\n'
        f'  <meta property="og:image:alt" content="{html.escape(description)}">\n'
        f'  <meta name="twitter:image" content="{html.escape(image)}">'
    ))
    page = page.replace("{{card}}", card)  # last, so nothing in the card is taken for a {{...}} to fill in
    return HTMLResponse(page, status_code=status, headers={"Cache-Control": "public, max-age=300"})


async def share_image(request):
    """The card as a 1200 by 630 picture, /r/<share_id>.png, for link previews and for the
    share menu's "copy image". Drawn by
    card_image.py the first time it's asked for, in a thread of its own so the game doesn't
    wait, then kept in memory for the next time."""
    share_id = request.path_params["share_id"]
    if share_id not in shared_images:
        try:
            record = await find_card(share_id)
        except httpx.HTTPError as error:
            print("Could not load a shared card:", repr(error))
            return PlainTextResponse("try again in a minute", status_code=503)
        if record is None:
            return PlainTextResponse("Not Found", status_code=404)
        # The picture says where to play (SITE, not the address the request came in at: the
        # picture is kept for everyone, so no one request may choose what it says).
        remember(shared_images, share_id, await asyncio.to_thread(card_image.render, record, SITE), IMAGES_KEPT)
    return Response(shared_images[share_id], media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


async def share_replay(request):
    """The ball's path for a card's round, /r/<share_id>/replay, as {"path": [[x, y], ...]}, one
    position a frame, for the replay on the share page.

    It isn't stored: the record keeps the start_x, gravity and bounce the whole path follows from,
    and the drop is played again here (see simulate_drop()), which takes a few hundredths of a
    second. If the physics has changed since, so that the drop no longer lasts as long or lands
    on the same side, the answer is 404 and the share page shows the card without a replay.
    """
    share_id = request.path_params["share_id"]
    if share_id not in shared_paths:
        try:
            record = await find_card(share_id)
        except httpx.HTTPError as error:
            print("Could not load a shared card:", repr(error))
            return JSONResponse({"error": "try again in a minute."}, status_code=503)
        if record is None:
            return JSONResponse({"error": "no such round."}, status_code=404)
        seed = record["trajectory"]
        states, bucket = simulate_drop(seed["start_x"], seed["gravity"], seed["bounce"])
        if bucket != record["outcome"] or len(states) != record["frames"]:
            return JSONResponse({"error": "this round can't be replayed."}, status_code=404)
        remember(shared_paths, share_id, positions(states), PATHS_KEPT)
    return JSONResponse({"path": shared_paths[share_id]}, headers={"Cache-Control": "public, max-age=86400"})


async def run_rounds():
    """The round loop: wait for the countdown, drop a ball, wait for it to land, pay out. Forever,
    or until the game is handed to a new server (see "The handover" below).

    It picks up from wherever the game is, as a round handed over by the server before may
    already have its ball in the air: then it starts by waiting for that ball to land.
    """
    while True:
        if not game["dropping"]:
            await asyncio.sleep(max(0, game["drops_at"] - time.time()))
            if not await still_ours():
                return
            # Drop from somewhere in the middle two-thirds, so the ball never just slides down a slanted wall.
            # Rounded to 15 significant digits, as that is all the database gives back: a
            # double needs 17 to come back exactly, and the path is chaotic, so a replay from
            # the 15 the round log holds would part from the real drop a few hundred frames in.
            # Any number of 15 digits does come back exactly, so this is the drop that was played.
            start_x = float(f"{random.uniform(WIDTH / 6, WIDTH * 5 / 6):.15g}")
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
                commit=COMMIT,
                gravity=GRAVITY,
                bounce=BOUNCE,
                counterparty=Counterparty(path, close_frame, start_x),
                sent_frames=0,
                buys=[],
                broadcasts=[],
                buy_timing=[],
            )
            written_to.clear()
        await asyncio.sleep(max(0, game["started_at"] + len(game["path"]) * FRAME_SECONDS - time.time()))
        if not await still_ours():
            return  # the new server has the round now, and pays it out
        rows = settle()
        new_best_rounds()
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


def public_state():
    """The state of the game that every page is sent, without the ball: the "game" message
    is this and, while the ball is falling, its next positions (see broadcast())."""
    message = {
        "dropping": game["dropping"],
        "seconds_to_drop": max(0, game["drops_at"] - time.time()),
        "prices": prices(),
        "costs": costs(),  # what the buy buttons charge
        "betting_open": betting_open(),
        "online": len(streams),
        "last_bucket": game["last_bucket"],
    }
    if game["update_at"] is not None:
        # Seconds until the announced update, which the page counts down on its own clock.
        message["update_in"] = max(0, round(game["update_at"] - time.time()))
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
    if game["dropping"]:
        frame = current_frame()
        start = min(frame, game["sent_frames"])
        message["frame"] = frame
        message["first_position"] = start
        message["positions"] = [rounded(position) for position in game["path"][start:game["sent_frames"]]]
    return sse("game", message)


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
        await asyncio.sleep(1 / BROADCASTS_PER_SECOND)
        if game["role"] == "retired":
            return  # the game has been handed to a new server, which sends these now
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

        message = public_state()
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
        tag = None
        queues = list(streams)
        random.shuffle(queues)
        if dropping:
            tag = (game["started_at"], len(game["broadcasts"]))
            game["broadcasts"].append([time.time() - game["started_at"], None, None, len(queues), 0])
        for queue in queues:
            send(queue, text, tag)


async def index(request):
    return FileResponse("index.html")


async def card_script(request):
    return FileResponse("card.js", media_type="text/javascript")


async def card_style(request):
    return FileResponse("card.css", media_type="text/css")


async def board_script(request):
    return FileResponse("board.js", media_type="text/javascript")


async def replay_script(request):
    return FileResponse("replay.js", media_type="text/javascript")


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
    after each round, the public "game" message when it connects (see catch_up()) and then
    from broadcast(), and its own "leaderboard"
    message (the top players, plus the players around it) when it connects and whenever the
    leaderboard is refreshed. If the player has a best-round card, a "card" message brings it
    when the page connects, and again whenever a round becomes their new best.
    """
    player_id = parse_id(request.query_params.get("player"))
    if player_id is None:
        return JSONResponse({"error": "bad player id."}, status_code=400)
    player = await get_player(player_id)
    await get_best_round(player_id)

    async def stream():
        if game["role"] == "retired":
            # The game was handed to a new server while we were looking this player up.
            yield come_back()
            return
        queue = asyncio.Queue(maxsize=100)
        streams[queue] = player_id
        try:
            yield sse("me", player)
            yield leaderboard_message(player)
            if player_id in best_rounds:
                yield card_message(best_rounds[player_id], fresh=False)
            yield catch_up()
            while True:
                item = await queue.get()
                if item is None:
                    return  # the game has been handed to a new server: see end_stream()
                message, tag = item
                yield message
                # We get here once the message has been handed to the network.
                if tag is not None:
                    note_written(player_id, tag)
        finally:
            del streams[queue]  # the page has gone away
            player["seen"] = time.time()

    # The headers ask anything between the server and the browser not to hold events back.
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)


async def buy(request):
    """Buy one share on one side, at its current cost (see costs_for_net())."""
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
    # At most MAX_BUYS_PER_SECOND in any one second, so a script can't flood the market.
    now = time.monotonic()
    times = recent_buys.setdefault(player_id, collections.deque())
    while times and times[0] <= now - 1:
        times.popleft()
    if len(times) >= MAX_BUYS_PER_SECOND:
        return JSONResponse({"error": "too many buys at once, slow down."}, status_code=429)
    times.append(now)
    player["seen"] = time.time()
    price = costs(frame)[side]
    if price > player["points"]:
        return JSONResponse({"error": "you don't have enough points."}, status_code=400)
    # For the round log. The page says which frame it was showing when the button was pressed,
    # which is a little behind the server's frame. It's the page's word, so only good for analysis.
    seen_frame = data.get("frame")
    if type(seen_frame) is not int or not 0 <= seen_frame < NOT_SEEN:
        seen_frame = NOT_SEEN
    game["buys"].append((frame, seen_frame, player_id, side, price, player["points"]))
    # For the round log's timing: when the server took this buy, and the newest broadcast it had
    # written to this player's pages by then, so analysis can tell how long the player took to
    # answer what they were sent, apart from where their page came in the order of sending.
    seen_index, seen_at = written_to.get(player_id, (None, None))
    game["buy_timing"].append((time.time() - game["started_at"], seen_at, seen_index))
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
            if game["role"] != "live":
                return moved(request)  # the game was handed to a new server while we waited our turn
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
        if game["role"] != "live":
            return moved(request)  # the game was handed to a new server while we waited our turn
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
    cards = await rename_cards(row["id"], new_name)
    return JSONResponse({"renamed": old_name, "to": new_name, "points": points, "name_locked": True, "cards": cards})


async def rename_cards(player_id, name):
    """After the owner has renamed a player, put the new name on all their best-round cards too,
    old ones included, so a bad name doesn't live on in share pages and link previews. A card
    made while the player was a guest has no name, and keeps none. Returns "renamed", or what
    went wrong; the player's rename itself has worked either way."""
    for card in [best_rounds.get(player_id), *unsaved_cards, *shared_records.values()]:
        if card and card["player"] == player_id and card["username"] is not None:
            card["username"] = name
    shared_images.clear()  # pictures of the old name; they are drawn again when asked for
    try:
        response = await db.patch(
            "/best_rounds",
            params={"player": "eq." + player_id, "username": "not.is.null"},
            json={"username": name},
        )
        response.raise_for_status()
    except httpx.HTTPError as error:
        return "the cards in the database still have the old name; rename them again to try again. " + database_problem(error)
    if player_id in best_rounds:
        send_to_player(player_id, card_message(best_rounds[player_id], fresh=False))
    return "renamed"


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
        if game["role"] != "live":
            return moved(request)  # the game was handed to a new server while we waited our turn
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


# The handover. An update starts a new server beside the old one, and for a minute or so both
# run: the host (Render) sends every new request to the new server once it is up, while the
# pages' open streams stay with the old one. The game lives in one server's memory, so it has to
# move from the old to the new, at that moment, or a page would watch the ball on one server
# and buy on the other. It goes like this:
#
# 1. The new server, before it takes any requests, says hello at the site's own address
#    (PUBLIC_URL). No request reaches it yet, so the old server answers. The new one now knows
#    there is a game to wait for (its role is "waiting"), and the old one starts watching.
# 2. The old server asks the same address "who are you?" twice a second. The first time the new
#    server answers, the host has switched over.
# 3. The old server stops changing anything ("moving") and sends the new one everything that
#    lives only in memory: the round being played, ball in the air and all, every player's
#    points and shares, the leaderboard, and whatever isn't saved yet (handover_snapshot()).
# 4. The new server takes it and carries on from the same frame (take_over()). Requests that
#    reached it before this were kept waiting, a second at most, and are answered now.
# 5. The old server ends every page's stream ("retired"). Each browser connects again by
#    itself, which reaches the new server, and the ball carries on from where it was.
#
# If any step fails, the game is never left without a server: the old one carries on until the
# host shuts it down, when it tries once more, and a new server that hears nothing starts a game
# of its own, into which a late handover is merged. The worst case is what an update used to
# be: a new game, and guests back on 10 points.

# Set while the game is settled on one server ("live" or "retired"), and clear while that is
# still being decided ("waiting" or "moving"), which is when requests are kept waiting.
role_settled = asyncio.Event()
game_tasks = []  # the round loop and the broadcaster, once this server is running the game
taken_from = set()  # the servers whose game we have taken, so one sent twice is taken once
watching = None  # the task watching for a new server to start taking requests, once one has said hello

# What is handed over of the round being played, or of the wait for the next one.
ROUND_KEYS = (
    "dropping", "drops_at", "started_at", "start_x", "path", "bucket", "close_frame",
    "commit", "gravity", "bounce", "sent_frames", "shares", "buys", "broadcasts", "buy_timing", "last_bucket",
)


def set_role(role):
    game["role"] = role
    if role in ("live", "retired"):
        role_settled.set()
    else:
        role_settled.clear()


async def still_ours():
    """Whether the game is still this server's to run. The round loop asks before each drop
    and each payout; while a handover is under way it waits here to see how it ends."""
    await role_settled.wait()
    return game["role"] == "live"


def go_live():
    """Start running the game on this server, from wherever the game is."""
    set_role("live")
    game_tasks.extend(asyncio.create_task(job) for job in (run_rounds(), broadcast()))


def start_afresh():
    """Start a game of our own, with a full countdown: there was no server before us to take
    one from, or it never handed its game over."""
    game["drops_at"] = time.time() + COUNTDOWN_SECONDS
    go_live()


def come_back():
    """Tells a page's browser how long to wait before it connects again, in the stream's own
    format. The browser does that by itself whenever its stream ends. Each page gets a different
    wait, by chance, so a crowd doesn't all arrive in the same instant."""
    return f"retry: {random.randint(*RECONNECT_MS)}\n\n"


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
    if game["dropping"]:
        frame = current_frame()
        end = min(frame + RECONNECT_AHEAD_FRAMES + 1, len(game["path"]))
        if betting_open(frame):
            sent_early = game["close_frame"] - FINAL_STRETCH_FRAMES + EARLY_AHEAD_FRAMES
            end = min(end, max(frame + BUFFER_FRAMES + 1, sent_early), game["close_frame"] + BUFFER_FRAMES)
        start = min(game["sent_frames"], end)
        message["frame"] = frame
        message["first_position"] = start
        message["positions"] = [rounded(position) for position in game["path"][start:end]]
    return sse("game", message)


def end_stream(queue, parting):
    """End one page's stream, after a last message and telling its browser when to connect
    again. Whatever was still waiting to be sent is dropped, as the new server sends everything again."""
    while not queue.empty():
        queue.get_nowait()
    queue.put_nowait((parting, None))
    queue.put_nowait((come_back(), None))
    queue.put_nowait(None)  # events() ends the stream when it reaches this


def moved(request):
    """The answer to a request that reached a server the game has left: try again, and the
    request will reach the new one. A stream is told to connect again, and anything else gets a
    503 with "moved", at which the page asks again by itself (post() in index.html)."""
    if request.url.path == "/events":
        return Response(come_back(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
    return JSONResponse(
        {"error": "the game is moving to a new server. try again in a moment.", "moved": True},
        status_code=503,
        headers={"Retry-After": "1"},
    )


def ours(handler):
    """Wraps every route that reads or changes the game, so it only runs on the server that
    has the game. On a new server still waiting for it, or an old one just handing it over, the
    request waits, as that takes well under a second. If a new server has waited HOLD_SECONDS
    and been handed nothing, it starts a game of its own."""
    async def gated(request):
        # Read what was sent first, so that nothing is waited for between the check below and
        # the handler's own work: a buy can't slip in after the game has been handed over.
        await request.body()
        if game["role"] in ("waiting", "moving"):
            try:
                await asyncio.wait_for(role_settled.wait(), HOLD_SECONDS)
            except TimeoutError:
                if game["role"] == "waiting":
                    print("Nobody handed the game over, so starting a new one.")
                    start_afresh()
        if game["role"] != "live":
            return moved(request)
        return await handler(request)
    return gated


def handover_refusal(request):
    """None if the request comes from our own other server, which sends HANDOVER_KEY.
    Otherwise "Not Found", just as for a page that doesn't exist."""
    given = request.headers.get("authorization", "")
    if not hmac.compare_digest(given.encode(), ("Bearer " + HANDOVER_KEY).encode()):
        return PlainTextResponse("Not Found", status_code=404)
    return None


def other_server(answer):
    """Whether an answer from the site's address, {"instance": ...}, came from a server other than this one."""
    return isinstance(answer, dict) and answer.get("instance") not in (None, INSTANCE)


async def health(request):
    """Says this server is up, and which one it is. Render asks, to know when a new server is
    ready for requests, and the old server asks during an update, to know when the new one has
    started getting them (see watch_for_successor())."""
    return JSONResponse({"instance": INSTANCE}, headers={"Cache-Control": "no-store"})


async def find_predecessor():
    """When this server starts, before it takes requests: ask at the site's address whether a
    server is running the game. Returns its name, or None if there isn't one, which is also the
    answer when it is too old to know about handovers, or can't be reached in three tries.

    A server that is itself still waiting for the game counts: with two updates close together
    it is the one that will have the game by the time requests come to us, and it hears of us
    when we say hello again (see keep_saying_hello())."""
    if not PUBLIC_URL:
        return None
    for attempt in range(3):
        try:
            response = await peer.post("/handover/hello", json={"instance": INSTANCE})
            if response.status_code < 500:
                answer = response.json() if response.status_code == 200 else None
                if other_server(answer) and answer.get("role") in ("live", "moving", "waiting"):
                    return answer["instance"]
                return None
        except (httpx.HTTPError, ValueError) as error:
            print("Could not ask for the server before us:", repr(error))
        await asyncio.sleep(1)
    return None


async def keep_saying_hello():
    """On a new server, for as long as it waits for the game: say hello again every
    HELLO_SECONDS. The server that ends up handing us the game may not be the one that heard
    us the first time. With two updates close together, it is the server of the update before
    ours, which was itself still waiting when we started."""
    while game["role"] == "waiting":
        await asyncio.sleep(HELLO_SECONDS)
        if game["role"] == "waiting":
            with contextlib.suppress(httpx.HTTPError):
                await peer.post("/handover/hello", json={"instance": INSTANCE})


async def handover_hello(request):
    """A new server, just started and not yet taking requests, asks who is running the game.
    If that's us, we start watching for the moment it does take them."""
    global watching
    refusal = handover_refusal(request)
    if refusal:
        return refusal
    if game["role"] == "live" and (watching is None or watching.done()):
        print("A new server has started. Watching for it to take over.")
        watching = asyncio.create_task(watch_for_successor())
    return JSONResponse({"instance": INSTANCE, "role": game["role"]})


async def watch_for_successor():
    """On the old server, once a new one has said hello: ask the site's address who answers
    there, every PROBE_SECONDS. While that is still us, requests are still coming to us. The
    first time it's another server, the host has switched over, and we hand the game on."""
    deadline = time.monotonic() + WATCH_SECONDS
    tries = 0
    while game["role"] == "live" and tries < HAND_OVER_TRIES and time.monotonic() < deadline:
        await asyncio.sleep(PROBE_SECONDS)
        try:
            answer = (await peer.get("/health")).json()
        except (httpx.HTTPError, ValueError):
            continue
        if other_server(answer):
            tries += 1
            if await hand_over(HAND_OVER_SECONDS) != "unreachable":
                return


def handover_snapshot():
    """Everything the next server needs to carry on the game exactly where it is.

    The players sent are everyone with a page open, everyone holding shares in this round,
    every named player, and the guests who have played and were last here within
    GUEST_KEPT_SECONDS. A visitor who only looked, and left, is the same made afresh. A guest
    who left longer ago is forgotten here, as every guest used to be at every update:
    otherwise the players would be handed from server to server for ever, and only grow.
    Times are the clock's own (seconds since 1970), which both servers' clocks agree on
    closely enough.
    """
    online = set(streams.values())
    now = time.time()

    def kept(player_id, player):
        if player_id in online or player["name"] or player["spent"] or any(player["shares"].values()):
            return True
        played = player["points"] != 10 or player["last_won"]
        return played and now - player["seen"] < GUEST_KEPT_SECONDS

    return {
        "format": HANDOVER_FORMAT,
        "from": INSTANCE,
        "rules": RULES_VERSION,
        "game": {key: game[key] for key in ROUND_KEYS},
        "players": {player_id: player for player_id, player in players.items() if kept(player_id, player)},
        "named_points": named_points if game["ranking_loaded"] else None,
        "unsaved_rounds": unsaved_rounds,
        "unsaved_cards": unsaved_cards,
    }


async def hand_over(seconds):
    """Hand the game to the new server, trying for up to this many seconds. Returns "taken",
    "refused" (it answered, but can't take this game: it reads a different HANDOVER_FORMAT),
    or "unreachable". Unless it was taken, the game carries on here.

    Both saving locks are held throughout, so no change of name or deleted card is half-way
    to the database as the game moves: what is sent is everything, and nothing changes here
    after it. Requests that arrive meanwhile wait (see ours()), and so does the round loop.
    """
    async with saving_players, saving_cards:
        if game["role"] != "live":
            return "unreachable"
        set_role("moving")
        outcome = "unreachable"
        try:
            body = zlib.compress(json.dumps(handover_snapshot()).encode())
            deadline = time.monotonic() + seconds
            while outcome == "unreachable" and time.monotonic() < deadline:
                try:
                    response = await peer.post("/handover/take", content=body)
                    answer = response.json()
                except (httpx.HTTPError, ValueError):
                    answer = None
                if other_server(answer):
                    outcome = "taken" if response.status_code == 200 and answer.get("took") else "refused"
                else:
                    await asyncio.sleep(0.25)  # the request came back to us, or nowhere: the switch isn't complete yet
        except BaseException as error:
            # Stopped part-way (the server is shutting down), or something that shouldn't go
            # wrong did. The game is still ours, and mustn't be left frozen.
            print("Could not hand the game over:", repr(error))
            set_role("live")
            raise
        if outcome != "taken":
            print(f"Could not hand the game over: {outcome}.")
            set_role("live")
            return outcome
        print(f"Handed the game to the new server, {answer['instance']}, with {len(streams)} pages open.")
        set_role("retired")
        # The new server saves these now. Writing them from here as well could undo something
        # a player does there, such as deleting a card.
        unsaved_rounds.clear()
        unsaved_cards.clear()
        parting = parting_message()
        for queue in streams:
            end_stream(queue, parting)
        return outcome


async def handover_take(request):
    """The old server hands us the game, as made by handover_snapshot() and squeezed with zlib."""
    refusal = handover_refusal(request)
    if refusal:
        return refusal
    try:
        handed = json.loads(zlib.decompress(await request.body()))
    except (zlib.error, ValueError):
        return JSONResponse({"error": "couldn't read that."}, status_code=400)
    readable = isinstance(handed, dict) and handed.get("format") == HANDOVER_FORMAT
    # Not from ourselves (the old server's request can come back to it, mid-switch), and not
    # if we are the old server, with a game of our own on its way out.
    if not readable or handed.get("from") == INSTANCE or game["role"] not in ("waiting", "live"):
        return JSONResponse({"instance": INSTANCE, "took": False}, status_code=409)
    if handed["from"] not in taken_from:  # the old server sends it again if our answer got lost
        taken_from.add(handed["from"])
        take_over(handed)
    return JSONResponse({"instance": INSTANCE, "took": True})


def take_over(handed):
    """Carry on the game the old server handed us.

    Normally we have been waiting for it, with no game of our own. Then everything is taken as
    it is, the round being played included, and the round loop starts from there: the same
    ball, at the same frame, with the same shares. The counterparty isn't sent. It is made
    again from the path and the buys, which always gives the same counterparty (see Counterparty).

    Their round is called off, and everyone's shares in it refunded, in two cases: if we play
    by different rules (RULES_VERSION), as a round can't change rules half-way, or if we gave
    up waiting and have a game of our own running. In that second case the players are merged
    into ours: a guest who has already come to us started here on 10 points, so the points
    they had with the old server take the place of those 10. A named player we already hold
    was loaded from the database, where their points are saved after every round.
    """
    theirs = handed["game"]
    waiting = game["role"] == "waiting"
    carry_on = waiting and theirs["dropping"] and handed["rules"] == RULES_VERSION
    for player_id, carried in handed["players"].items():
        player = new_player()
        player.update({key: carried[key] for key in player if key in carried})
        if not carry_on:
            player["points"] = round(player["points"] + player["spent"], 2)
            player["shares"], player["spent"] = {"left": 0, "right": 0}, 0
        mine = players.get(player_id)
        if mine is None:
            players[player_id] = player
        elif mine["name"] is None and player["name"] is None:
            mine["points"] = round(mine["points"] + player["points"] - 10, 2)
        if player["name"]:
            saved_ids.add(player_id)

    known = {log["started_at"] for log in unsaved_rounds}
    unsaved_rounds.extend(log for log in handed["unsaved_rounds"] if log["started_at"] not in known)
    known = {card["share_id"] for card in unsaved_cards}
    for card in handed["unsaved_cards"]:
        if card["share_id"] not in known:
            unsaved_cards.append(card)
        # A card this new isn't in the database yet, where get_best_round() would look for it.
        best = best_rounds.get(card["player"])
        if not card["cleared"] and (best is None or card["profit"] > best["profit"]):
            best_rounds[card["player"]] = card
            card_holders.add(card["player"])

    if not waiting:
        print(f"Merged {len(handed['players'])} players from the old server, {handed['from']}, into the game already running here.")
        for queue, player_id in streams.items():
            send(queue, sse("me", players[player_id]))
        publish_leaderboard()
        return
    if handed["named_points"] is not None:
        # The old server's leaderboard is newer than what we read from the database as we started.
        named_points.clear()
        named_points.update(handed["named_points"])
    if carry_on:
        game.update({key: theirs[key] for key in ROUND_KEYS})
        game["counterparty"] = Counterparty(game["path"], game["close_frame"], game["start_x"])
    else:
        game["last_bucket"] = theirs["last_bucket"]
        # Between rounds, their countdown carries on. A round called off starts a new countdown.
        game["drops_at"] = time.time() + COUNTDOWN_SECONDS if theirs["dropping"] else max(theirs["drops_at"], time.time())
    state = "carrying on its round" if carry_on else "its round called off" if theirs["dropping"] else "between rounds"
    print(f"Took over the game from the old server, {handed['from']}: {len(handed['players'])} players, {state}.")
    go_live()
    publish_leaderboard()


@contextlib.asynccontextmanager
async def lifespan(app):
    """When the server starts: load the leaderboard, start saving the round log and the cards,
    and either wait to be handed the game by the server before us (see "The handover" above) or,
    if there isn't one, start the round loop and the broadcaster straight away, even if the
    leaderboard takes a few tries to load.

    When it stops: if it still has the game, try once more to hand it to a new server, then
    save what is left to save.
    """
    jobs = [load_ranking(), load_card_holders(), save_round_logs(), save_cards_forever()]
    tasks = [asyncio.create_task(job) for job in jobs]
    predecessor = await find_predecessor()
    if predecessor:
        print(f"Waiting to be handed the game by the old server, {predecessor}.")
        tasks.append(asyncio.create_task(keep_saying_hello()))
    else:
        start_afresh()
    yield
    if watching:
        tasks.append(watching)
    for task in tasks + game_tasks:
        task.cancel()
    await asyncio.gather(*tasks, *game_tasks, return_exceptions=True)  # all stopped, so nothing changes under the handover
    if PUBLIC_URL and game["role"] == "live":
        with contextlib.suppress(Exception):  # whatever goes wrong, still save what is left below
            await hand_over(LAST_CHANCE_SECONDS)
    for save, unsaved in ((save_rounds, unsaved_rounds), (save_cards, unsaved_cards)):
        if unsaved:
            with contextlib.suppress(httpx.HTTPError, KeyError):
                await save()


app = Starlette(
    routes=[
        Route("/", index),
        Route("/card.js", card_script),
        Route("/card.css", card_style),
        Route("/board.js", board_script),
        Route("/replay.js", replay_script),
        Route("/r/{share_id}.png", share_image),  # before /r/{share_id}, which would take "<id>.png" for an id
        Route("/r/{share_id}/replay", share_replay),
        Route("/r/{share_id}", share_page),
        Route("/board", board),
        Route("/health", health),
        # ours() keeps these to the one server that has the game: see "The handover" above.
        Route("/events", ours(events)),
        Route("/buy", ours(buy), methods=["POST"]),
        Route("/name", ours(set_name), methods=["POST"]),
        Route("/reset", ours(reset_points), methods=["POST"]),
        Route("/card/clear", ours(clear_card), methods=["POST"]),
        Route("/admin/names", ours(admin_names)),
        Route("/admin/rename", ours(admin_rename), methods=["POST"]),
        Route("/admin/unlock", ours(admin_unlock), methods=["POST"]),
        Route("/admin/notice", ours(admin_notice), methods=["POST"]),
        Route("/handover/hello", handover_hello, methods=["POST"]),
        Route("/handover/take", handover_take, methods=["POST"]),
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
