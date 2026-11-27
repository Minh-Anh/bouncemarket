# /// script
# dependencies = ["httpx==0.28.1", "pillow==12.0.0", "starlette==1.10.0", "uvicorn[standard]==0.54.0"]
# ///
"""Who trades on frames they've been sent but their page hasn't shown yet?

A fair-chance model: P(left | x, y, vx, vy), from how often the ball landed left from each cell of
a grid over position and velocity, learned on the other half of the rounds (two-fold, by round).

For a buy at server frame f, three views of the ball:
  shown   f - 12   roughly what a normal page is showing (pages run ~12 frames behind)
  now     f        where the server is
  ahead   f + A    the newest frame the stream has sent: A = 12 in the final stretch, up to 48 before
Each step's change in the model's chance for the side bought is new information. A player who
uses a view leans their buys towards the side that view's change favours, more than 50%.
Only buys where that change moves the chance by >= 0.05 count, so the shares are meaningful."""
import collections
import pickle
import os
import sys

sys.path.insert(0, os.environ.get("BOUNCEMARKET_CODE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))  # the app.py to use: this repo, or BOUNCEMARKET_CODE
import app

d = pickle.load(open(sys.argv[1], "rb"))
rows, names = d["rows"], d["names"]
by = {v.lower(): k for k, v in names.items()}
targets = sys.argv[2].split(",")
W, H = app.WIDTH, app.HEIGHT
GX, GY, GV = 32, 32, 7

def state(path, f):
    f = max(1, min(len(path) - 1, f))
    (x0, y0), (x1, y1) = path[f - 1], path[f]
    return x1, y1, (x1 - x0) * 60, (y1 - y0) * 60

def cell(s):
    x, y, vx, vy = s
    vb = min(GV - 1, max(0, int((vx + 210) / 60)))  # sideways speed in 60 px/s bins
    return (min(GX - 1, max(0, int(x / W * GX))), min(GY - 1, max(0, int(y / H * GY))), vb)

halves = [rows[0::2], rows[1::2]]
models = []
for half in halves:
    m = collections.defaultdict(lambda: [0, 0])
    for r in half:
        for f in range(1, min(len(r["path"]), r["close_frame"] + 60)):
            c = m[cell(state(r["path"], f))]
            c[0] += r["bucket"] == "left"; c[1] += 1
    models.append(m)

def p_left(model, s):
    a, n = model[cell(s)]
    return (a + 2) / (n + 4)

groups = {t: {by[t.lower()]} for t in targets if t.lower() in by}
everyone = {b[2] for r in rows for b in r["buys"]}
groups["everyone else"] = everyone - set().union(*groups.values())
group_of = {k: g for g, ks in groups.items() for k in ks}

STEPS = ["shown->now", "now->ahead", "ahead->unsent"]
lean = {g: collections.Counter() for g in groups}
cnt = {g: collections.Counter() for g in groups}
brier = collections.Counter()
for hi, half in enumerate(halves):
    model = models[1 - hi]  # learned on the other half
    for r in half:
        path, close = r["path"], r["close_frame"]
        ahead_n = lambda f: app.BUFFER_FRAMES if f >= close - app.FINAL_STRETCH_FRAMES else app.EARLY_AHEAD_FRAMES
        for (f, s_, k, side, price, pts) in r["buys"]:
            g = group_of[k]
            sgn = 1 if side == "left" else -1
            p_shown = p_left(model, state(path, f - 12))
            p_now = p_left(model, state(path, f))
            # the newest frame sent can't pass the last row while betting is open
            fa = min(f + ahead_n(f), close + app.BUFFER_FRAMES - 1)
            p_ahead = p_left(model, state(path, fa))
            # control: frames nobody has been sent yet
            p_beyond = p_left(model, state(path, fa + 24))
            for step, delta in (("shown->now", p_now - p_shown), ("now->ahead", p_ahead - p_now), ("ahead->unsent", p_beyond - p_ahead)):
                if abs(delta) >= 0.05:
                    cnt[g][step] += 1
                    lean[g][step] += (delta > 0) == (sgn > 0)
            # how well each view predicts the outcome (sanity check of the model)
            if g == "everyone else":
                won_left = r["bucket"] == "left"
                for name, p in (("shown", p_shown), ("now", p_now), ("ahead", p_ahead)):
                    brier[name] += (p - won_left) ** 2
                brier["n"] += 1

print("model check (Brier score at everyone's buys, lower is better): " +
      ", ".join(f"{v}: {brier[v] / brier['n']:.3f}" for v in ("shown", "now", "ahead")))
print(f"{'':>16}  " + "   ".join(f"{s:>22}" for s in STEPS))
for g in groups:
    cells = []
    for s in STEPS:
        n = cnt[g][s]
        cells.append(f"{100 * lean[g][s] / n:5.1f}% of {n:6d}" if n else f"{'-':>22}")
    print(f"{g:>16}  " + "   ".join(f"{c:>22}" for c in cells))
