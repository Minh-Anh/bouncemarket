# /// script
# dependencies = ["httpx==0.28.1", "pillow==12.0.0", "starlette==1.10.0", "uvicorn[standard]==0.54.0"]
# ///
"""A player's buys by point in the betting window (win rate, average price, profit), how their
cheap buys sit against where the ball is and which way it's moving, and how often their first
buy of a round was right.

    uv run --env-file .env analyses/player_phases.py rounds.pkl foob
"""
import collections, os, pickle, sys
sys.path.insert(0, os.environ.get("BOUNCEMARKET_CODE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))  # the app.py to use: this repo, or BOUNCEMARKET_CODE
import app
d = pickle.load(open(sys.argv[1], "rb")); rows = d["rows"]
key = {v.lower(): k for k, v in d["names"].items()}[sys.argv[2].lower()]
print("rounds with replayed states:", sum(r["states"] is not None for r in rows), "of", len(rows))
W = app.WIDTH
win = collections.defaultdict(lambda: [0, 0, 0.0, 0.0])
cheap = collections.Counter()
first_net = collections.Counter()
for r in rows:
    mine = [b for b in r["buys"] if b[2] == key]
    if not mine: continue
    for (f, s, k, side, price, p) in mine:
        frac = min(9, int(10 * f / max(1, r["close_frame"])))
        w = win[frac]; won = side == r["bucket"]
        w[0] += 1; w[1] += won; w[2] += price; w[3] += won - price
        if price < 0.4 and r["states"]:
            x, y, vx, vy = r["states"][f]
            cheap["n"] += 1
            cheap["ball on their side"] += (x < W / 2) == (side == "left")
            cheap["moving towards their side"] += (vx < 0) == (side == "left")
            cheap["won"] += won
    # what was their first buy's frame and was it right
    f0 = mine[0]
    first_net["first buy right" if f0[3] == r["bucket"] else "first buy wrong"] += 1
print("by point in betting window: buys, win%, avg price, profit")
for k in sorted(win):
    n, w, pr, p = win[k]
    print(f"  {k*10:3d}-{k*10+10:3d}%: {n:6d} {100*w/n:5.1f}% {pr/n:.3f} {p:+8.2f}")
print("cheap (<0.40) buys:", {k: (v if k == 'n' else f"{100*v/cheap['n']:.1f}%") for k, v in cheap.items()})
print(dict(first_net))
