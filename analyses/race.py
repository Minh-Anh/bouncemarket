# /// script
# dependencies = ["httpx==0.28.1", "pillow==12.0.0", "starlette==1.10.0", "uvicorn[standard]==0.54.0"]
# ///
"""The start-of-round race. Betting opens at frame 0 and every page hears of the drop from the
same broadcast, at most 6 frames (100 ms) later. game["buys"] is in arrival order, so within a
round the order of each player's first buy is the order their first request reached the server.

For each player: in how many rounds they bought in the opening second, their median frame and
arrival rank (among players, by first buy) in those rounds, and how often they arrived first."""
import collections
import pickle
import statistics as st
import sys

d = pickle.load(open(sys.argv[1], "rb"))
rows, names = d["rows"], d["names"]
OPENING = 60  # frames: the first second

first_frames = collections.defaultdict(list)
ranks = collections.defaultdict(list)
firsts = collections.Counter()
frame0 = collections.Counter()
round_first_frame = []
for r in rows:
    seen = {}
    for (f, s, k, side, price, p) in r["buys"]:
        if f >= OPENING:
            break
        if k not in seen:
            seen[k] = (len(seen), f)
    if not seen:
        continue
    round_first_frame.append(min(f for _, f in seen.values()))
    for k, (rank, f) in seen.items():
        first_frames[k].append(f)
        ranks[k].append(rank)
        firsts[k] += rank == 0
        frame0[k] += f == 0

n_rounds = len(rows)
print(f"{n_rounds} rounds; earliest buy of the round: median frame {st.median(round_first_frame)}, "
      f"frame 0 in {sum(f == 0 for f in round_first_frame)} rounds")
active = [k for k in first_frames if len(first_frames[k]) >= n_rounds * 0.3]
active.sort(key=lambda k: (st.median(ranks[k]), st.median(first_frames[k])))
print(f"{'player':>18} {'rounds':>6} {'first%':>7} {'frame0%':>8} {'med frame':>9} {'p10 frame':>9} {'med rank':>8}")
for k in active[:20]:
    fr = sorted(first_frames[k])
    print(f"{names.get(k, f'(guest {k})'):>18} {len(fr):6d} {100 * firsts[k] / len(fr):6.1f}% {100 * frame0[k] / len(fr):7.1f}% "
          f"{st.median(fr):9.1f} {fr[len(fr) // 10]:9d} {st.median(ranks[k]):8.1f}")
# Head to head: a player (the second argument, foob by default) against the next fastest.
by_name = {v.lower(): k for k, v in names.items()}
who = (sys.argv[2] if len(sys.argv) > 2 else "foob").lower()
if who in by_name and by_name[who] in first_frames:
    me = by_name[who]
    pos = active.index(me) + 1 if me in active else None
    fr = sorted(first_frames[me])
    print(f"{who}: place {pos} of {len(active)} regular early buyers; first in {firsts[me]} rounds; "
          f"median frame {st.median(fr)}, median rank {st.median(ranks[me])}")
    for rival in [k for k in active if k != me][:3]:
        ahead = behind = 0
        leads = []
        for r in rows:
            first = {}
            for i, b in enumerate(r["buys"]):
                if b[0] >= OPENING:
                    break
                first.setdefault(b[2], (i, b[0]))
            if me in first and rival in first:
                (mi, mf), (ri, rf) = first[me], first[rival]
                ahead += mi < ri
                behind += mi > ri
                leads.append(rf - mf)
        if leads:
            lead = st.median(leads)
            print(f"  vs {names.get(rival, f'(guest {rival})')}: {len(leads)} rounds, {who} first in {ahead}, "
                  f"the other first in {behind}, median lead {lead} frames ({lead * 1000 / 60:.0f} ms)")
