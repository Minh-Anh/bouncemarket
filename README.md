# bouncemarket

A ball drops from a random spot, bounces off pins, and lands in one of two buckets.

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

You start with 10 points. Each plus button buys one share on that side at the price shown. A share pays 1 point if the ball lands on its side and nothing if it doesn't. So a share bought at 0.30 makes 0.70 if it wins and loses 0.30 if it doesn't.

You can buy only while the ball is falling: from the moment it drops until just before it passes the last row of pins. Shares can't be sold back; you hold them until the ball lands.

The price of a side is its fraction of all the shares bought this round, starting at 0.50 each, so the two prices always add up to 1. Every buy pushes that side's price up and the other side's down. Being right early, while a side is still cheap, pays more than piling in once everyone agrees.

There is also a counterparty with a simple strategy: it buys one share on whichever half of the board the ball starts in, then one more every 0.5 seconds on whichever half the ball is in at that moment, until betting closes. Its buying drifts the price towards the side the ball has been spending its time on. The counterparty is also the seller of every share you buy: it keeps what you pay and pays out the winners.

Anyone can play straight away. To appear on the leaderboard, enter a name of up to 16 characters. Names are unique, and from then on your points are saved.

## How it works

The server runs one game for everyone. It simulates each drop in full the moment the ball is released, but keeps the result to itself.

Every open page holds one stream from the server (as [server-sent events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)) carrying two kinds of message:

- A public "game" message, 10 times a second, identical for every player: the countdown, the price of each side, and while the ball is falling, its next few positions.
- A private "me" message with your name, points and shares, sent when you connect and after each round. Buying a share returns the same information.
- A "leaderboard" message with the top 10 named players, sent when you connect and after each round.

Ball positions are sent up to 12 frames (a fifth of a second) early and the page plays them back on its own clock, which keeps the ball moving smoothly when the network delivers messages unevenly. That is as far ahead as a page ever sees, and betting closes that same fifth of a second before the ball passes the last row, so nobody can bet on a result they have already been sent.

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
| `--bounce`  | 0.8     | Fraction of speed kept after hitting a pin or wall, 0 to 1. Lower makes the ball slow down more in the dense rows. |

Everything else (board size, pin rows, ball and pin size) is a constant at the top of `app.py`.
