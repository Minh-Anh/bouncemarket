# /// script
# dependencies = ["httpx==0.28.1", "pillow==12.0.0", "starlette==1.10.0", "uvicorn[standard]==0.54.0"]
# ///
"""Does a player's side follow where the ball is about to go (frames the server has already
streamed but hasn't reached) more than where it has been?

For each buy at server frame f, side matches the sign of the ball's sideways move over
(f, f+h] for h > 0 (the future), or over (f+h, f] for h < 0 (the past). Only buys where the ball
actually moved at least 1 px sideways over that span count."""
import collections
import pickle
import os
import sys

sys.path.insert(0, os.environ.get("BOUNCEMARKET_CODE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))  # the app.py to use: this repo, or BOUNCEMARKET_CODE
import app

d = pickle.load(open(sys.argv[1], "rb"))
rows, names = d["rows"], d["names"]
by_name = {v.lower(): k for k, v in names.items()}
targets = sys.argv[2].split(",")
groups = {t: {by_name[t.lower()]} for t in targets if t.lower() in by_name}
everyone = {b[2] for r in rows for b in r["buys"]}
groups["everyone else"] = everyone - set().union(*groups.values())
H = [-30, -12, -6, -3, 3, 6, 12, 18, 24, 36, 60]

# "Turns": the ball's sideways direction over the next 12 frames is the opposite of the last 12.
for g, keys in groups.items():
    match = collections.Counter(); total = collections.Counter()
    turn = collections.Counter()
    for r in rows:
        path = r["path"]; last = len(path) - 1
        for (f, s, k, side, price, p) in r["buys"]:
            if k not in keys:
                continue
            x = path[f][0]
            left = side == "left"
            for h in H:
                a, b = (f, min(last, f + h)) if h > 0 else (max(0, f + h), f)
                dx = path[b][0] - path[a][0]
                if abs(dx) >= 1:
                    total[h] += 1
                    match[h] += (dx < 0) == left
            past = x - path[max(0, f - 12)][0]
            fut = path[min(last, f + 12)][0] - x
            if abs(past) >= 1 and abs(fut) >= 1 and (past < 0) != (fut < 0):
                turn["n"] += 1
                turn["follows the coming turn"] += (fut < 0) == left
    cells = "  ".join(f"{h:+d}:{100 * match[h] / total[h]:4.1f}" for h in H if total[h])
    t = f"{100 * turn['follows the coming turn'] / turn['n']:.1f}% of {turn['n']}" if turn["n"] else "-"
    print(f"{g:>15}  {cells}   at turns, follows the coming one: {t}")
