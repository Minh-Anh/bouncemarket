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

An update doesn't stop the game: the old server hands it to the new one, ball in the air and all, and the open pages follow. See [Updating without stopping the game](#updating-without-stopping-the-game).

## How to play

Everyone who opens the page watches the same ball and trades in the same market. A new ball drops 5 seconds after the last one lands.

You start with 10 points. Each "buy 1 share" button buys one share on that side at the price shown. A share pays 1 point if the ball lands on its side and nothing if it doesn't. So a share bought at 0.30 makes 0.70 if it wins and loses 0.30 if it doesn't. While you hold shares, each side's card shows what you would net if the ball lands on that side: your shares on that side minus everything you paid this round.

You can buy only while the ball is falling: from the moment it drops until just before it passes the last row of pins. Shares can't be sold back; you hold them until the ball lands.

Both sides start at 0.50 each round, and the two prices always add up to 1. A side's price depends only on how many more shares have been bought on it than on the other side this round. A buy on Left and a buy on Right cancel out, as in a real market, where buying one side is the same as someone selling it to you. So a share moves the price by the same amount at the end of a round as at the start, and a few traders piling in late can swing it hard. A side 7 shares ahead costs 0.73 and 14 ahead costs 0.88, along a logistic curve, the one prediction-market makers use (`PRICE_SOFTNESS` in `app.py`; higher makes each share move the price less). Every buy pushes that side's price up and the other side's down. Being right early, while a side is still cheap, pays more than piling in once everyone agrees.

The price on each side's card is what a share costs you, which is a little more than the market's chance for that side, shown in the odds bar. Your buy moves the price, so you pay the average price along that move, not the price before it: the cost a logarithmic market maker charges (`costs_for_net()` in `app.py`), rounded to the nearest cent. At 0.50 a side, a share costs 0.52. Charging the price before the move gave away about half a price step on every share, and let a script buy Left at 0.50 and then Right at 0.46, for a pair sure to pay 1.00. At the market maker's cost, equal shares on both sides always cost exactly what they are sure to pay, so there is nothing to gain from buying both sides, and the house takes no cut.

