# /// script
# dependencies = []
# ///
"""Who piles in behind a player? For each of their bursts (a buy with none of theirs in the 6
frames before), count everyone else's buys in the next 12 frames (200 ms), on the same side and
on the other, and list the players who most often buy the same side right after.

    uv run analyses/followers.py rounds.pkl foob

rounds.pkl is made by fetch_rounds.py. Columns: same-side follow-ups, the share of that player's
follow-ups that were on the same side, and the share of all their buys that were follow-ups."""
import collections
import pickle
import sys

d = pickle.load(open(sys.argv[1], "rb"))
names = d["names"]
who = sys.argv[2] if len(sys.argv) > 2 else "foob"
leader = next(k for k, v in names.items() if v.lower() == who.lower())
WINDOW, QUIET = 12, 6

same, other, total = collections.Counter(), collections.Counter(), collections.Counter()
bursts = 0
for r in d["rows"]:
    buys = r["buys"]
    last = -99
    for i, b in enumerate(buys):
        if b[2] != leader:
            total[b[2]] += 1
            continue
        if b[0] - last > QUIET:
            bursts += 1
            for b2 in buys[i + 1:]:
                if b2[0] > b[0] + WINDOW:
                    break
                if b2[2] != leader:
                    (same if b2[3] == b[3] else other)[b2[2]] += 1
        last = b[0]

print(f"{who}: {bursts} bursts; others' buys within {WINDOW} frames after: "
      f"same side {sum(same.values())}, other side {sum(other.values())}")
print(f"{'player':>18} {'same side':>9} {'same %':>7} {'of their buys':>13}")
for k, c in same.most_common(int(sys.argv[3]) if len(sys.argv) > 3 else 10):
    n = c + other[k]
    print(f"{names.get(k, f'(guest {k})'):>18} {c:9d} {100 * c / n:6.1f}% {100 * n / total[k]:12.1f}%")
