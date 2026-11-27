# bouncemarket

A ball drops from a random spot in the middle two-thirds of the top, bounces off pins, and lands in one of two buckets. The board is a trapezoid: its walls lean inwards, so the pins get closer together towards the bottom and the ball slows down as it falls.

https://bouncemarket.minhanhday.com/

## Requirements

[uv](https://docs.astral.sh/uv/). To install it:

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

uv installs the Python dependencies automatically on first run: the Starlette web framework, the Pillow imaging library (which draws the best-round card for link previews), the uvicorn server with its "standard" extras (uvloop, a faster event loop, and httptools, a faster HTTP parser, both used automatically on Linux and macOS), and the httpx web client. Each is pinned to an exact version, at the top of `app.py` for uv and in `requirements.txt` for Render; the two lists must stay the same. The pinned Starlette needs Python 3.11 or newer.

A [Supabase](https://supabase.com) project, which stores the named players and the round log. To set it up:

1. In the Supabase dashboard, open the SQL editor and run the contents of `schema.sql`. It only adds what is missing, so run it again whenever it changes.
2. Create a file called `.env` in this folder (git ignores it) with the project's URL and its secret key, both found under the project's API settings:

```
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-secret-key
ADMIN_KEY=a-long-random-key
```

Use the secret key (also called the service role key), not the publishable one, and never put it in the page or commit it.

`ADMIN_KEY` is optional. It is the owner's key for moderating names (see [Moderating names](#moderating-names)); without it, the moderation routes don't exist. Make it long and random, for example with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`, and keep it as secret as the database key.

## Run locally

```
uv run --env-file .env app.py
```

Then open http://localhost:8000.

## Deploy

The app is hosted on [Render](https://render.com), which reads `render.yaml`, installs `requirements.txt`, and runs `python app.py`. Every push to `main` redeploys it. `SUPABASE_URL`, `SUPABASE_KEY` and, optionally, `ADMIN_KEY` are set under Environment in the Render dashboard.

When an update changes `schema.sql`, run it in the Supabase SQL editor before pushing. The old code doesn't mind the additions, and the new code may need them (see [Moderating names](#moderating-names) for what happens if it is pushed first).

## How to play

Everyone who opens the page watches the same ball and trades in the same market. A new ball drops 5 seconds after the last one lands.

You start with 10 points. Each "buy 1 share" button buys one share on that side at the price shown. A share pays 1 point if the ball lands on its side and nothing if it doesn't. So a share bought at 0.30 makes 0.70 if it wins and loses 0.30 if it doesn't. While you hold shares, each side's card shows what you would net if the ball lands on that side: your shares on that side minus everything you paid this round.

You can buy only while the ball is falling: from the moment it drops until just before it passes the last row of pins. Shares can't be sold back; you hold them until the ball lands.

Both sides start at 0.50 each round, and the two prices always add up to 1. A side's price depends only on how many more shares have been bought on it than on the other side this round. A buy on Left and a buy on Right cancel out, as in a real market, where buying one side is the same as someone selling it to you. So a share moves the price by the same amount at the end of a round as at the start, and a few traders piling in late can swing it hard. A side 7 shares ahead costs 0.73 and 14 ahead costs 0.88, along a logistic curve, the one prediction-market makers use (`PRICE_SOFTNESS` in `app.py`; higher makes each share move the price less). Every buy pushes that side's price up and the other side's down. Being right early, while a side is still cheap, pays more than piling in once everyone agrees.

The price on each side's card is what a share costs you, which is a little more than the market's chance for that side, shown in the odds bar. Your buy moves the price, so you pay the average price along that move, not the price before it: the cost a logarithmic market maker charges (`costs_for_net()` in `app.py`), rounded to the nearest cent. At 0.50 a side, a share costs 0.52. Charging the price before the move gave away about half a price step on every share, and let a script buy Left at 0.50 and then Right at 0.46, for a pair sure to pay 1.00. At the market maker's cost, equal shares on both sides always cost exactly what they are sure to pay, so there is nothing to gain from buying both sides, and the house takes no cut.

Each player can buy at most 10 shares in any one second (`MAX_BUYS_PER_SECOND`). A person clicks 5 to 8.5 times a second; a buy past the limit is turned down.

There is also a counterparty, so the market moves even with nobody else on: five pretend traders who buy the way a person might. Each guesses where the ball will land with a naive rule: the further it is from the middle and the lower it is, the surer they get. Each looks at the ball and the price; if the price is off from their guess by 0.03 or more, they buy one share of the side that looks too cheap and look again a moment later, at a person's clicking speed (5 to 8.5 clicks a second), and otherwise they look again after a short pause. That makes bursts of buying, sometimes two traders in the same instant. Their buying drifts the price towards where the ball seems to be heading, hardest near the bottom, and they push back when players pile onto the side the ball isn't on.

The pretend traders make way for real people. Each different player who buys in a round takes one trader's place for the rest of that round, so once five people have bought, the pretend traders have stopped and the market is all human. Players are counted by their buys, not by who has the page open, so a crowd that's only watching still gets a moving market.

The counterparty is also the seller of every share you buy: it keeps what you pay and pays out the winners.

Anyone can play straight away, and is ranked on the leaderboard from the start: until you claim a name you appear as "you", where your points would put you, with a "set username" button. Other people's guests aren't shown, only named players and you. Names are unique, ignoring capitals, and from then on your points are saved. The pencil button after your name on the leaderboard changes it to any name nobody else is using, and your points stay with you.

A username is 1 to 16 plain letters (A to Z, either case), numbers, `-` or `_`, with at least one letter or number. The page drops any other character as you type. Some names are reserved because they would look like the site itself talking (`you`, `guest`, `admin`, `moderator` and the like), and a word filter turns down slurs, obscenities and hate symbols, including the usual disguises such as numbers for letters. The rules are in `names.py`. They are checked only when a name is set or changed, so a name saved before a rule existed stays as it is, unless the owner changes it (see [Moderating names](#moderating-names)). If the owner has changed your name, it is locked and you can't change it yourself.

Anyone can start over with 10 points, whatever they have now, named or not. The circling-arrows button on your own leaderboard row does it, asking first only if you have more than 10 points to lose. If you can't afford a share on either side and hold none, a "reset to 10" button also appears under the buy buttons. A reset can't be used while you hold shares in the round being played, or someone could spend everything pushing a price around, start over, and do it again for free. A guest's reset changes nothing in the database and nothing on anyone else's leaderboard, only their own.

## Your best round

Once a round makes you more than 1.00 point, a card for it appears above the leaderboard: the profit, a chart of the price over the round with a mark for each of your buys (circles for Left, triangles for Right), what you bought and at what average price, where the ball landed, and your shares, cost and payout. Profit is everything your winning shares paid out minus everything you paid that round, on both sides. Only a round that beats your best so far replaces the card; a tie doesn't. A new best is marked "new best round" until the next round has been played, inside the card, so it never covers the board or the buy buttons.

The card stays when you reload the page or reset to 10: a reset changes your points, not your record. Guests get one too.

The trash can at the card's top right takes the card away, after asking "delete this card?" inside the card itself. It's hidden while the card is marked "new best round", and appears in that spot once the mark goes. Your record starts over, so your next round over 1.00 makes a new card, however small. Links you've already shared keep working.

The "share" button opens a small menu:

- **copy text** copies a line and a link, such as:

  ```
  bouncemarket: bought Left x6 at avg 0.14, landed Left, +5.16 in one round https://bouncemarket.minhanhday.com/r/k3J9xQ2a
  ```

- **copy image** copies a picture of the card, with "bouncemarket" and the site's address on it, for pasting where a link won't do. It's left out in browsers that can't put a picture on the clipboard.
- **share…** opens the device's own share sheet, in browsers that have one.

The link opens a public page for anyone, with nothing to sign in to. It plays the round again: the ball falls while the card's price line draws along with it, each of your buys pops onto the chart as you made it, and a line under the board says what's happening ("bought left at 0.13", "trading closed", "landed left · +5.16 points"). Then the "play" button appears. "skip" jumps to the end and "watch again" starts over. With reduced motion turned on in the device's settings, the page shows the finished card without the replay. Posted in a chat app or social feed, the link unfurls with the same picture of the card. Links keep working after you beat that round.

## How it works

The server runs one game for everyone. It simulates each drop in full the moment the ball is released, but keeps the result to itself.

Every open page holds one stream from the server (as [server-sent events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)) carrying two kinds of message:

- A public "game" message, identical for every player: the countdown, the price of each side, the seconds until an announced update if there is one (see [Announcing an update](#announcing-an-update)), and while the ball is falling, its next positions. It is sent only as often as needed, because at scale these messages are most of the server's work and bandwidth: twice a second early in a drop, 10 times a second for the last 1.5 seconds before betting closes and until the ball lands, once a second between drops, and straight away when a drop starts or the ball lands.
- A private "me" message with your name, points and shares, and `name_locked`, which is true if the owner has locked your name, sent when you connect, after each round, and when the owner changes your name. Buying a share, setting a name and resetting return the same information.
- A "leaderboard" message with the top 10 named players, sent when you connect and after each round. If you're ranked below the top 10, it also has the two players either side of you, which the page shows under a "…" with your row in yellow. The server reads every named player's id and points from the database once when it starts, then keeps the ranking up to date in memory: it is the only thing that changes anyone's points, so it never needs to read them again.

Ball positions are sent early and the page plays them back on its own clock, which keeps the ball moving smoothly when the network delivers messages unevenly. Early in a drop they are sent up to 48 frames (0.8 seconds) early. For the last 1.5 seconds before betting closes, only 12 frames (a fifth of a second) early. Betting closes that fifth of a second before the ball passes the last row, and while betting is open no page is ever sent a position past the last row, so nobody can bet on a result they have already been sent. Positions are rounded to a tenth of a pixel to keep messages short (`POSITION_DECIMALS` in `app.py`).

The server is a single process on a single thread that switches between pages whenever one is waiting, so each open page costs very little. On a laptop it held 5,000 open pages using about 60% of one processor core.

Your browser remembers a random id that tells the server which player you are. Whoever has that id is that player, so it works like a password and is never shown to anyone else.

If the database can't be reached, the game carries on: everyone can still play, but names can't be claimed and saved points aren't loaded until it is back.

Players without a name live only in the server's memory, and lose their points when it restarts. Once you claim a name, your id, name and points are a row in the Supabase `players` table. The server saves your points there after every round you play in, and loads them back the next time your browser connects.

The database is asked about a player only when it can't already know the answer. The read at startup gives the server the id of every player with a row, and it adds to that list as players claim names. So once that read has worked, a browser id that isn't on the list is a new visitor with nothing saved, and their page starts without the database being asked anything: a big crowd arriving at once costs the database nothing. A player who does have a row is looked up the first time they connect after a restart, for their name, points and lock, and not again until the next restart. Until the startup read has worked (in the first moment after a restart, or while the database is down, when it is tried again every 30 seconds) the server can't tell newcomers from returning players, so it looks up every player as they connect. Claiming a first name also looks the player up once more just before writing the row, in case the row was written moments ago by the previous server, which keeps running for a little while during a deploy; otherwise the new row would overwrite it.

### The best-round card

After each round settles, `new_best_rounds()` in `app.py` works out each buyer's profit from the buys the server itself took, at the frame and price it charged. No route accepts a card, a profit or a trade from a page, so nothing a page sends can make or change one. A round that made more than `CARD_MIN_PROFIT` (1.00) and more than the player's best so far becomes a record, which is sent to that player's open pages as a "card" message (marked fresh) and saved a moment later to the `best_rounds` table by a separate task, like the round log. Pages that connect later are sent the player's card, not fresh.

Each record holds a short random `share_id` (8 characters, the only thing in the link), the player's id and their name at the time, the round's start and settle times, where the ball landed, the player's buys, the left side's price at every frame it changed (cut down to 200 points in a very busy round; the right side's price is always 1 minus it), the cost, payout and profit, and the `start_x`, `gravity`, `bounce` and commit the ball's whole path follows from, for a replay later. Rows are never changed when a better round comes along, so old links keep working, and a player's best is their row with the most profit that they haven't deleted. Deleting a card (`POST /card/clear`, from the card's trash can) sets `cleared` on all the player's rows rather than removing them, so their share links still work. The player's id is never sent to a page or shown on a share page.

The card is drawn by `card.js` (styles in `card.css`), on the game page and on the share page `/r/<share_id>` (`share.html`, filled in by `share_page()`). Its preview tags, and the share menu's "copy image", use `/r/<share_id>.png`, a 1200 by 630 picture of the card drawn by `card_image.py` with Pillow, in a thread so the game doesn't wait, and kept in memory once drawn. The address on the picture is `SITE` in `app.py`, not the address a request came in at, as one picture is kept for everyone. A card not saved yet is still found, as share pages look at the unsaved ones first.

The replay (`replay.js`) gets the ball's path from `/r/<share_id>/replay`. The path isn't stored: the server plays the drop again from the record's `start_x`, `gravity` and `bounce` (a few hundredths of a second) and keeps the last 100 in memory. If the physics in `app.py` has changed since, so the drop no longer lasts as long or lands on the same side, that answers 404 and the page shows the card without a replay. The board is drawn by `board.js`, which the game page uses too.

Like the players table, the server reads who has a card once at startup (`load_card_holders()`), so a new visitor's page starts without asking the database, and looks up a returning player's best the first time they connect. If the database is down, cards are still made and shown, and wait in memory (up to 1,000) to be saved.

## Moderating names

The word filter can't catch everything. If a bad name reaches the leaderboard, the owner can replace it straight away, with no restart: the server gives the player a neutral name and locks it, so they can't change it back. Their points stay with them. The lock is the `name_locked` column of the `players` table, so it lasts through restarts.

This needs `ADMIN_KEY` set on the server (see [Requirements](#requirements)). Each request sends it as `Authorization: Bearer <key>`. A missing or wrong key gets `401`, and if `ADMIN_KEY` isn't set at all, the routes answer `404 Not Found`, as if they weren't there. In a terminal, first:

```
export ADMIN_KEY=the-key-you-set-on-render
export SITE=https://bouncemarket.minhanhday.com
```

Every named player with their points and whether their name is locked, most points first, so you can look beyond the top 10:

```
curl -s "$SITE/admin/names" -H "Authorization: Bearer $ADMIN_KEY"
```

It answers `{"players": [{"name": "Sam", "points": 42.5, "name_locked": false}, ...], "count": 1, "lock_column": true}`. `lock_column` says whether the database has the `name_locked` column yet (`null` if there are no players to tell from).

Replace a name with one the server makes up, such as `player-4821`, and lock it. The name is matched ignoring capitals, and the player doesn't need to be online:

```
curl -s -X POST "$SITE/admin/rename" -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" -d '{"name": "TheBadName"}'
```

Or choose the new name yourself with `"to"`. It must follow the same rules as any username:

```
curl -s -X POST "$SITE/admin/rename" -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" -d '{"name": "TheBadName", "to": "renamed1"}'
```

It answers `{"renamed": "TheBadName", "to": "player-4821", "points": 12.5, "name_locked": true, "cards": "renamed"}`. The new name is written to the database first; only if that works does the server change the leaderboard for everyone, and the player's own pages, at once. If the database write fails, nothing changes and the answer says why.

A rename also puts the new name on all the player's best-round cards, old ones included, so the bad name doesn't live on in share pages. `"cards"` says whether that worked; if it didn't, the rename itself still has, and renaming again retries it. Link previews that chat apps have already fetched may keep the old picture for up to a day.

Lift a lock, so the player can choose a name again:

```
curl -s -X POST "$SITE/admin/unlock" -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" -d '{"name": "player-4821"}'
```

The errors are `400` for a bad request or a `"to"` that breaks the username rules, `404` if nobody has the name, `409` if `"to"` is taken, and `503` if the database can't be reached or the server is still reading the leaderboard after a restart (try again in a minute).

The `name_locked` column must exist before these work: run `schema.sql` again (it only adds what is missing). If the new code runs before the column exists, the game itself is unaffected, as the server reads each player's row without naming the column and treats a missing one as "not locked". Only renaming and unlocking fail, with an answer saying to run `schema.sql`.

Avoid moderating during a deploy: for a minute or so the previous server is still running, and it may save a round's points under the old name, undoing the change. If that happens, rename again once the deploy has finished.

## Announcing an update

A deploy restarts the server, and guests (players without a username) lose their points. To warn them first, put up a banner on every open page that counts down to the update. With `ADMIN_KEY` and `SITE` set as above, for an update in 15 minutes:

```
curl -s -X POST "$SITE/admin/notice" -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" -d '{"minutes": 15}'
```

Every page then shows "System update in 15 minutes, all non-named player scores may be wiped. If you would like to save your points, choose a username before the update.", counting down a minute at a time, with "choose a username" opening the username dialog for guests. When the time is up it says the update is starting now. It answers `{"notice": "published", "minutes": 15, "update_at": "..."}`. `minutes` can be any number from 0 to 1440, fractions included, and sending a new one restarts the countdown. Send `{"minutes": 0}` to take the banner down.

The banner lives only in the server's memory, so the deploy it announces takes it down. Pages that connect while it is up see it within a second.

## The round log

Every round that at least one player bought in is saved to the database, for analysis later: how well the market's odds track the real ones, and how players differ in the way they play. Rounds nobody bought in aren't saved.

Each is one row of the `rounds` table:

| Column | Meaning |
| ------ | ------- |
| `started_at` | When the ball dropped. |
| `commit_id` | The version of the code that played the round (Render's `RENDER_GIT_COMMIT`, or `local`). |
| `gravity`, `bounce` | The physics options the server was running with. |
| `start_x` | Where along the top the ball dropped from. |
| `frames`, `close_frame` | How many frames (60 a second) the ball took to land, and the frame betting closed at. |
| `bucket` | Where the ball landed. |
| `path` | The ball's position at every frame, packed. |
| `buys`, `n_buys` | Every buy, in order, packed, and how many there are. |
| `format` | How `path` and `buys` are packed (`LOG_FORMAT` in `app.py`). |
| `rules` | Which pricing and counterparty rules the round was played by (`RULES_VERSION` in `app.py`). Rounds logged before the column existed are 1: each side's price was its fraction of all shares bought, and the counterparty bought one share at a time on whichever half the ball was in. In 2, prices follow the logistic curve and the counterparty is today's pretend traders, but a share was charged the price before the buy. 3 charges the market maker's cost of the buy instead. `analysis.py` keeps all three, so old rounds still read back right. |

A drop is decided entirely by `start_x`, `gravity` and `bounce`: the same three numbers give exactly the same path on any computer. So the analysis plays each round again to get the ball's exact position and velocity at every frame, and nothing about the ball is stored per buy. The saved `path` (under a kilobyte) is there to check the replay against, and to fall back on if the physics in `app.py` ever changes. If it does, a round still replays under the version in its `commit_id`.

For each buy, the log keeps the server's frame when it took the buy, the frame the player's page said it was showing when the button was pressed (a little earlier, and what the player was actually looking at), who bought, which side, the price paid, and the player's points just before. The page's frame is the page's word, so it's only good for analysis. Everything else about the market at that moment follows from the buys before it and the path: the counterparty's buys are worked out again from the path, where the ball dropped from and the players' buys (its traders' timing has chance in it, but seeded from where the ball dropped, so it comes out the same), and the price from the shares.

The buys are nearly all of what gets stored (a thousand players buying three shares a round is about 8 million buys in ten hours), so they are packed small: about 7 bytes each, against about 80 as database rows of their own. The price of that is that the database can't search inside them. `pack_buys()` in `app.py` describes the layout. In place of each player's long id, a buy holds a small number, the player's key, from the `player_keys` table. Every player gets one the first time they buy, named or not.

Rounds are saved a second or so after they finish, by a separate task, so a slow database never holds up the game. If the database can't be reached, up to 20 rounds wait in memory and are saved when it is back.

### Reading it back

```
uv run --env-file .env analysis.py buys.csv
```

writes one line per buy ever made: the round, the player (their key, and their name if they have one), the frame, the side, the price, their points, the ball's position and velocity at the server's frame and at the frame the page was showing, the shares players and the counterparty had bought on each side just before, where the ball landed, and what the share made. End the file name in `.gz` to have it compressed. The top of `analysis.py` lists the columns.

It also says if any rounds didn't replay to the path that was saved, or if any buys weren't at the price today's `app.py` would charge. Either means the rules have changed since those rounds were played.

To work with whole rounds in Python instead, `analysis.rounds()` gives each one with its path, its exact state at every frame, and its buys.

## Tuning the physics

Gravity and bounce can be passed on the command line:

```
uv run app.py --gravity 1500 --bounce 0.3
```

| Option      | Default | Meaning                                                        |
| ----------- | ------- | -------------------------------------------------------------- |
| `--gravity` | 400     | Downward acceleration, in pixels / second². Higher is faster.  |
| `--bounce`  | 0.85    | Fraction of speed kept after hitting a pin or wall, 0 to 1. Lower makes the ball slow down more in the dense rows. |

Everything else (board size, pin rows, ball and pin size) is a constant at the top of `app.py`.

The round log saves the gravity and bounce each round was played with, so rounds played under different options replay correctly. A change to one of the constants is only recorded as a new `commit_id`.