A share costs at least 0.01 and at most 1.00. A side 37 shares ahead costs 1.00, which is all it can pay back, so once a ball is plainly going one way there is nothing left to make by buying it. Until rules version 4 the most was 0.99, and every share past that point still cost 0.99: a few players bought thousands a round of the side that was all but sure, winning a cent nearly every time (see [After the update](#after-the-update-what-the-timing-log-shows)). Now their own buys carry the price to 1.00 within a few shares. The least stays at 0.01, since a free share would be worth buying however unlikely its side. Beyond 37 ahead, that puts a Left share and a Right share a cent over the 1 point the pair pays.

Each player can buy at most 10 shares in any one second (`MAX_BUYS_PER_SECOND`). A person clicks 5 to 8.5 times a second; a buy past the limit is turned down.

There is also a counterparty, so the market moves even with nobody else on: five pretend traders who buy the way a person might. Each guesses where the ball will land with a naive rule: the further it is from the middle and the lower it is, the surer they get. Each looks at the ball and the price; if the price is off from their guess by 0.03 or more, they buy one share of the side that looks too cheap and look again a moment later, at a person's clicking speed (5 to 8.5 clicks a second), and otherwise they look again after a short pause. That makes bursts of buying, sometimes two traders in the same instant. Their buying drifts the price towards where the ball seems to be heading, hardest near the bottom, and they push back when players pile onto the side the ball isn't on.

The pretend traders make way for real people. Each different player who buys in a round takes one trader's place for the rest of that round, so once five people have bought, the pretend traders have stopped and the market is all human. Players are counted by their buys, not by who has the page open, so a crowd that's only watching still gets a moving market.

The counterparty is also the seller of every share you buy: it keeps what you pay and pays out the winners.

Anyone can play straight away, and is ranked on the leaderboard from the start: until you claim a name you appear as "you", where your points would put you, with a "set username" button. Other people's guests aren't shown, only named players and you. Names are unique, ignoring capitals, and from then on your points are saved. The pencil button after your name on the leaderboard changes it to any name nobody else is using, and your points stay with you.

A username is 1 to 16 plain letters (A to Z, either case), numbers, `-` or `_`, with at least one letter or number. The page drops any other character as you type. Some names are reserved because they would look like the site itself talking (`you`, `guest`, `admin`, `moderator` and the like), and a word filter turns down slurs, obscenities and hate symbols, including the usual disguises such as numbers for letters. The rules are in `names.py`. They are checked only when a name is set or changed, so a name saved before a rule existed stays as it is, unless the owner changes it (see [Moderating names](#moderating-names)). If the owner has changed your name, it is locked and you can't change it yourself.

Anyone can start over with 10 points, whatever they have now, named or not. The circling-arrows button on your own leaderboard row does it, asking first only if you have more than 10 points to lose. If you can't afford a share on either side and hold none, a "reset to 10" button also appears under the buy buttons. A reset can't be used while you hold shares in the round being played, or someone could spend everything pushing a price around, start over, and do it again for free. Nor more than once every 10 minutes, or more than 6 times an hour from one internet address (see [Limits on resets and buys](#limits-on-resets-and-buys)). A guest's reset changes nothing in the database and nothing on anyone else's leaderboard, only their own.

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

- A public "game" message, identical for every player: the countdown, the price of each side, the seconds until an announced update if there is one (see [Announcing an update](#announcing-an-update)), and while the ball is falling, its next positions. It is sent only as often as needed, because at scale these messages are most of the server's work and bandwidth: twice a second early in a drop, 10 times a second for the last 1.5 seconds before betting closes and until the ball lands, once a second between drops, and straight away when a drop starts or the ball lands. Each time, the pages are sent it in a new random order. With a thousand pages open the last is written a noticeable moment after the first, and in a fixed order (the order pages connected) the same page would always hear first. A page is also sent one of its own when it connects, with the ball's positions from where it is now, so a page opened during a drop shows the ball at once.
- A private "me" message with your name, points and shares, and `name_locked`, which is true if the owner has locked your name, sent when you connect, after each round, and when the owner changes your name. Buying a share, setting a name and resetting return the same information.
- A "leaderboard" message with the top 10 named players, sent when you connect and after each round. If you're ranked below the top 10, it also has the two players either side of you, which the page shows under a "…" with your row in yellow. The server reads every named player's id and points from the database once when it starts, then keeps the ranking up to date in memory: it is the only thing that changes anyone's points, so it never needs to read them again.

Ball positions are sent early and the page plays them back on its own clock, which keeps the ball moving smoothly when the network delivers messages unevenly. Early in a drop they are sent up to 48 frames (0.8 seconds) early. For the last 1.5 seconds before betting closes, only 12 frames (a fifth of a second) early. Betting closes that fifth of a second before the ball passes the last row, and while betting is open no page is ever sent a position past the last row, so nobody can bet on a result they have already been sent. Positions are rounded to a tenth of a pixel to keep messages short (`POSITION_DECIMALS` in `app.py`).

The server is a single process on a single thread that switches between pages whenever one is waiting, so each open page costs very little. On a laptop it held 5,000 open pages using about 60% of one processor core.

Your browser remembers a random id that tells the server which player you are. Whoever has that id is that player, so it works like a password and is never shown to anyone else.

If the database can't be reached, the game carries on: everyone can still play, but names can't be claimed and saved points aren't loaded until it is back.

Players without a name live only in the server's memory. An update carries them over to the new server (see [Updating without stopping the game](#updating-without-stopping-the-game)); they lose their points only if the server stops with no new one to hand them to, such as after a crash, or if an update comes more than a day after they were last here and they hold no shares (`GUEST_KEPT_SECONDS` in `app.py`), so that old guests don't pile up for ever. Once you claim a name, your id, name and points are a row in the Supabase `players` table. The server saves your points there after every round you play in, and loads them back the next time your browser connects.

The database is asked about a player only when it can't already know the answer. The read at startup gives the server the id of every player with a row, and it adds to that list as players claim names. So once that read has worked, a browser id that isn't on the list is a new visitor with nothing saved, and their page starts without the database being asked anything: a big crowd arriving at once costs the database nothing. A player who does have a row is looked up the first time they connect after a restart, for their name, points and lock, and not again until the next restart. Until the startup read has worked (in the first moment after a restart, or while the database is down, when it is tried again every 30 seconds) the server can't tell newcomers from returning players, so it looks up every player as they connect. Claiming a first name also looks the player up once more just before writing the row, in case the row was written moments ago by the previous server, which keeps running for a little while during a deploy; otherwise the new row would overwrite it.

### The hidden nudges

At every pin bounce the ball's direction is turned by a tiny random angle, typically 0.17 degrees either way (`NUDGE` in `app.py`, 0.003 radians), far too small to see. The angles come from a seed the server draws for each drop from the operating system's randomness, and the seed is kept secret until the ball has landed: no page is ever sent it while betting is open. Once the round is over it is saved with the round and on any card made from it, so the drop can still be replayed exactly.

Without the nudges, the whole drop followed from where the ball started. Players worked that out from the path they were sent, as Precogbot had tried across rounds (see [An attempt on the random numbers](#an-attempt-on-the-random-numbers)), only now within one round. As `start_x` is rounded to 15 significant digits, only so many starting points are possible, and a few hundred frames of path leave one: in 200 rounds on 27–28 November 2026 that took a median of 264 frames, with half the betting window still to go. From there a script knew the winner for certain. In about half of the rounds, the price had settled at 0.90 or more on the winner within a frame or so of the stream delivering the frame that settled it, a median of 34 frames before the server's own frame got there, and never once after. Several players (socramt, prrrrrrrrrrrrr, le-botty, garethgeorge, cubebot-v3, foob) won every share they bought in that gap, and they read the stream up to the newest frame sent, 48 frames ahead. Sending whole pixels instead of tenths would only have put the solve 12 frames or so later.

The path is chaotic, so a turn this small soon makes the rest of the drop impossible to work out from what has been seen of it. In 60 drops made with the nudges, even a script that knew the ball's exact position and velocity at every moment (more than the stream gives) could be 95% sure of the winner only a median of 46 frames before betting closed, against the whole drop without them. By then the stream is only 12 frames ahead, so 17% of the betting window is left once it carries the frame that decides it, against half before. In a first test, with the turns drawn a little differently, bigger turns gained little more (1.7 degrees gave 38 frames) and smaller ones gave back a lot (0.00006 degrees gave 132).

The turn is made with the same + − × ÷ and square roots as the rest of `simulate_drop()`, and drawn from a generator whose numbers Python promises not to change, so a replay comes out the same on any computer. Rounds played before the nudges have no seed, and replay without them, as they were played. A round handed over during an update by a server from before them carries on without them too.

### The best-round card

After each round settles, `new_best_rounds()` in `app.py` works out each buyer's profit from the buys the server itself took, at the frame and price it charged. No route accepts a card, a profit or a trade from a page, so nothing a page sends can make or change one. A round that made more than `CARD_MIN_PROFIT` (1.00) and more than the player's best so far becomes a record, which is sent to that player's open pages as a "card" message (marked fresh) and saved a moment later to the `best_rounds` table by a separate task, like the round log. Pages that connect later are sent the player's card, not fresh.

Each record holds a short random `share_id` (8 characters, the only thing in the link), the player's id and their name at the time, the round's start and settle times, where the ball landed, the player's buys, the left side's price at every frame it changed (cut down to 200 points in a very busy round; the right side's price is always 1 minus it), the cost, payout and profit, and the `start_x`, `gravity`, `bounce`, `nudge`, `nudge_seed` and commit the ball's whole path follows from, for a replay later. Rows are never changed when a better round comes along, so old links keep working, and a player's best is their row with the most profit that they haven't deleted. Deleting a card (`POST /card/clear`, from the card's trash can) sets `cleared` on all the player's rows rather than removing them, so their share links still work. The player's id is never sent to a page or shown on a share page.

The card is drawn by `card.js` (styles in `card.css`), on the game page and on the share page `/r/<share_id>` (`share.html`, filled in by `share_page()`). Its preview tags, and the share menu's "copy image", use `/r/<share_id>.png`, a 1200 by 630 picture of the card drawn by `card_image.py` with Pillow, in a thread so the game doesn't wait, and kept in memory once drawn. The address on the picture is `SITE` in `app.py`, not the address a request came in at, as one picture is kept for everyone. A card not saved yet is still found, as share pages look at the unsaved ones first.

The replay (`replay.js`) gets the ball's path from `/r/<share_id>/replay`. The path isn't stored: the server plays the drop again from the record's `start_x`, `gravity`, `bounce`, `nudge` and `nudge_seed` (a few hundredths of a second) and keeps the last 100 in memory. If the physics in `app.py` has changed since, so the drop no longer lasts as long or lands on the same side, that answers 404 and the page shows the card without a replay. The board is drawn by `board.js`, which the game page uses too.

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

During a deploy, a command can reach the old server just as it hands the game over. It then answers `503` with `"moved": true` and has changed nothing: run the command again. If a handover fails (see [Updating without stopping the game](#updating-without-stopping-the-game)), the previous server keeps running for a minute or so and may save a round's points under the old name, undoing a rename made meanwhile. If that happens, rename again once the deploy has finished.

## Limits on open pages

Every open page holds a stream, and every broadcast is written to every stream, so one computer opening thousands of streams slows the game for everyone. Two limits stop that, both settable as environment variables on the host:

| Variable | Default | |
| --- | --- | --- |
| `MAX_STREAMS_PER_ADDRESS` | 20 | The most pages open from one internet address. A household, an office or a classroom shares one address, so it isn't small. A page past the limit gets `429`. `0` turns it off. |
| `MAX_STREAMS` | 1500 | The most pages open at once. Past it a new page gets `503`, and the players already in keep a quick game. Set it to what the server can carry. |

A page that is turned away says so, and asks again by itself every 10 to 20 seconds until there is room.

The server never sees a visitor's address directly, as every connection reaches it from Render's proxy. It reads the address from the `CF-Connecting-IP` header, which Cloudflare (in front of every Render service) sets and a visitor can't forge, and failing that from the first address in `X-Forwarded-For`, which a visitor can forge. An IPv6 address counts as its network (the first half, a /64). With neither header, as on your own computer, pages aren't counted against an address, only against `MAX_STREAMS`.

Once a minute, if anyone was turned away or one address holds many pages, the server says so in the log:

`1203 pages open from 961 addresses (the limits are 1500, and 20 from one address). Most from one address: 203.0.113.7 has 20. Turned away 2950 connections from 3 addresses in the last 60 seconds: 2948 from 203.0.113.7, ...`

A few addresses turned away thousands of times is one person's script. If the line says many pages "came with no address", the headers aren't what the server expects and the limit per address isn't working. The limits are per address, so they don't stop a flood from thousands of different addresses: that needs a filter in front of the server, such as Cloudflare's rate limiting.

## Limits on resets and buys

Every player starts with 10 points, and can reset to 10 whenever they hold no shares. In 1,200 rounds on 28 November 2026, 24 guests spent their 10 points every round, each on 10 shares bought at once, early and on either side, paying 0.75 to 0.85 for shares that won less than half the time, and reset to 10 between rounds. Together they lost 26.5 points a round, about six times what the market maker gives away. Three players made all their profit taking the other side of those bursts, within a few hundredths of a second: before any broadcast could have shown them the burst, and almost never after anyone else's of the same size. Whether one person ran both sides, or the three watched the price some faster way and the guests were someone's careless script, free points were being turned into real ones.

Four limits make that slow and costly, all settable as environment variables on the host, and `0` turns each off:

| Variable | Default | |
| --- | --- | --- |
| `MAX_BUYS_PER_ADDRESS` | 20 | The most buys in any one second from all the players at one internet address together, on top of each player's own 10 (`MAX_BUYS_PER_SECOND`). Past it a buy gets `429`. A household, an office or a classroom shares one address; 20 is two people clicking as fast as anyone can. |
| `RESET_COOLDOWN_SECONDS` | 600 | How long a player waits after a reset before they can reset again. A refused reset says how many minutes are left. |
| `MAX_RESETS_PER_ADDRESS` | 6 | The most resets in any hour from all the players at one internet address together. |
| `MAX_NEW_GUESTS_PER_ADDRESS` | 20 | The most new guests in any hour from one internet address. A new browser is a new guest with 10 points, so without it one person could make as many as they liked. A page past the limit gets `429`, says so, and keeps trying, as one past the [limit on open pages](#limits-on-open-pages) does. Players the server already knows, and players with a saved name, aren't counted. |

The address is worked out as for the [limits on open pages](#limits-on-open-pages), and with none to go by (as on your own computer) only the limits for each player apply. A player's last reset goes with them when the game moves to a new server; the counts for each address start afresh.

Someone with many internet addresses can still get round all four, but each address then brings only so many free points an hour.

## Announcing an update

An update normally carries everyone's points over to the new server (see [Updating without stopping the game](#updating-without-stopping-the-game)). It can't when the old server doesn't know how to hand over, or the new one can't read what it is handed: the first deploy of the handover code itself, and any deploy that changes `HANDOVER_FORMAT`. Then guests (players without a username) lose their points, as they do if a handover fails. To warn them first, put up a banner on every open page that counts down to the update. With `ADMIN_KEY` and `SITE` set as above, for an update in 15 minutes:

```
curl -s -X POST "$SITE/admin/notice" -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" -d '{"minutes": 15}'
```

Every page then shows "System update in 15 minutes, all non-named player scores may be wiped. If you would like to save your points, choose a username before the update.", counting down a minute at a time, with "choose a username" opening the username dialog for guests. When the time is up it says the update is starting now. It answers `{"notice": "published", "minutes": 15, "update_at": "..."}`. `minutes` can be any number from 0 to 1440, fractions included, and sending a new one restarts the countdown. Send `{"minutes": 0}` to take the banner down.

The banner lives only in the server's memory and isn't handed over, so the deploy it announces takes it down. Pages that connect while it is up see it straight away.

## Updating without stopping the game

Render updates a service blue-green: it starts a new server beside the old one, sends every new request to the new one once it is up, and stops the old one a minute later. Open streams stay with the old server for that minute. The game lives in one server's memory, so on its own this would split it: a page would watch the ball on the old server while its buys went to the new one, which has never heard of the player, and a minute later every page would land in a different game, guests back on 10 points.

So the old server hands the game over at the moment of the switch:

1. The new server, before it takes any requests, says hello at the site's own address. No request reaches it yet, so the old server answers. The new one now waits for the game without starting one of its own, and the old one starts watching.
2. Twice a second, the old server asks the site's address "which server are you?" (`/health`). The first time the new server answers, Render has switched over.
3. The old server stops changing anything and sends the new one everything that lives only in memory: the round being played (the ball's path, where it will land, every buy), each player's points and shares, the leaderboard, and any round or card not saved yet.
4. The new server carries on from the same frame. Requests that reached it before then were kept waiting, and are answered now.
5. The old server sends each page the next second and a half of the ball's path, then ends its stream. The browser connects again by itself, between 0.05 and 0.5 seconds later, each page after a different wait so they don't all arrive at once, and reaches the new server.

Rehearsed on one computer with 20 pages, every page was back on a stream within 0.7 seconds of the switch, and none missed a frame of the ball's path. A player sees nothing: the ball keeps falling, their shares and points are as they were, and a buy made in that moment waits a fraction of a second longer for its answer. The round is paid out and logged by the new server, once, under the old server's `commit_id`, as that is the code that dropped the ball. Only a handover in the last second or so before trading closes can show. A handover never shows a page more of the ball's path than it could have seen without one, and near the close that is only 0.2 seconds ahead, so the ball may stand still for a moment, up to about half a second, until the page has reconnected.

It needs nothing set up on Render. The servers find each other through `RENDER_EXTERNAL_URL`, which Render sets, and prove who they are with a key worked out from `SUPABASE_KEY`, which both have. `render.yaml` sets `healthCheckPath: /health`; if the service was made before that line was there, set the health check path to `/health` in the Render dashboard too. Anywhere else, set `PUBLIC_URL` to the address the site is reached at. Without either there are no handovers, which is how it runs on your own computer. There must only ever be one instance of the service, as before: the game is one server's memory.

Three things to know:

- **The first deploy of this code restarts the game as before.** The old server is the one that hands over, and the one running now doesn't know how. Every deploy after that hands over. [Announce](#announcing-an-update) this one if guests should be warned.
- **A change of rules calls off the round in the air.** If the new code has a different `RULES_VERSION`, the round can't change rules half-way, so everyone's shares in it are refunded and a new countdown starts. Everything else carries over.
- **A change to what is handed over** in a way the new code can't read means changing `HANDOVER_FORMAT` in `app.py`. That one deploy then restarts the game as before.

If a step fails, the game is never left without a server. The old server carries on if it can't deliver the game, tries twice more, and tries a last time when Render tells it to stop. A new server that has waited 5 seconds with requests held and been handed nothing starts a game of its own, and a handover that arrives later is merged into it: the old round is refunded and the players' points follow them. The worst case is what an update used to be.

The servers say what happened in Render's logs. The old one: `A new server has started. Watching for it to take over.` and then `Handed the game to the new server, <name>, with 12 pages open.` The new one: `Waiting to be handed the game by the old server, <name>.` and then `Took over the game from the old server, <name>: 14 players, carrying on its round.`

The code is under "The handover" in `app.py`.

## The round log

Every round that at least one player bought in is saved to the database, for analysis later: how well the market's odds track the real ones, and how players differ in the way they play. Rounds nobody bought in aren't saved.

Each is one row of the `rounds` table:

| Column | Meaning |
| ------ | ------- |
| `started_at` | When the ball dropped. |
| `commit_id` | The version of the code that dropped the ball (Render's `RENDER_GIT_COMMIT`, or `local`). A round handed over during an update keeps the old server's. |
| `gravity`, `bounce` | The physics options the ball was dropped with. |
| `start_x` | Where along the top the ball dropped from, to 15 significant digits. |
| `nudge`, `nudge_seed` | How big the ball's hidden turns at each pin bounce typically were, in radians, and the seed they were drawn from (see [The hidden nudges](#the-hidden-nudges)). Empty for rounds played before them, which had none. Run `schema.sql` to add these columns: until then the server saves rounds without them, and they can't be replayed exactly. |
| `frames`, `close_frame` | How many frames (60 a second) the ball took to land, and the frame betting closed at. |
| `bucket` | Where the ball landed. |
| `path` | The ball's position at every frame, packed. |
| `buys`, `n_buys` | Every buy, in order, packed, and how many there are. |
| `format` | How `path` and `buys` are packed (`LOG_FORMAT` in `app.py`). |
| `rules` | Which pricing and counterparty rules the round was played by (`RULES_VERSION` in `app.py`). Rounds logged before the column existed are 1: each side's price was its fraction of all shares bought, and the counterparty bought one share at a time on whichever half the ball was in. In 2, prices follow the logistic curve and the counterparty is today's pretend traders, but a share was charged the price before the buy. 3 charges the market maker's cost of the buy instead. 4 lets that cost reach 1.00, where 3 stopped at 0.99. `analysis.py` keeps all four, so old rounds still read back right. |
| `timing` | When each broadcast went out and each buy was taken, to the microsecond, packed (see [Timing](#timing)). Empty for rounds logged before the column existed. |

A drop is decided entirely by `start_x`, `gravity`, `bounce`, `nudge` and `nudge_seed` (see [The hidden nudges](#the-hidden-nudges)): the same five numbers give exactly the same path on any computer. Rounds played before the nudges have none, and replay without them. So the analysis plays each round again to get the ball's exact position and velocity at every frame, and nothing about the ball is stored per buy. The saved `path` (under a kilobyte) is there to check the replay against, and to fall back on if the physics in `app.py` ever changes. If it does, a round still replays under the version in its `commit_id`.

The database gives `start_x` back to 15 significant digits, where a number like it needs 17 to come back exactly, and the path is chaotic: a replay from 15 digits parts from the real drop a few hundred frames in. So the server rounds `start_x` to 15 digits before it drops the ball, and the drop that is played is the one that is saved. Rounds logged before that was done mostly don't replay to their saved path; `analysis.py` says how many, and falls back on the saved path for their positions. Their counterparty buys, worked out again from `start_x`, can be off by a share or two as well.

For each buy, the log keeps the server's frame when it took the buy, the frame the player's page said it was showing when the button was pressed (a little earlier, and what the player was actually looking at), who bought, which side, the price paid, and the player's points just before. The page's frame is the page's word, so it's only good for analysis. Everything else about the market at that moment follows from the buys before it and the path: the counterparty's buys are worked out again from the path, where the ball dropped from and the players' buys (its traders' timing has chance in it, but seeded from where the ball dropped, so it comes out the same), and the price from the shares.

The buys are nearly all of what gets stored (a thousand players buying three shares a round is about 8 million buys in ten hours), so they are packed small: about 7 bytes each, against about 80 as database rows of their own. The price of that is that the database can't search inside them. `pack_buys()` in `app.py` describes the layout. In place of each player's long id, a buy holds a small number, the player's key, from the `player_keys` table. Every player gets one the first time they buy, named or not.

Rounds are saved a second or so after they finish, by a separate task, so a slow database never holds up the game. If the database can't be reached, up to 20 rounds wait in memory and are saved when it is back.

### Timing

Frames are a sixtieth of a second, too coarse to tell who answers fastest, so each round also keeps its timing, in microseconds since the drop by the server's clock (`pack_timing()` in `app.py`):

- For each broadcast during the drop: when it was made, when it was written to the first page and to the last, and how many pages it was queued for and written to. "Written" is when the server handed it to the network for that page, which comes in the broadcast's random order.
- For each buy, in the same order as the buys: when the server took it, and the newest broadcast of the round that had been written to the buyer's pages by then, and when.

The time from that write to the buy is how long the player took to answer what they had just been sent: their network, there and back, plus however long their own page or program took. It no longer includes where their page came in the order of sending, which is measured separately. A player whose answer is always fast at the start of a drop (when there is nothing to work out but "it has started") has a fast network; one who is also fast once there is a price to weigh has fast code too. The server can't see further into a player's network than that, as Render's proxy stands between them.

The `timing` column must exist before it is saved: run `schema.sql` again before pushing. If the new code runs first, rounds are saved without their timing, the server says so in its log, and it tries again with timing after its next restart.

### Reading it back

```
uv run --env-file .env analysis.py buys.csv
```

writes one line per buy ever made: the round, the player (their key, and their name if they have one), the frame, the side, the price, their points, the ball's position and velocity at the server's frame and at the frame the page was showing, the shares players and the counterparty had bought on each side just before, where the ball landed, what the share made, and from the round's timing, when the buy was taken to the microsecond, how long the player took to answer the newest broadcast they had been sent, and how long that broadcast took to reach their page in its sending order. End the file name in `.gz` to have it compressed. The top of `analysis.py` lists the columns.

It also says if any rounds didn't replay to the path that was saved, or if any buys weren't at the price today's `app.py` would charge. Either means the rules have changed since those rounds were played, or they were played before `start_x` was rounded (see above).

To work with whole rounds in Python instead, `analysis.rounds()` gives each one with its path, its exact state at every frame, and its buys.

## What players are doing

A study of the round log on 27 November 2026 (the scripts are in [`analyses/`](analyses/README.md)), of rounds played under `e09f030` (rules version 3), before the timing column, the shuffled send order and the rounding of `start_x`. The market maker figures are from 273 rounds between 17:28 and 18:28 UTC; the rest from 600 rounds between 18:40 and 20:54 UTC, with about 1,250 players and 1,000 to 1,700 buys a round. Those rounds were played before `start_x` was rounded, so only about one in ten replays exactly: the study used the saved `path` for the ball, and the counterparty worked out again from `start_x` is off by a share in about a third of rounds, which barely moves any figure here.

### The market maker loses a fixed amount each round

It lost 1,315 points in the hour, 4.82 a round, and lost 270 of the 273 rounds. Nearly all of that is built into the pricing. A logarithmic market maker's loss over a round depends only on how far ahead the winning side is at the close: nothing if the two are level, and at most `PRICE_SOFTNESS` × ln 2 = 4.85 points once the price is sure of the winner, which it nearly always is by the close. Each round splits exactly into three parts:

| Part | Points in the hour | A round |
| ---- | ------------------ | ------- |
| The pricing's built-in loss | −1,267 | −4.64 |
| Rounding to the cent, and the 0.01 and 0.99 limits | −43 | −0.16 |
| The counterparty's shares (it neither pays for them nor collects on them) | −5 | −0.02 |

So the house gives away about 4.8 points every round anyone trades: about 1,300 points an hour into players' balances at this pace, 0.7% of the points that change hands. It is left that way on purpose. A fee of a cent a share would turn it round, and a lower `PRICE_SOFTNESS` shrinks it in proportion, at the cost of each share moving the price more.

### The crowd misprices the round, in a way that can be predicted

Early in a drop the crowd piles onto whichever side the ball is on, though high up that says little: the board is chaotic. Late in a drop the price is slow to believe what is plain. The favourite's price, sampled every 6 frames, against how often the favourite won:

| Price | First third of the betting window | Middle third | Last third |
| ----- | --------------------------------- | ------------ | ---------- |
| about 0.74 | won 0.60 | 0.70 | 0.81 |
| about 0.84 | 0.59 | 0.80 | 0.94 |
| 0.95 to 0.99 | 0.76 | 0.98 | 1.00 |

Taken over all buys, shares bought at 0.10 to 0.40 made 0.09 to 0.13 each and shares bought at 0.60 to 0.90 lost about as much.

### The kinds of trader

**A speed trader (foob).** By far the biggest winner: +3,049 points over the 600 rounds, 5.08 a round, about the whole of what the market maker gives away. foob buys the underdog early (+1,029) and in the middle (+888), and the near-certain favourite late, in volume at small margins (+336 from 11,493 buys). It buys both sides in 96% of rounds, but isn't hedging: its worst case averages −22 a round. It makes money at every price, and the price of its side rises about 0.11 in the second after it buys. It is almost certainly a script: up to 10 buys in one frame (exactly `MAX_BUYS_PER_SECOND`, all at once), and about 50 a round.

Its edge is a race. Each buy was valued again as if it had come d frames later, at the price the market had reached by then, leaving out foob's own later buys:

| Delay | 0 | 1 frame (17 ms) | 2 | 3 (50 ms) | 6 (100 ms) | 12 (200 ms) | 30 (0.5 s) | 60 (1 s) |
| ----- | - | --------------- | - | --------- | ---------- | ----------- | ---------- | -------- |
| Profit a share | +0.120 | +0.104 | +0.079 | +0.061 | +0.042 | +0.018 | +0.066 | +0.049 |

Half of it is gone in 50 ms and 85% in 200 ms. Some comes back after half a second, as the crowd that rushed in behind overshoots and the price falls back: that slower part is the mispricing above.

foob is also first to the server. At the start of a drop, when every page hears of it from the same broadcast, foob's first buy arrived before any other player's in 63% of rounds, typically at frame 5 and never at frame 0 (so it answers the broadcast; it doesn't time its request from the countdown), 33 to 100 ms ahead of the next fastest (switcharoo-alpha, kitsu_test, clawde). In a tenth of rounds its whole loop, from the broadcast to its buy arriving, took about 50 ms or less. Without the timing log, its network, its code and its place in the old fixed send order (the order pages connected) can't be told apart; the timing log and the shuffled order were added to settle that (see [Timing](#timing)).

**A pack that follows within 200 ms.** In the 200 ms after one of foob's bursts, other players bought 137,615 shares on its side and 86,635 on the other. The fastest followers nearly always take its side: doober (84%), goober (85%), hydrogen (88%), prrrrrrrrrrrrr (91%), clawde (77%), martin (92%), Zone (100%), tokenmaxer_bot (98%), Precogbot (96%). For doober, hydrogen and prrrrrrrrrrrrr these are 40 to 50% of all they buy. The log can't tell whether they react to the same signal a moment later or chase foob's move on the price.

**Slow-edge traders (martin, tokenmaxer_bot).** martin made +1,334 points (98.7% of shares won, at an average price of 0.92), tokenmaxer_bot +337. Their edge doesn't decay with delay; it grows (martin +0.081 a share at once, +0.112 a second later), as they tend to buy just after foob's bursts, at the top of the move, and then the price falls back. They win by being right, not by being fast.

**Traders reading the buffered frames, a little.** Early in a drop the stream carries positions 48 frames (0.8 s) ahead of the server's frame, and a normal page runs about 12 behind it, so a script reading the stream itself sees the ball's near future. To look for that, a model of the chance of landing left from the ball's position and sideways speed (a grid, learned on half the rounds and tested on the other half) gave the change in chance for the side bought over three steps: from what a page shows to the server's frame, from there to the newest frame sent, and, as a control, from there to frames not yet sent to anyone. Leaning towards the last can only be forecasting; leaning more towards the frames sent than towards the control is a sign of reading them. The model does predict better from the frames sent than from the server's frame.

| Player | Page → server | Server → newest sent | Newest sent → not yet sent (control) | Lean on the frames sent, beyond the control |
| ------ | ------------- | -------------------- | ------------------------------------ | -------------------------------------------- |
| Zone | 53.1% | 59.1% | 55.6% | +3.5 |
| Precogbot | 54.3% | 58.1% | 55.0% | +3.1 |
| martin | 53.5% | 58.0% | 55.0% | +3.0 |
| doober | 52.4% | 55.7% | 53.5% | +2.2 |
| tokenmaxer_bot | 54.2% | 58.2% | 56.8% | +1.4 |
| foob | 52.6% | 54.2% | 54.2% | 0.0 |
| prrrrrrrrrrrrr | 56.2% | 54.3% | 58.9% | −4.6 |
| everyone else | 49.5% | 49.3% | 49.5% | |

Zone, Precogbot and martin lean about 3 points more towards the frames they've been sent than towards ones nobody has, which fits reading the stream, but most of their lean is forecasting, and with buys coming in bursts a gap that size could be noise. foob shows none of it: it is fast, not peeking. prrrrrrrrrrrrr is the odd one, better at frames not yet sent than at ones it has, which fits running the physics forward. The lookahead was kept at 48 frames: trading on the buffered frames is part of the game. Comparing the excess early in a drop (48 frames ahead) with the final stretch (12 ahead) would show more.

**Everyone else** (all but foob, martin and tokenmaxer_bot) loses slowly: −842 points over 626,319 buys in the 600 rounds, about −0.001 a share, mostly buying the side the ball is on (55% of buys), with no edge at any delay.

### After the update: what the timing log shows

A second study, of the first 141 rounds played under `e621554` (21:59 to 22:29 UTC on 27 November 2026), with the timing log, the shuffled send order and `start_x` rounded: 126,156 buys by 671 players, about 456 pages open. Every round replays exactly. Half an hour is a small sample beside the 600 rounds above, so read the smaller differences with care. The scripts are the same, plus [`timing.py`](analyses/timing.py).

**The house loses more, through the 0.99 limit.** It lost every round, 7.56 points each: the pricing's built-in loss is the same 4.83, but rounding and the price limits now cost 2.81 a round, against 0.16 before. 58,682 buys, 46% of all of them, were at the 0.99 limit, and 99.8% of those won: +456 points, a cent at a time, for shares the pricing would have charged about 0.998 for. A dozen players do it in volume, all late in the drop and none losing a single one: le-botty, prrrrrrrrrrrrr, hydrogen, Precogbot, Zone, kittens and martin about 4,000 each. For Zone (98%), kittens (99%) and martin (91%) it is nearly all they buy. Neither the pricing nor the limits changed in the update; players have copied what foob did late in a drop. The take is capped only by `MAX_BUYS_PER_SECOND`. Rules version 4 ended it by letting the price reach 1.00. Priced that way, 57,080 of these 58,682 buys would have cost 1.00, and together they would have lost 115 points instead of making 456.

**Who wins.** doober (+416) and foob (+409) are level, then hydrogen (+249), prrrrrrrrrrrrr (+197), hurf (+152), le-botty (+139) and Precogbot (+118). foob makes 2.9 a round, against 5.1 before, and 0.069 a share, against 0.12. martin has nearly stopped making money (+18; +0.005 a share, against +0.08). The cheap side is still where the money is: shares bought at 0.10 to 0.40 made 0.08 to 0.13 each, and at 0.60 to 0.90 lost 0.05 to 0.07. only_left (−253) and only_right always buy the same side.

**A broadcast reaches every page within about 20 ms.** It is written to the first page and the last 19.7 ms apart (median; 90% within 23 ms). So a page's place in the order is now worth at most 20 ms, and on average nothing.

**foob is fast; the send order doesn't explain it.** It still bought first in 73 of the 141 rounds, 59% of those it bought early in (63% before). Its buy reached the server a median 25 ms after the broadcast was written to it, and 13 ms in its fastest tenth: there and back across the network, plus whatever its code takes, in that time. So it sits very near the server and its code adds next to nothing. In 30 of the 134 rounds it played, it let the first broadcast go and bought on a later one. Its lead over doober in the opening race (83 ms) is four times the whole spread of the send order, and the round's first buyer had been written in the first half of the order in only 33 of 139 rounds. The question the first study left open is settled: it is foob's network and code, not its place in the order.

**The money is in the first 40 ms after a broadcast.** Early in a drop a broadcast goes out about every 0.6 s, carrying the next 0.8 s of the ball's path and a price that has not moved since the last one. Profit a share of every buy in that part of the drop, by how long after the broadcast was written to the buyer it was taken:

| Answered within | 0–40 ms | 40–80 ms | 80–150 ms | 150–300 ms | 300–600 ms |
| --------------- | ------- | -------- | --------- | ---------- | ---------- |
| Everyone | +0.057 (11,270 buys) | +0.018 | +0.005 | −0.001 | −0.003 |
| foob | +0.161 (2,008) | +0.034 | +0.054 | −0.026 | +0.053 |
| doober | +0.154 (968) | +0.034 | −0.008 | +0.060 | +0.066 |
| everyone but the nine players studied | +0.004 | −0.001 | −0.006 | −0.013 | −0.018 |

The first 40 ms took about 640 points over the half hour, more than half of everything players made. The price the broadcast showed is stale by the time anyone answers, and the first to answer buy it before it moves. Few answer that fast as a habit. With broadcasts 0.6 s apart, a player who buys at random times has about a sixth of their buys within 100 ms of one by chance. alef has 99% (median 26 ms, but only 321 buys), foob 64% (26 ms), hydrogen 43% (54 ms) and garethgeorge 42% (49 ms). kitsu has 74%, but never under 77 ms: a script on a distant network. Nearly everyone else is at chance.

**Some answer on a timer.** doober's first buy of a drop comes 137 to 230 ms after the write, every round. Its fastest answers later in the drop take 13 ms, so its network is fast and the wait is its own. hurf answers about 156 ms after each broadcast, give or take 8 ms. prrrrrrrrrrrrr's first buy reached the server before any broadcast of the round had been written to it in 20 rounds: it times its first buy off the countdown and buys without seeing anything.

**Some keep several pages open.** When a player has more than one page open, the log keeps the latest write to any of them, so their place in the order looks late. From how late, prrrrrrrrrrrrr keeps about 4.6 pages open, foob 4.1, alef 3.5, le-botty 3.2, tokenmaxer_bot 3.1 and goober 2.9. With the order shuffled, that is the one way to be written early more often, but over 20 ms it is worth only a few milliseconds. It also makes their answers look a few milliseconds quicker than they are. Logging the earliest write as well would measure it.

**hurf's page isn't the game's.** Every page reports the frame it is showing behind the server's frame. hurf's reports one about 29 frames (0.5 s) ahead, so it is showing the buffered frames. But hurf wins on price, not on the future: it buys the cheap side (average price 0.41, won 47%, +0.06 a share), and its edge hardly fades with delay (76% left after 200 ms).

**The sign of reading the buffered frames has gone.** With exact replays, no player leans towards the frames they have been sent any more than towards the frames nobody has: Zone 54.2% against 55.8% for the control, Precogbot 50.4% against 51.6%. The 3-point lean the first study found was most likely noise, as it allowed.

**The pack still follows foob.** In the 200 ms after one of foob's 1,441 bursts, others bought 24,093 shares on its side and 13,593 on the other; garethgeorge (90%), hydrogen (81%) and prrrrrrrrrrrrr (78%) most of all.

### An attempt on the random numbers

The player behind Precogbot told the story on Hacker News. They spent about four hours trying to predict where each ball would drop, which would have meant knowing every round's winner before betting opened.

The attack was a real one. Until `e621554`, where the ball dropped from came from Python's `random.uniform()`, and nothing else drew on Python's random numbers between drops (only the owner renaming a player does). Python's random numbers come from a Mersenne Twister. It isn't made to keep secrets: from about 20,000 bits of its output, anyone can work out its whole state, and with it every number to come. Each drop's `start_x` carries 53 of those bits. The ball's path is sent to every page, the physics is in the open and the same everywhere, and the path is chaotic. So searching for the starting point that gives a path, to the tenth of a pixel it is sent at, pins `start_x` down very closely, and they did that well. Knowing the top 40 bits or so of each would take about 500 rounds, two or three hours of play; they planned on 600.

Three things got in their way, none of them meant to:

- **A restart.** Every new server starts the Mersenne Twister afresh from the operating system's randomness, so the rounds they had collected told them nothing about the next ones.
- **`start_x` rounded to 15 significant digits** (`e621554`, for exact replays from the round log). It loses about 5 of the 53 bits, which means collecting more rounds, not giving up.
- **The shuffled send order** (`e621554`, so that no page always hears first). That is what made it hopeless. Each broadcast shuffles the open pages, several hundred of them, with the same Mersenne Twister, up to 10 times a second. Tens of thousands of its numbers now go by between one drop and the next, and how many depends on the numbers themselves.

It never paid off. Had it worked, Precogbot would have bought the winning side early, at about 0.50, nearly every round. Instead its buys in the first third of the betting window won 52 to 75% of the time, hour by hour, at an average of 0.52 to 0.92: ordinary buying of whichever side looked likely. In the half hour after the update, a third of its profit (40 of 118 points) came from the 0.99 limit.

Since then, `start_x` comes from the operating system's randomness (`random.SystemRandom()`), which can't be predicted from anything a player sees. The send order has a Mersenne Twister of its own, seeded the same way, which is far quicker for shuffling a thousand pages ten times a second. Nobody is ever shown the order, and it says nothing about the ball.

## Tuning the physics

Gravity, bounce and the hidden nudges can be passed on the command line:

```
uv run app.py --gravity 1500 --bounce 0.3 --nudge 0
```

| Option      | Default | Meaning                                                        |
| ----------- | ------- | -------------------------------------------------------------- |
| `--gravity` | 400     | Downward acceleration, in pixels / second². Higher is faster.  |
| `--bounce`  | 0.85    | Fraction of speed kept after hitting a pin or wall, 0 to 1. Lower makes the ball slow down more in the dense rows. |
| `--nudge`   | 0.003   | Typical size, in radians, of the hidden random turn at each pin bounce (see [The hidden nudges](#the-hidden-nudges)). 0 for none, which lets players work out the rest of the drop from the path. |

Everything else (board size, pin rows, ball and pin size) is a constant at the top of `app.py`.

The round log saves the gravity, bounce and nudges each round was played with, so rounds played under different options replay correctly. A change to one of the constants is only recorded as a new `commit_id`.
