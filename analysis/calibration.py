"""Is the market's price a fair chance? Sample the price every 6 frames of every round's betting
window and compare with how often that side actually won, by price and by phase of the round.
Also: when foob buys, how does their side's price compare with the calibrated chance?

    uv run -m analysis.calibration rounds.pkl foob
"""
import bisect
import collections
import pickle
import sys

from bouncemarket.game import pricing

d = pickle.load(open(sys.argv[1], "rb"))
rows, names = d["rows"], d["names"]
key = {v.lower(): k for k, v in names.items()}[sys.argv[2].lower()]

def nets_at(row, frames):
    """Market net (players + counterparty) at the start of each frame given."""
    events = sorted([(f, 0, s) for f, s in row["bot_buys"]] + [(b[0], 1, b[3]) for b in row["buys"]])
    fs = [e[0] for e in events]
    cum = [0]
    for e in events:
        cum.append(cum[-1] + (1 if e[2] == "left" else -1))
    return [cum[bisect.bisect_left(fs, f)] for f in frames]

# calibration of the *favourite* side's price, by phase
cal = collections.defaultdict(lambda: [0, 0.0, 0])  # (phase, price bin) -> [n, sum price, favourite won]
for r in rows:
    frames = list(range(0, r["close_frame"], 6))
    for f, net in zip(frames, nets_at(r, frames)):
        pl = pricing.prices_for_net(net)["left"]
        fav_left = pl >= 0.5
        p = pl if fav_left else 1 - pl
        phase = "early" if f < r["close_frame"] / 3 else "middle" if f < 2 * r["close_frame"] / 3 else "late"
        b = cal[(phase, min(9, int(p * 10)))]
        b[0] += 1; b[1] += p; b[2] += (r["bucket"] == "left") == fav_left
print("favourite's price vs how often it won (samples every 6 frames):")
for phase in ("early", "middle", "late"):
    cells = []
    for k in range(5, 10):
        n, sp, w = cal[(phase, k)]
        if n >= 30:
            cells.append(f"{sp / n:.2f}->{w / n:.2f} (n={n})")
    print(f"  {phase:6}: " + "   ".join(cells))

# foob: favourite or underdog?
side_kind = collections.Counter(); prof = collections.Counter()
for r in rows:
    mine = [b for b in r["buys"] if b[2] == key]
    for (f, s, k, side, price, p) in mine:
        kind = "underdog" if price < 0.5 else "favourite"
        phase = "early" if f < r["close_frame"] / 3 else "middle" if f < 2 * r["close_frame"] / 3 else "late"
        side_kind[(phase, kind)] += 1
        prof[(phase, kind)] += (side == r["bucket"]) - price
print("foob's buys by phase: count, profit")
for phase in ("early", "middle", "late"):
    print(f"  {phase:6}: " + "   ".join(f"{kind} {side_kind[(phase, kind)]:5d} {prof[(phase, kind)]:+8.2f}" for kind in ("underdog", "favourite")))
