# The server

One process on one thread, running one game that lives in its memory. `app.py`, in the folder above, starts it. [How it works](../docs/how-it-works.md) explains the design; this is the map.

## What is where

| | |
| --- | --- |
| `config.py` | The settings: what the host sets in the environment, and every number that can be tuned. |
| `state.py` | Everything kept in memory: the game, the players, the open pages, what is waiting to be saved. |
| `db.py` | The connection to the database. |
| **The game** | |
| `game/` | The rules, and nothing else: `board.py`, `physics.py`, `pricing.py`, `counterparty.py`, `names.py`. |
| `rounds.py` | The round loop: a countdown, a drop, a payout. |
| `market.py` | The market of the round being played: whether it is open, and each side's price. |
| `broadcast.py` | The "game" message every page is sent, with the ball's next positions. |
| `streams.py` | Queues a message for a page, or for all of a player's pages. |
| `players.py` | Makes players, and loads and saves the named ones. |
| `leaderboard.py` | The ranking, and the part of it each page is sent. |
| `limits.py` | How many pages and new guests one internet address may have. |
| **What is kept of a round** | |
| `round_log.py`, `packing.py` | The round log: every round and its buys, packed small and saved. |
| `cards.py`, `card_text.py`, `card_image.py` | The best-round card: its record, its words and its picture. |
| **Updates** | |
| `role.py`, `handover.py` | Which server has the game, and handing it to the next one during a deploy. |
| **The web** | |
| `web/app.py` | Every address the server answers at, and what runs when it starts and stops. |
| `web/pages.py` | The game page, the files in [`frontend/`](../frontend/README.md), and `/board`. |
| `web/play.py` | A page's stream (`/events`), and `/buy`, `/name`, `/reset`, `/card/clear`. |
| `web/share.py` | A card's public page, picture and replay, under `/r/`. |
| `web/admin.py` | The owner's routes, under `/admin/`. |
| `web/inputs.py` | Reads what a request sent. |

## How it fits together

- **`game/` stands alone.** It never reads the environment, the database or `state`, so a round can be played again anywhere from the few numbers saved of it. A change to the pricing or the counterparty needs a new `RULES_VERSION` (`game/__init__.py`); a change to the board or the physics means old rounds only replay under the commit that played them.
- **Modules are imported whole**, and their names written in full: `state.players`, `config.COUNTDOWN_SECONDS`, `streams.send()`. It shows where each thing lives, and lets a test change one while the server runs.
- **The game never waits for the database.** Rounds, cards and points are saved by tasks of their own, which try again if the database is down.
- **Everything in `state.py` is lost on a restart** unless `handover.py` sends it to the next server. Add to `handover_snapshot()` and `take_over()` whatever new state should survive an update.

## Settings

Set in the environment (in `.env` on your own computer, in the dashboard on Render):

| Variable | |
| --- | --- |
| `SUPABASE_URL`, `SUPABASE_KEY` | The database. The server won't start without them. |
| `ADMIN_KEY` | Optional. The owner's key for the routes under `/admin/` (see [Moderating names](../docs/moderation.md)). |
| `PUBLIC_URL` | The address the site is reached at, for [handovers](../docs/deploys.md). Render sets `RENDER_EXTERNAL_URL`, which does as well. |
| `PORT` | The port to listen on. 8000 unless set; Render sets it. |
| `MAX_STREAMS`, `MAX_STREAMS_PER_ADDRESS`, `MAX_BUYS_PER_ADDRESS`, `RESET_COOLDOWN_SECONDS`, `MAX_RESETS_PER_ADDRESS`, `MAX_NEW_GUESTS_PER_ADDRESS` | See [Limits](../docs/limits.md). |

`app.py` also takes `--gravity`, `--bounce` and `--nudge` (see [Tuning the physics](../docs/how-it-works.md#tuning-the-physics)). Every other number is a constant in `config.py` or, for the rules of the game, in `game/`.
