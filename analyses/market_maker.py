# /// script
# dependencies = ["httpx==0.28.1", "pillow==12.0.0", "starlette==1.10.0", "uvicorn[standard]==0.54.0"]
# ///
"""Break the market maker's P&L over the last hour into its parts, round by round.

For every round, with D = how many more shares (players + counterparty) the winning side has
than the losing side at the end, and b = PRICE_SOFTNESS, this is an exact identity:

  MM P&L vs players = lmsr        b*ln(1 + e^(-D/b)) - b*ln 2   (the LMSR's built-in subsidy)
                    + bot         counterparty shares' winnings - their exact cost (bots don't pay or collect)
                    + rounding    sum over player buys of (charged - exact LMSR cost), incl. the 0.01/0.99 clamp
"""
import collections
import datetime
import math
import os
import sys

sys.path.insert(0, os.environ.get("BOUNCEMARKET_CODE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))  # the app.py to use: this repo, or BOUNCEMARKET_CODE
import analysis
import app

B = app.PRICE_SOFTNESS
STEP = math.exp(1 / B)


def exact_cost(net, side):
    left = 1 / (1 + math.exp(-net / B))
    p = left if side == "left" else 1 - left
    return B * math.log(1 - p + p * STEP)


db = analysis.connect()
# The last hour, or from a time given as the first argument (e.g. 2026-11-27T21:59Z), to study one version's rounds.
since = sys.argv[1] if len(sys.argv) > 1 else (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)).isoformat()
rows = []
params = {"select": "*", "order": "id", "started_at": f"gte.{since}", "limit": 40}
while True:
    r = db.get("/rounds", params=params)
    r.raise_for_status()
    page = r.json()
    rows += page
    if len(page) < params["limit"]:
        break
    params["id"] = f"gt.{page[-1]['id']}"

tot = collections.Counter()
mismatched_prices = 0
by_depth = collections.defaultdict(lambda: [0, 0.0])  # buy frame as a fraction of close_frame -> [buys, player profit]
by_price = collections.defaultdict(lambda: [0, 0.0])
lmsr_terms, mm_round = [], []
check_worst = 0.0
commits = collections.Counter()
for row in rows:
    commits[row["commit_id"][:7]] += 1
    row["path"] = app.unpack_path(analysis.from_bytea(row["path"]))
    row["buys"] = app.unpack_buys(analysis.from_bytea(row["buys"]))
    version = analysis.rules(row)
    if version < 3:
        continue
    bots = analysis.bot_buys(row)
    win = row["bucket"]
    # Merge: at the same frame, the counterparty's buys come before the players' (costs(frame) counts bot buys <= frame).
    events = [(f, 0, side, None) for f, side in bots] + [(b[0], 1, b[3], b) for b in row["buys"]]
    events.sort(key=lambda e: (e[0], e[1]))
    net = 0
    paid = payout = rounding = bot_cost = bot_win = 0.0
    for f, is_player, side, b in events:
        ex = exact_cost(net, side)
        if is_player:
            price = b[4]
            if abs(analysis.costs_for_net(version, net)[side] - price) > 1e-9:
                mismatched_prices += 1
            paid += price
            won = side == win
            payout += won
            rounding += price - ex
            frac = min(9, int(10 * f / max(1, row["close_frame"])))
            by_depth[frac][0] += 1
            by_depth[frac][1] += won - price
            band = "1.00" if price >= 1 else "0.99" if price >= 0.99 else "0.01 (floor)" if price <= 0.01 else f"{int(price * 10) / 10:.1f}-{int(price * 10) / 10 + 0.1:.1f}"
            by_price[band][0] += 1
            by_price[band][1] += won - price
        else:
            bot_cost += ex
            bot_win += side == win
        net += 1 if side == "left" else -1
    D = net if win == "left" else -net
    lmsr = B * math.log(1 + math.exp(-D / B)) - B * math.log(2)
    bot = bot_win - bot_cost
    mm = paid - payout
    check_worst = max(check_worst, abs(mm - (lmsr + bot + rounding)))
    tot.update(rounds=1, paid=paid, payout=payout, mm=mm, lmsr=lmsr, bot=bot, rounding=rounding, n=len(row["buys"]))
    lmsr_terms.append(lmsr)
    mm_round.append(mm)

n = tot["rounds"]
print(f"rounds in the last hour: {len(rows)} (since {since[:19]}Z), commits {dict(commits)}; analysed {n} (rules 3 and later)")
print(f"player buys {tot['n']:.0f}; logged prices not matching the rebuilt market: {mismatched_prices}")
print(f"identity check, worst |mm - parts| in a round: {check_worst:.6f}")
print(f"players paid {tot['paid']:.2f}, paid out {tot['payout']:.2f}")
print(f"MM net vs players: {tot['mm']:+.2f}  ({tot['mm'] / n:+.3f}/round)")
print(f"  lmsr subsidy:   {tot['lmsr']:+.2f}  ({tot['lmsr'] / n:+.3f}/round; max possible {-B * math.log(2):.3f})")
print(f"  bot shares:     {tot['bot']:+.2f}  ({tot['bot'] / n:+.3f}/round)")
print(f"  rounding/clamp: {tot['rounding']:+.2f}  ({tot['rounding'] / n:+.3f}/round)")
print(f"rounds where MM lost: {sum(m < 0 for m in mm_round)}/{n}; lmsr term within 0.1 of max in {sum(t < -B * math.log(2) + 0.1 for t in lmsr_terms)}/{n}")
print("player profit by when in the betting window:")
for k in sorted(by_depth):
    c, p = by_depth[k]
    print(f"  {k * 10:3d}-{k * 10 + 10:3d}%: {c:7d} buys, profit {p:+9.2f}, per buy {p / c:+.4f}")
print("player profit by price paid:")
for k in sorted(by_price):
    c, p = by_price[k]
    print(f"  {k:>12}: {c:7d} buys, profit {p:+9.2f}, per buy {p / c:+.4f}")
