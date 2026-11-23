# bouncemarket

A ball drops from a random spot, bounces off pins, and lands in one of two buckets.

## Requirements

[uv](https://docs.astral.sh/uv/). To install it:

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

uv installs the Python dependencies (Flask and the waitress web server, declared at the top of `app.py`) automatically on first run.

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

Betting stays open after the drop: while the ball is falling you can add to either bet (but not lower it) until the ball passes the last row of pins.

Besides the other players, there is a counterparty with a simple strategy: it bets one point on whichever half of the board the ball starts in, then one more point every 0.5 seconds on whichever half the ball is in at that moment, until betting closes. "Others" under each side is the total bet there by the counterparty and every other player.

There is no house. All bets go into one pot, and when the ball lands the pot is split among the points bet on the winning bucket. So you can only win what others lose, a crowded side pays little per point, and an empty side pays a lot. The "Pays" number under each side is what one point on that side gets back if it wins, given the bets so far. If nobody bet on the winning bucket, everyone gets their bets back.

## How it works

The server runs one game for everyone. It simulates each drop in full the moment the ball is released, but keeps the result to itself. Every open page gets a stream of snapshots (as [server-sent events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)), 60 a second while the ball is falling, so a page learns the bucket only when the ball lands. Each snapshot is sent 5 frames (a twelfth of a second) early and the page plays them back on its own clock, which keeps the ball moving smoothly when the network delivers them unevenly. That is as far ahead as a page ever sees.

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
