# How it works

The server runs one game for everyone. It simulates each drop in full the moment the ball is released, but keeps the result to itself.

Every open page holds one stream from the server (as [server-sent events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)) carrying four kinds of message:

- A public "game" message, identical for every player: the countdown, the price of each side, the seconds until an announced update if there is one (see [Announcing an update](deploys.md#announcing-an-update)), and while the ball is falling, its next positions. It is sent only as often as needed, because at scale these messages are most of the server's work and bandwidth: twice a second early in a drop, 10 times a second for the last 1.5 seconds before betting closes and until the ball lands, once a second between drops, and straight away when a drop starts or the ball lands. Each time, the pages are sent it in a new random order. With a thousand pages open the last is written a noticeable moment after the first, and in a fixed order (the order pages connected) the same page would always hear first. A page is also sent one of its own when it connects, with the ball's positions from where it is now, so a page opened during a drop shows the ball at once.
- A private "me" message with your name, points and shares, and `name_locked`, which is true if the owner has locked your name, sent when you connect, after each round, and when the owner changes your name. Buying a share, setting a name and resetting return the same information.
- A "leaderboard" message with the top 10 named players, sent when you connect, after each round, and whenever anyone sets or changes a name or a named player resets. If you're ranked below the top 10, it also has the two players either side of you, which the page shows under a "…" with your row in yellow. The server reads every named player's id and points from the database once when it starts, then keeps the ranking up to date in memory: it is the only thing that changes anyone's points, so it never needs to read them again.
- A "card" message with your best-round card, sent when you connect, if you have one, and whenever a round becomes your new best or you delete the card (see [The best-round card](#the-best-round-card)).

Ball positions are sent early and the page plays them back on its own clock, which keeps the ball moving smoothly when the network delivers messages unevenly. Early in a drop they are sent up to 48 frames (0.8 seconds) early. For the last 1.5 seconds before betting closes, only 12 frames (a fifth of a second) early. Betting closes that fifth of a second before the ball passes the last row, and while betting is open no page is ever sent a position past the last row, so nobody can bet on a result they have already been sent. Positions are rounded to a tenth of a pixel to keep messages short (`POSITION_DECIMALS` in `bouncemarket/config.py`).

The server is a single process on a single thread that switches between pages whenever one is waiting, so each open page costs very little. On a laptop it held 5,000 open pages using about 60% of one processor core.

Your browser remembers a random id that tells the server which player you are. Whoever has that id is that player, so it works like a password and is never shown to anyone else.

If the database can't be reached, the game carries on: everyone can still play, but names can't be claimed and saved points aren't loaded until it is back.

Players without a name live only in the server's memory. An update carries them over to the new server (see [Updating without stopping the game](deploys.md#updating-without-stopping-the-game)); they lose their points only if the server stops with no new one to hand them to, such as after a crash, or if an update comes more than a day after they were last here and they hold no shares (`GUEST_KEPT_SECONDS` in `bouncemarket/config.py`), so that old guests don't pile up for ever. Once you claim a name, your id, name and points are a row in the Supabase `players` table. The server saves your points there after every round you play in, and loads them back the next time your browser connects.

The database is asked about a player only when it can't already know the answer. The read at startup gives the server the id of every player with a row, and it adds to that list as players claim names. So once that read has worked, a browser id that isn't on the list is a new visitor with nothing saved, and their page starts without the database being asked anything: a big crowd arriving at once costs the database nothing. A player who does have a row is looked up the first time they connect after a restart, for their name, points and lock, and not again until the next restart. Until the startup read has worked (in the first moment after a restart, or while the database is down, when it is tried again every 30 seconds) the server can't tell newcomers from returning players, so it looks up every player as they connect. Claiming a first name also looks the player up once more just before writing the row, in case the row was written moments ago by the previous server, which keeps running for a little while during a deploy; otherwise the new row would overwrite it.

## The best-round card

After each round settles, `new_best_rounds()` in `bouncemarket/cards.py` works out each buyer's profit from the buys the server itself took, at the frame and price it charged. No route accepts a card, a profit or a trade from a page, so nothing a page sends can make or change one. A round that made more than `CARD_MIN_PROFIT` (1.00) and more than the player's best so far becomes a record, which is sent to that player's open pages as a "card" message (marked fresh) and saved a moment later to the `best_rounds` table by a separate task, like the round log. Pages that connect later are sent the player's card, not fresh.

Each record holds a short random `share_id` (8 characters, the only thing in the link), the player's id and their name at the time, the round's start and settle times, where the ball landed, the player's buys, the left side's price at every frame it changed (cut down to 200 points in a very busy round; the right side's price is always 1 minus it), the cost, payout and profit, and the `start_x`, `gravity`, `bounce`, `nudge`, `nudge_seed` and commit the ball's whole path follows from, for a replay later. Rows are never changed when a better round comes along, so old links keep working, and a player's best is their row with the most profit that they haven't deleted. Deleting a card (`POST /card/clear`, from the card's trash can) sets `cleared` on all the player's rows rather than removing them, so their share links still work. The player's id is never sent to a page or shown on a share page.

The card is drawn by `frontend/card.js` (styles in `card.css`), on the game page and on the share page `/r/<share_id>` (`frontend/share.html`, filled in by `share_page()` in `bouncemarket/web/share.py`). Its preview tags, and the share menu's "copy image", use `/r/<share_id>.png`, a 1200 by 630 picture of the card drawn by `bouncemarket/card_image.py` with Pillow, in a thread so the game doesn't wait, and kept in memory once drawn. The address on the picture is `SITE` in `bouncemarket/config.py`, not the address a request came in at, as one picture is kept for everyone. A card not saved yet is still found, as share pages look at the unsaved ones first.

The replay (`frontend/replay.js`) gets the ball's path from `/r/<share_id>/replay`. The path isn't stored: the server plays the drop again from the record's `start_x`, `gravity`, `bounce`, `nudge` and `nudge_seed` (a few hundredths of a second) and keeps the last 100 in memory. If the physics has changed since, so the drop no longer lasts as long or lands on the same side, that answers 404 and the page shows the card without a replay. The board is drawn by `board.js`, which the game page uses too.

Like the players table, the server reads who has a card once at startup (`load_card_holders()`), so a new visitor's page starts without asking the database, and looks up a returning player's best the first time they connect. If the database is down, cards are still made and shown, and wait in memory (up to 1,000) to be saved.

## Tuning the physics

Gravity, bounce and the hidden nudges can be passed on the command line:

```
uv run --env-file .env app.py --gravity 1500 --bounce 0.3 --nudge 0
```

| Option      | Default | Meaning                                                        |
| ----------- | ------- | -------------------------------------------------------------- |
| `--gravity` | 400     | Downward acceleration, in pixels / second². Higher is faster.  |
| `--bounce`  | 0.85    | Fraction of speed kept after hitting a pin or wall, 0 to 1. Lower makes the ball slow down more in the dense rows. |
| `--nudge`   | 0.003   | Typical size, in radians, of the hidden random turn at each pin bounce (see [The hidden nudges](randomness.md#the-hidden-nudges)). 0 for none, which lets players work out the rest of the drop from the path. |

Everything else (board size, pin rows, ball and pin size) is a constant in `bouncemarket/game/board.py`.

The round log saves the gravity, bounce and nudges each round was played with, so rounds played under different options replay correctly. A change to one of the constants is only recorded as a new `commit_id`.
