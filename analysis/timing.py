"""What the timing log says about speed: how long each broadcast takes to reach every page, who
answers broadcasts fastest once their place in the (now random) send order is taken out, and
whether answering fast still pays.

    uv run -m analysis.timing rounds.pkl foob,doober

Only rounds with timing are used (fetch them with fetch_rounds.py after e621554). Times are in
milliseconds by the server's clock. For a buy, "send" is how long after the broadcast was made
it was written to the buyer (their place in that broadcast's order), and "answer" is how long
after that write the server took the buy: the player's network there and back, plus their own
page or program. Only buys answering a broadcast written in the early part of the drop count as
reactions, where broadcasts are about 0.6 s apart: in the final stretch they come every 0.1 s,
so every buy is soon after one and a reaction can't be told from a steady stream of clicks.

A player with several pages open is logged with the latest write to any of them (written_to in
bouncemarket/state.py), so their place in the order looks late and their answer short: they answer the first
of their pages to be written, a few milliseconds earlier. "pages" estimates how many they keep
open from that: the latest of n random places averages n/(n+1)."""
import collections
import pickle
import statistics as st
import sys

d = pickle.load(open(sys.argv[1], "rb"))
rows, names = [r for r in d["rows"] if r.get("timing")], d["names"]
by_name = {v.lower(): k for k, v in names.items()}
watch = [by_name[n.lower()] for n in (sys.argv[2].split(",") if len(sys.argv) > 2 else []) if n.lower() in by_name]
name = lambda k: names.get(k, f"(guest {k})")
ms = lambda s: s * 1000
OPENING = 1.0  # seconds: the race at the start of a drop
EARLY_GAP = 0.3  # a broadcast is "early" if the next comes at least this long after it


def q(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(p * len(values)))] if values else float("nan")


print(f"{len(rows)} rounds with timing, {rows[0]['started_at'][:19]} .. {rows[-1]['started_at'][:19]}")

# 1. Fan-out: how long a broadcast takes to be written to every page.
spread = {"early": [], "final": []}
pages = []
for r in rows:
    b = r["timing"]["broadcasts"]
    for i, (built, first, last, queued, written) in enumerate(b):
        if first is None or last is None:
            continue
        gap = b[i + 1][0] - built if i + 1 < len(b) else 0
        spread["early" if gap >= EARLY_GAP else "final"].append(ms(last - first))
        pages.append(queued)
print(f"\nFan-out: {st.median(pages):.0f} pages a broadcast (median). First to last page written:")
for k, v in spread.items():
    print(f"  {k:>5} broadcasts: {len(v):5d}, median {st.median(v):5.1f} ms, p90 {q(v, .9):5.1f} ms, max {max(v):5.1f} ms")

# 2. The race at the start of each drop, with send order taken out.
race = collections.defaultdict(lambda: collections.defaultdict(list))
first_place = collections.Counter()
first_by_answer = collections.Counter()
before_write = collections.Counter()
luck = []  # for each round's winner: their place in broadcast 0's order, as a fraction (0 = written first)
for r in rows:
    t = r["timing"]
    b0 = t["broadcasts"][0] if t["broadcasts"] else None
    firsts = {}
    for (f, s, k, side, price, p), (taken, seen, idx) in zip(r["buys"], t["buys"]):
        if taken > OPENING:
            break
        if k in firsts:
            continue
        if seen is None:
            before_write[k] += 1
            continue
        firsts[k] = (taken, seen, idx)
    if not firsts or b0 is None:
        continue
    for k, (taken, seen, idx) in firsts.items():
        built, first, last = t["broadcasts"][idx][:3]
        race[k]["taken"].append(ms(taken))
        race[k]["answer"].append(ms(taken - seen))
        race[k]["send"].append(ms(seen - built))
        if idx == 0 and last > first:
            race[k]["place"].append((seen - first) / (last - first))
    winner = min(firsts, key=lambda k: firsts[k][0])
    first_place[winner] += 1
    first_by_answer[min(firsts, key=lambda k: firsts[k][0] - firsts[k][1])] += 1
    if race[winner]["place"]:
        luck.append(race[winner]["place"][-1])
