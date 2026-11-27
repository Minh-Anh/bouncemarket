# /// script
# dependencies = ["httpx==0.28.1", "pillow==12.0.0", "starlette==1.10.0", "uvicorn[standard]==0.54.0"]
# ///
"""Profile one player's trading against everyone else, from the cached rounds."""
import collections
import pickle
import statistics as st
import os
import sys

sys.path.insert(0, os.environ.get("BOUNCEMARKET_CODE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))  # the app.py to use: this repo, or BOUNCEMARKET_CODE
import app

data = pickle.load(open(sys.argv[1], "rb"))
rows, names = data["rows"], data["names"]
targets = sys.argv[2].split(",")
key_by_name = {v.lower(): k for k, v in names.items()}
W = app.WIDTH
LOOK = app.BUFFER_FRAMES

# Empirical chance of landing left from where the ball is, on a grid, from all cached rounds.
GX, GY = 24, 24
grid = collections.defaultdict(lambda: [0, 0])
def cell(x, y):
    return (min(GX - 1, max(0, int(x / W * GX))), min(GY - 1, max(0, int(y / app.HEIGHT * GY))))
for row in rows:
    for f in range(0, row["close_frame"] + LOOK + 1, 2):
        if f < len(row["path"]):
            g = grid[cell(*row["path"][f])]
            g[0] += row["bucket"] == "left"; g[1] += 1
def p_left(x, y):
    a, n = grid[cell(x, y)]
    return (a + 1) / (n + 2)

def stats_for(keyset):
    s = collections.Counter(); lags = []; gaps = []; fracs = []; prices = []
    agree_now = agree_ahead = agree_vel = agree_final = 0
    edge_now = edge_ahead = 0.0
    fade = follow = 0
    rounds_in = set(); both_sides = 0
    for row in rows:
        path, close, win = row["path"], row["close_frame"], row["bucket"]
        mine = [b for b in row["buys"] if b[2] in keyset]
        if not mine:
            continue
        rounds_in.add(row["id"])
        if len({b[3] for b in mine}) == 2:
            both_sides += 1
        frames = [b[0] for b in mine]
        gaps += [b - a for a, b in zip(frames, frames[1:])]
        others = [b for b in row["buys"] if b[2] not in keyset]
        oi = 0; recent = collections.deque()
        for (f, seen, k, side, price, pts) in mine:
            s["n"] += 1; won = side == win; s["won"] += won; s["profit"] += won - price
            prices.append(price); fracs.append(f / max(1, close))
            if seen is not None and seen != app.NOT_SEEN:
                lags.append(f - seen)
            else:
                s["no_seen"] += 1
            x, y = path[f]
            xa, ya = path[min(len(path) - 1, f + LOOK)]
            left = side == "left"
            agree_now += (x < W / 2) == left
            agree_ahead += (xa < W / 2) == left
            if row["states"]:
                agree_vel += (row["states"][f][2] < 0) == left
            pn, pa = p_left(x, y), p_left(xa, ya)
            edge_now += (pn if left else 1 - pn) - price
            edge_ahead += (pa if left else 1 - pa) - price
            # what other players bought in the 30 frames before
            flow = sum((1 if b[3] == "left" else -1) for b in others if f - 30 <= b[0] < f)
            if flow:
                follow += (flow > 0) == left; fade += (flow > 0) != left
    n = s["n"]
    if not n:
        return None
    return {
        "buys": n, "rounds": len(rounds_in), "profit": round(s["profit"], 2), "per_buy": round(s["profit"] / n, 4),
        "win%": round(100 * s["won"] / n, 1), "avg_price": round(st.mean(prices), 3),
        "buys/round": round(n / len(rounds_in), 1), "both_sides_rounds%": round(100 * both_sides / len(rounds_in), 1),
        "median_gap_frames": st.median(gaps) if gaps else None, "gap_p10": sorted(gaps)[len(gaps) // 10] if gaps else None,
        "no_seen_frame%": round(100 * s["no_seen"] / n, 1),
        "lag_median": st.median(lags) if lags else None, "lag_p10/p90": (sorted(lags)[len(lags) // 10], sorted(lags)[9 * len(lags) // 10]) if lags else None,
        "window_median%": round(100 * st.median(fracs)),
        "side=ball_now%": round(100 * agree_now / n, 1), "side=ball_+12f%": round(100 * agree_ahead / n, 1),
        "side=velocity%": round(100 * agree_vel / n, 1),
        "edge_vs_grid_now": round(edge_now / n, 4), "edge_vs_grid_+12f": round(edge_ahead / n, 4),
        "follows_crowd%": round(100 * follow / max(1, follow + fade), 1),
    }

everyone = {b[2] for row in rows for b in row["buys"]}
groups = {t: {key_by_name[t.lower()]} for t in targets if t.lower() in key_by_name}
groups["everyone else"] = everyone - set().union(*groups.values())
results = {g: stats_for(ks) for g, ks in groups.items()}
for metric in next(r for r in results.values() if r):
    print(f"{metric:22}" + "".join(f"{str(results[g][metric] if results[g] else '-'):>16}" for g in groups))
print(" " * 22 + "".join(f"{g:>16}" for g in groups))
