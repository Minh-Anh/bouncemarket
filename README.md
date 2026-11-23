# bouncemarket

A ball drops from a random spot, bounces off pins, and lands in one of two buckets.

https://bouncemarket.onrender.com/

## Requirements

[uv](https://docs.astral.sh/uv/). To install it:

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

uv installs the Python dependencies (the Starlette web framework and the uvicorn server, declared at the top of `app.py`) automatically on first run.

## Run locally

```
uv run app.py
```

Then open http://localhost:8000.

## Deploy

The app is hosted on [Render](https://render.com), which reads `render.yaml`, installs `requirements.txt`, and runs `python app.py`. Every push to `main` redeploys it.

## How to play

Everyone who opens the page watches the same ball and bets into the same market. A new ball drops 5 seconds after the last one lands.

You start with 10 points. Use the plus and minus buttons to bet points on left, right, or both, as long as the bets add up to no more than your points.

Betting stays open after the drop: while the ball is falling you can add to either bet (but not lower it) until just before the ball passes the last row of pins.

Besides the other players, there is a counterparty with a simple strategy: it bets one point on whichever half of the board the ball starts in, then one more point every 0.5 seconds on whichever half the ball is in at that moment, until betting closes. "Others" under each side is the total bet there by the counterparty and every other player.

There is no house. All bets go into one pot, and when the ball lands the pot is split among the points bet on the winning bucket. So you can only win what others lose, a crowded side pays little per point, and an empty side pays a lot. The "Pays" number under each side is what one point on that side gets back if it wins, given the bets so far. If nobody bet on the winning bucket, everyone gets their bets back.

## How it works

The server runs one game for everyone. It simulates each drop in full the moment the ball is released, but keeps the result to itself.

Every open page holds one stream from the server (as [server-sent events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)) carrying two kinds of message:

- A public "game" message, 10 times a second, identical for every player: the countdown, the total bets on each side, and while the ball is falling, its next few positions.
- A private "me" message with your points and bets, sent when you connect and after each round. Placing a bet returns the same information.

Ball positions are sent up to 12 frames (a fifth of a second) early and the page plays them back on its own clock, which keeps the ball moving smoothly when the network delivers messages unevenly. That is as far ahead as a page ever sees, and betting closes that same fifth of a second before the ball passes the last row, so nobody can bet on a result they have already been sent.

The server is a single process on a single thread that switches between pages whenever one is waiting, so each open page costs very little. On a laptop it held 5,000 open pages using about 60% of one processor core.

Your browser remembers a random id that tells the server which player you are. Points live in the server's memory, so they reset whenever the server restarts.

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
