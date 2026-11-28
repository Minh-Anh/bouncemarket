"""Do the last 60 cached rounds replay exactly from their start_x? Counts (exact, same bucket,
same length) and shows where the first few part from the saved path. Rounds played before
start_x was rounded to 15 digits mostly don't.

    uv run -m analysis.replay_check rounds.pkl
"""
import collections
import pickle
import sys

from bouncemarket.game import physics

rows = pickle.load(open(sys.argv[1], "rb"))["rows"]
c = collections.Counter(); firstdiff = []
for r in rows[-60:]:
    states, bucket = physics.replay_drop(r)
    pos = physics.positions(states)
    ok = pos == r["path"] and bucket == r["bucket"]
    c[(ok, bucket == r["bucket"], len(pos) == len(r["path"]))] += 1
    if not ok and len(firstdiff) < 3:
        i = next((i for i, (a, b) in enumerate(zip(pos, r["path"])) if a != b), None)
        firstdiff.append((r["id"], i, pos[i] if i is not None else None, r["path"][i] if i is not None else None, len(pos), len(r["path"])))
print(dict(c)); print(firstdiff)
