# /// script
# dependencies = ["httpx==0.28.1", "pillow==12.0.0", "starlette==1.10.0", "uvicorn[standard]==0.54.0"]
# ///
"""How fast does a player's edge decay with reaction time?

For each buy at frame f (arrival index i), the same buy made d frames later would have been
charged costs_for_net(net) for the same side, where net counts every buy (players and the
counterparty) that arrived at frames < f + d, minus this player's own buys from index i on
(so their own buying isn't counted as moving the price against them). Buys whose f + d is at or
past close_frame couldn't have been made: they're left out of every column, so each column
compares the same buys. Also: how much of the edge is gone within each delay."""
import bisect
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
DELAYS = [0, 1, 2, 3, 4, 6, 9, 12, 18, 30, 60]
groups = {t: {by[t.lower()]} for t in targets if t.lower() in by}
everyone = {b[2] for r in rows for b in r["buys"]}
groups["everyone else"] = everyone - set().union(*groups.values())
sign = lambda side: 1 if side == "left" else -1

for g, keys in groups.items():
    profit = collections.Counter(); n = 0
    for r in rows:
        close, win = r["close_frame"], r["bucket"]
        # all buys in arrival order with counterparty buys placed before player buys of the same frame
        events = sorted([(f, 0, j, s, None) for j, (f, s) in enumerate(r["bot_buys"])] +
                        [(b[0], 1, j, b[3], b[2]) for j, b in enumerate(r["buys"])], key=lambda e: (e[0], e[1], e[2]))
        frames = [e[0] for e in events]
        cum = [0]
        for e in events:
            cum.append(cum[-1] + sign(e[3]))
        own = [(j, b[0], sign(b[3])) for j, b in enumerate(r["buys"]) if b[2] in keys]
        if not own:
            continue
        own_frames = [o[1] for o in own]
        own_cum = [0]
        for o in own:
            own_cum.append(own_cum[-1] + o[2])
        for idx, (j, f, sg) in enumerate(own):
            if f + DELAYS[-1] >= close:
                continue
            b = r["buys"][j]
            side, price = b[3], b[4]
            won = side == win
            n += 1
            for dly in DELAYS:
                if dly == 0:
                    cost = price
                else:
                    net_all = cum[bisect.bisect_left(frames, f + dly)]
                    mine_after = own_cum[bisect.bisect_left(own_frames, f + dly)] - own_cum[idx]
                    cost = app.costs_for_net(net_all - mine_after)[side]
                profit[dly] += won - cost
    if not n:
        continue
    base = profit[0]
    cells = "  ".join(f"{dly:>2}f:{profit[dly] / n:+.4f}" for dly in DELAYS)
    keep = "  ".join(f"{dly:>2}f:{100 * profit[dly] / base:4.0f}%" for dly in DELAYS[1:]) if base > 0 else ""
    print(f"{g:>16} n={n:6d}  per buy  {cells}")
    if keep:
        print(f"{'':>16}          edge kept {keep}")
