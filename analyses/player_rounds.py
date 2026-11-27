# /// script
# dependencies = ["httpx==0.28.1", "pillow==12.0.0", "starlette==1.10.0", "uvicorn[standard]==0.54.0"]
# ///
"""How one player's rounds are built: guaranteed profit, reaction to others' buys, price after."""
import collections
import math
import pickle
import statistics as st
import os
import sys

sys.path.insert(0, os.environ.get("BOUNCEMARKET_CODE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))  # the app.py to use: this repo, or BOUNCEMARKET_CODE
import app

data = pickle.load(open(sys.argv[1], "rb"))
rows, names = data["rows"], data["names"]
key = {v.lower(): k for k, v in names.items()}[sys.argv[2].lower()]

def market_nets(row):
    """net (left - right) of all shares just before each player buy, index aligned to row['buys']."""
    bots = row["bot_buys"]; bi = 0; net = 0; out = []
    for b in row["buys"]:
        while bi < len(bots) and bots[bi][0] <= b[0]:
            net += 1 if bots[bi][1] == "left" else -1; bi += 1
        out.append(net)
        net += 1 if b[3] == "left" else -1
    return out

locked = []; outcomes = collections.Counter(); per_round = []
react = collections.Counter(); after = []; burst_sizes = []; burst_first_price = []
both_prices = []
price_bins = collections.defaultdict(lambda: [0, 0, 0.0])
for row in rows:
    buys = row["buys"]
    mine_idx = [i for i, b in enumerate(buys) if b[2] == key]
    if not mine_idx:
        continue
    nets = market_nets(row)
    L = sum(buys[i][3] == "left" for i in mine_idx); R = len(mine_idx) - L
    cost = sum(buys[i][4] for i in mine_idx)
    pl, pr = L - cost, R - cost
    locked.append(min(pl, pr))
    outcomes["both win" if pl > 0 and pr > 0 else "one side only" if pl > 0 or pr > 0 else "both lose"] += 1
    per_round.append((L if row["bucket"] == "left" else R) - cost)
    # bursts: consecutive buys of theirs in the same frame
    frames = collections.Counter(buys[i][0] for i in mine_idx)
    burst_sizes += list(frames.values())
    for i in mine_idx:
        f, side, price = buys[i][0], buys[i][3], buys[i][4]
        won = side == row["bucket"]
        b = price_bins[min(9, int(price * 10))]
        b[0] += 1; b[1] += won; b[2] += won - price
        # others' net buying in the 10 frames before (positive = towards left)
        prior = sum((1 if b2[3] == "left" else -1) for b2 in buys[:i] if b2[2] != key and f - 10 <= b2[0] < f)
        if prior:
            react["against recent flow" if (prior > 0) != (side == "left") else "with recent flow"] += 1
        # the market's price for their side 60 frames later
        later = [j for j in range(i, len(buys)) if buys[j][0] >= f + 60]
        if later:
            net_then = nets[later[0]]
            p_then = app.prices_for_net(net_then)["left" if side == "left" else "right"]
            after.append(p_then - price)

n = len(per_round)
print(f"rounds {n}; profit {sum(per_round):.2f} ({st.mean(per_round):+.2f}/round), lost {sum(p < 0 for p in per_round)} rounds")
print(f"locked-in (worst-case) profit per round: mean {st.mean(locked):+.2f}, median {st.median(locked):+.2f}; outcomes {dict(outcomes)}")
print(f"burst size (own buys in one frame): median {st.median(burst_sizes)}, max {max(burst_sizes)}; share of buys in bursts >=5: {sum(s for s in burst_sizes if s >= 5) / sum(burst_sizes):.0%}")
print(f"their side vs others' buying in the previous 10 frames: {dict(react)}")
print(f"price of their side 1s later minus price paid: mean {st.mean(after):+.3f}, median {st.median(after):+.3f}")
print("by price paid: buys, win%, profit")
for k in sorted(price_bins):
    c, w, p = price_bins[k]
    print(f"  {k / 10:.1f}-{k / 10 + 0.1:.1f}: {c:6d} {100 * w / c:5.1f}% {p:+9.2f}")
