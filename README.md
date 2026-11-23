# bouncemarket

A ball drops from a random spot, bounces off pins, and lands in one of two buckets.

## Requirements

[uv](https://docs.astral.sh/uv/). To install it:

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

uv installs Flask (the only Python dependency, declared at the top of `app.py`) automatically on first run.

## Run locally

```
uv run app.py
```

Then open http://localhost:8000.

## Tuning the physics

Gravity and bounce can be passed on the command line:

```
uv run app.py --gravity 1500 --bounce 0.3
```

| Option      | Default | Meaning                                                        |
| ----------- | ------- | -------------------------------------------------------------- |
| `--gravity` | 1200    | Downward acceleration, in pixels / second². Higher is faster.  |
| `--bounce`  | 0.5     | Fraction of speed kept after hitting a pin or wall, 0 to 1. Lower makes the ball slow down more in the dense rows. |

Everything else (board size, pin rows, ball and pin size) is a constant at the top of `app.py`.