regular = sorted((k for k in race if len(race[k]["taken"]) >= len(rows) * 0.3), key=lambda k: st.median(race[k]["taken"]))
print(f"\nThe race in the first {OPENING:.0f} s of a drop ({len(rows)} rounds). place = where the player's page came in")
print("broadcast 0's random order (0 first, 1 last); won = first buy of the round; by answer = would have been")
print("first had everyone been written at the same moment (flattering players with several pages open).")
print(f"{'player':>18} {'rounds':>6} {'won':>5} {'by answer':>9} {'taken':>7} {'send':>6} {'answer p10':>10} {'answer':>7} {'place':>6}")
for k in regular[:15]:
    x = race[k]
    print(f"{name(k):>18} {len(x['taken']):6d} {first_place[k]:5d} {first_by_answer[k]:9d} {st.median(x['taken']):7.1f} "
          f"{st.median(x['send']):6.1f} {q(x['answer'], .1):10.1f} {st.median(x['answer']):7.1f} {st.median(x['place']) if x['place'] else float('nan'):6.2f}")
if luck:
    print(f"the round's winner was in the first half of the send order in {sum(p < .5 for p in luck)} of {len(luck)} rounds")
if before_write:
    print("first buys taken before the player had been written any broadcast of the round (timed off the countdown):",
          ", ".join(f"{name(k)} {c}" for k, c in before_write.most_common(8)))

# 3. Reactions through the drop: the first buy after each early broadcast reached the player.
react = collections.defaultdict(list)  # player -> answer ms of their first buy after each early broadcast
place = collections.defaultdict(list)  # player -> place of the latest write to them, for every buy, to estimate their pages
early_buys = collections.Counter()
quick = collections.Counter()  # early buys within 100 ms of the write before them
profit_by_answer = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0.0]))
BANDS = [(0, 40), (40, 80), (80, 150), (150, 300), (300, 600), (600, 10_000)]
for r in rows:
    t = r["timing"]
    b = t["broadcasts"]
    early = {i for i in range(len(b) - 1) if b[i + 1][0] - b[i][0] >= EARLY_GAP}
    answered = set()
    for (f, s, k, side, price, p), (taken, seen, idx) in zip(r["buys"], t["buys"]):
        if idx is not None and b[idx][2] > b[idx][1]:
            place[k].append((seen - b[idx][1]) / (b[idx][2] - b[idx][1]))
        if idx is None or idx not in early:
            continue
        a = ms(taken - seen)
        early_buys[k] += 1
        quick[k] += a < 100
        if (k, idx) not in answered:
            answered.add((k, idx))
            react[k].append(a)
        band = next(f"{lo}-{hi}" for lo, hi in BANDS if a < hi)
        won = (side == r["bucket"]) - price
        for who in (k if k in watch else "rest", "all"):
            profit_by_answer[who][band][0] += 1
            profit_by_answer[who][band][1] += won
busy = sorted((k for k in react if len(react[k]) >= 200), key=lambda k: q(react[k], .5))
print(f"\nReactions to early broadcasts (about 0.6 s apart, so a steady clicker has about 1 in 6 of its buys")
print("within 100 ms of one by chance). first answer = how long after each broadcast was written their first buy came.")
print(f"{'player':>18} {'early buys':>10} {'<100 ms':>8} {'first answer p10':>16} {'median':>7} {'p90':>7} {'pages':>6}")
for k in busy[:20]:
    m = st.mean(place[k]) if place[k] else 0.5
    print(f"{name(k):>18} {early_buys[k]:10d} {100 * quick[k] / early_buys[k]:7.0f}% {q(react[k], .1):16.1f} {q(react[k], .5):7.1f} "
          f"{q(react[k], .9):7.1f} {m / (1 - m) if m < 1 else float('inf'):6.1f}")

print("\nProfit a share of early-stretch buys, by answer time (ms after the broadcast was written to the buyer):")
cols = [f"{lo}-{hi}" for lo, hi in BANDS]
print(f"{'':>18} " + " ".join(f"{c:>15}" for c in cols))
for who in [*watch, "rest", "all"]:
    if who not in profit_by_answer:
        continue
    cells = []
    for c in cols:
        n, p = profit_by_answer[who][c]
        cells.append(f"{p / n:+.3f} ({n:5d})" if n else f"{'':>15}")
    print(f"{who if isinstance(who, str) else name(who):>18} " + " ".join(f"{c:>15}" for c in cells))
