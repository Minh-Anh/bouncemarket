"""Everything the server keeps in memory: the one shared game, the players, the open pages, and
what is waiting to be saved.

It all resets when the server restarts, except what an update hands to the next server (see
handover.py). The whole server runs on one thread, so none of it needs locks, and every other
module reads and changes it here, as state.NAME.
"""
import asyncio
import collections
import time

from bouncemarket import config

# The one shared game that every player watches and bets on. Rounds repeat forever:
# a countdown, then a ball drops. "path" is the ball's position at every frame (None
# before the first drop) and "bucket" is where it lands. Pages are sent the path a few
# frames at a time, and the bucket when the ball lands.
game = {
    # Whose game it is (see role.py and handover.py): "waiting" on a new server until the old one
    # hands the game over, "live" on the one server running it, "moving" for the moment the old
    # server is handing it over, and "retired" on the old server from then on.
    "role": "waiting",
    "dropping": False,
    "drops_at": time.time() + config.COUNTDOWN_SECONDS,  # when the countdown ends
    "started_at": 0,  # when the ball dropped
    "start_x": None,  # where along the top the ball dropped from. The whole path follows from this one number
    "path": None,
    "bucket": None,
    "close_frame": 0,  # betting closes when the ball reaches this frame
    # The code version and physics options of the server that dropped this ball. They are this
    # server's own, unless the round was handed over part-way through by the one before.
    "commit": config.COMMIT,
    "gravity": config.GRAVITY,
    "bounce": config.BOUNCE,
    "nudge": config.NUDGE,
    # The seed of this drop's hidden nudges (see simulate_drop()). It must never reach a page
    # while the ball is falling: it is saved with the round, and on the card, once it has landed.
    "nudge_seed": None,
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

# The same for each internet address, all its players' buys together, {address: deque of
# time.monotonic()}, for MAX_BUYS_PER_ADDRESS. Emptied every round.
recent_address_buys = {}

# When each internet address's players reset their points in the last hour, {address: deque of
# time.time()}, for MAX_RESETS_PER_ADDRESS. Not handed over in an update, so a new server
# starts it afresh.
recent_resets = {}

# The same for new guests from each address, for MAX_NEW_GUESTS_PER_ADDRESS (see admit_new_guest()).
recent_new_guests = {}

# One entry per open page: a queue of messages waiting to be sent to it, and whose page it is.
# Each message in a queue is (text, tag): tag is (started_at, index) for a broadcast during a
# drop, so events() can note when it was written (see written_to), and None for anything else.
streams = {}  # {queue: player_id}

# How many pages each internet address has open, or is just opening: {address: count}, with
# no entry for an address that has none. The address is None for a page whose address we
# can't tell (see client_address()). slots_taken is the total. turned_away counts the
# connections refused since log_streams() last wrote to the log, {address: count}.
slots = collections.Counter()
slots_taken = 0
turned_away = collections.Counter()

# The newest broadcast of this round written to each player's pages, and when, in seconds since
# the drop: {player_id: (index in game["broadcasts"], seconds)}. If a player has more than one
# page open, it is the latest write to any of them. Emptied at every drop. buy() logs it.
written_to = {}

# The round log gives every player who has ever bought a share a small whole number, their
# key, and uses it in place of their long id, so each logged buy takes a few bytes.
# {player_id: key}, for the players we have asked the database about since the server started.
player_keys = {}

# Rounds waiting to be saved to the round log, oldest first, each as made by round_log.entry().
unsaved_rounds = []

# The columns of the rounds table that the database has told us it doesn't have (see
# LATER_ROUND_COLUMNS in round_log.py).
missing_round_columns = set()

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

# The handover (see role.py and handover.py).
# Set while the game is settled on one server ("live" or "retired"), and clear while that is
# still being decided ("waiting" or "moving"), which is when requests are kept waiting.
role_settled = asyncio.Event()
game_tasks = []  # the round loop and the broadcaster, once this server is running the game
taken_from = set()  # the servers whose game we have taken, so one sent twice is taken once
watching = None  # the task watching for a new server to start taking requests, once one has said hello
