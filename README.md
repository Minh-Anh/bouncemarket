# bouncemarket

A ball drops from a random spot in the middle two-thirds of the top, bounces off pins, and lands in one of two buckets. The board is a trapezoid: its walls lean inwards, so the pins get closer together towards the bottom and the ball slows down as it falls.

https://bouncemarket.onrender.com/

## Requirements

[uv](https://docs.astral.sh/uv/). To install it:

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

uv installs the Python dependencies (the Starlette web framework, the uvicorn server and the httpx web client, declared at the top of `app.py`) automatically on first run.

A [Supabase](https://supabase.com) project, which stores the named players. To set it up:

1. In the Supabase dashboard, open the SQL editor and run the contents of `schema.sql`.
2. Create a file called `.env` in this folder (git ignores it) with the project's URL and its secret key, both found under the project's API settings:

```
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-secret-key
```

Use the secret key (also called the service role key), not the publishable one, and never put it in the page or commit it.

## Run locally

```
uv run --env-file .env app.py
```

Then open http://localhost:8000.

## Deploy

The app is hosted on [Render](https://render.com), which reads `render.yaml`, installs `requirements.txt`, and runs `python app.py`. Every push to `main` redeploys it. `SUPABASE_URL` and `SUPABASE_KEY` are set under Environment in the Render dashboard.

## How to play

Everyone who opens the page watches the same ball and trades in the same market. A new ball drops 5 seconds after the last one lands.

You start with 10 points. Each "Buy 1 share" button buys one share on that side at the price shown. A share pays 1 point if the ball lands on its side and nothing if it doesn't. So a share bought at 0.30 makes 0.70 if it wins and loses 0.30 if it doesn't.

You can buy only while the ball is falling: from the moment it drops until just before it passes the last row of pins. Shares can't be sold back; you hold them until the ball lands.

The price of a side is its fraction of all the shares bought this round, starting at 0.50 each, so the two prices always add up to 1. Every buy pushes that side's price up and the other side's down. Being right early, while a side is still cheap, pays more than piling in once everyone agrees.

There is also a counterparty with a simple strategy: it buys one share on whichever half of the board the ball starts in, then keeps buying on whichever half the ball is in at that moment, until betting closes. It buys faster the lower the ball is: once a second with the ball at the top, speeding up to 10 times a second by the bottom of the betting zone, where the ball's position says the most about where it will land. Its buying drifts the price towards the side the ball has been spending its time on, and most strongly towards where it is near the end. The counterparty is also the seller of every share you buy: it keeps what you pay and pays out the winners.

Anyone can play straight away, and is ranked on the leaderboard from the start: until you claim a name you appear as "you", where your points would put you, with a "set username" button. Other people's guests aren't shown, only named players and you. A name can be up to 16 characters. Names are unique, and from then on your points are saved. The circling-arrows button beside your name on the leaderboard starts you over with 10 points, after asking first. It can't be used while you hold shares in the round being played.

## How it works

The server runs one game for everyone. It simulates each drop in full the moment the ball is released, but keeps the result to itself.

Every open page holds one stream from the server (as [server-sent events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)) carrying two kinds of message:

- A public "game" message, identical for every player: the countdown, the price of each side, and while the ball is falling, its next positions. It is sent only as often as needed, because at scale these messages are most of the server's work and bandwidth: twice a second early in a drop, 10 times a second for the last 1.5 seconds before betting closes and until the ball lands, once a second between drops, and straight away when a drop starts or the ball lands.
- A private "me" message with your name, points and shares, sent when you connect and after each round. Buying a share returns the same information.
- A "leaderboard" message with the top 10 named players, sent when you connect and after each round. If you're ranked below the top 10, it also has the two players either side of you, which the page shows under a "…" with your row in yellow. The server reads every named player's points from the database once when it starts, then keeps the ranking up to date in memory: it is the only thing that changes anyone's points, so it never needs to read them again.

Ball positions are sent early and the page plays them back on its own clock, which keeps the ball moving smoothly when the network delivers messages unevenly. Early in a drop they are sent up to 48 frames (0.8 seconds) early. For the last 1.5 seconds before betting closes, only 12 frames (a fifth of a second) early. Betting closes that fifth of a second before the ball passes the last row, and while betting is open no page is ever sent a position past the last row, so nobody can bet on a result they have already been sent. Positions are rounded to a tenth of a pixel to keep messages short (`POSITION_DECIMALS` in `app.py`).

The server is a single process on a single thread that switches between pages whenever one is waiting, so each open page costs very little. On a laptop it held 5,000 open pages using about 60% of one processor core.

Your browser remembers a random id that tells the server which player you are. Whoever has that id is that player, so it works like a password and is never shown to anyone else.

If the database can't be reached, the game carries on: everyone can still play, but names can't be claimed and saved points aren't loaded until it is back.

Players without a name live only in the server's memory, and lose their points when it restarts. Once you claim a name, your id, name and points are a row in the Supabase `players` table. The server saves your points there after every round you play in, and loads them back the next time your browser connects.

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
