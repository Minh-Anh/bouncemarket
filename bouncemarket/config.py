"""The server's settings: what the host sets in the environment, and every number that can be tuned.

The rules of the game itself (the board, the physics, the pricing, the counterparty) are not
here but in game/, as old rounds are replayed by them. Other modules read these as
config.NAME, at the moment they need them, so a test can change one while the server runs.
"""
import os

# The database, a Supabase project (see db.py, which won't start without these two). The secret
# key gives full access, so it stays on the server, in the environment, and the page never
# talks to Supabase itself.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

# The owner's key for the moderation routes under /admin (see web/admin.py).
# Optional: without it those routes don't exist, as far as anyone outside can tell.
ADMIN_KEY = os.environ.get("ADMIN_KEY", "").strip()

# The address the site is reached at from outside, which is how the old and the new server find
# each other. Render sets RENDER_EXTERNAL_URL; without either, there are no handovers.
PUBLIC_URL = (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
COMMIT = os.environ.get("RENDER_GIT_COMMIT", "local")  # which version of this code played the round; Render sets this
PORT = int(os.environ.get("PORT", 8000))  # Render says which port to listen on

# The physics each ball is dropped with. These three can also be set on the command line,
# e.g. --bounce 0.3 (see app.py in the folder above).
GRAVITY = 400.0  # pixels / second^2
BOUNCE = 0.85  # fraction of speed kept after hitting something, 0 to 1
NUDGE = 0.003  # the typical size, in radians (0.17 degrees), of the hidden random turn the ball takes at each pin bounce; 0 for none (see simulate_drop())

# Rounds.
COUNTDOWN_SECONDS = 5  # the wait between one ball landing and the next one dropping
MAX_BUYS_PER_SECOND = 10  # the most buys one player can make in any one second; a person clicks 5 to 8.5 times a second, a script far faster

# Broadcasts (see broadcast.py). A frame is a sixtieth of a second.
BROADCASTS_PER_SECOND = 10  # the most often every page is sent the state of the game; see broadcast()
FRAMES_PER_BROADCAST = 6  # 60 frames a second / 10 broadcasts a second
FINAL_STRETCH_FRAMES = 90  # for the last 1.5 seconds before betting closes, and until the ball lands, send every broadcast
EARLY_AHEAD_FRAMES = 48  # before that, send positions this far ahead (0.8 seconds), so a message is needed only every half second
POSITION_DECIMALS = 1  # ball positions are sent rounded to tenths of a pixel; 0 (whole pixels) is ~15% smaller but may look steppy
BUFFER_FRAMES = 12  # ball positions are sent this many frames early, so the page can play back smoothly

# Limits that stop one person turning free points into real ones (see
# docs/limits.md). Each can be set on the host without changing the code; 0 for no limit.
MAX_BUYS_PER_ADDRESS = int(os.environ.get("MAX_BUYS_PER_ADDRESS", 20))  # the most buys all the players at one internet address can make in any one second
RESET_COOLDOWN_SECONDS = int(os.environ.get("RESET_COOLDOWN_SECONDS", 10 * 60))  # how long a player waits after a reset before they can reset again
MAX_RESETS_PER_ADDRESS = int(os.environ.get("MAX_RESETS_PER_ADDRESS", 6))  # the most resets all the players at one internet address can make in any hour
MAX_NEW_GUESTS_PER_ADDRESS = int(os.environ.get("MAX_NEW_GUESTS_PER_ADDRESS", 20))  # the most new guests, each with 10 free points, from one internet address in any hour

# Limits on open pages, so that nobody can slow the game for everyone by opening thousands (see
# take_slot()). Both can be set on the host without changing the code; 0 for no limit per address.
MAX_STREAMS = int(os.environ.get("MAX_STREAMS", 1500))  # the most pages open at once. Past it a new page is told the game is full, and keeps trying. Set it to what the server can carry
MAX_STREAMS_PER_ADDRESS = int(os.environ.get("MAX_STREAMS_PER_ADDRESS", 20))  # the most from one internet address: plenty for a household, an office or a classroom, which share one
STREAM_LOG_SECONDS = 60  # how often the log says who has many pages open and who was turned away, when anyone has or was

# The leaderboard.
LEADERBOARD_SIZE = 10
NEIGHBORS = 2  # a player ranked below the top 10 also sees this many players either side of them

# The database.
PAGE_SIZE = 1000  # the most rows Supabase returns from one request, unless its settings say otherwise
UNSAVED_ROUNDS_KEPT = 20  # if the database can't be reached, this many rounds wait in memory, and older ones are dropped
KEYS_PER_REQUEST = 500  # the most players whose keys (see player_keys) are asked for in one request to the database

# The best-round card: each player's best single round, which they can share. See
# cards.py, frontend/card.js, which draws it, and card_image.py, which draws its picture.
CARD_MIN_PROFIT = 1.00  # a round makes a card only if it made more than this
PRICE_POINTS = 200  # the most points of a round's price line a card keeps
UNSAVED_CARDS_KEPT = 1000  # if the database can't be reached, this many cards wait in memory, and older ones are dropped
SHARED_KEPT = 500  # how many cards that share pages asked for are kept in memory, so a popular link doesn't ask the database every time
IMAGES_KEPT = 100  # how many link-preview pictures are kept in memory once drawn
PATHS_KEPT = 100  # how many replays' ball paths are kept in memory once worked out
SITE = "bouncemarket.minhanhday.com"  # where to play, written on the card's picture

# The handover (see handover.py).
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
