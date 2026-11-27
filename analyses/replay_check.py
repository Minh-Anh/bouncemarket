# /// script
# dependencies = ["httpx==0.28.1", "pillow==12.0.0", "starlette==1.10.0", "uvicorn[standard]==0.54.0"]
# ///
"""Do the last 60 cached rounds replay exactly from their start_x? Counts (exact, same bucket,
same length) and shows where the first few part from the saved path. Rounds played before
start_x was rounded to 15 digits mostly don't.

    uv run --env-file .env analyses/replay_check.py rounds.pkl
"""
import collections, os, pickle, sys
sys.path.insert(0, os.environ.get("BOUNCEMARKET_CODE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))  # the app.py to use: this repo, or BOUNCEMARKET_CODE
import app
rows = pickle.load(open(sys.argv[1], "rb"))["rows"]
c = collections.Counter(); firstdiff = []
for r in rows[-60:]:
    states, bucket = app.simulate_drop(r["start_x"], r["gravity"], r["bounce"])
    pos = app.positions(states)
    ok = pos == r["path"] and bucket == r["bucket"]
    c[(ok, bucket == r["bucket"], len(pos) == len(r["path"]))] += 1
    if not ok and len(firstdiff) < 3:
        i = next((i for i, (a, b) in enumerate(zip(pos, r["path"])) if a != b), None)
        firstdiff.append((r["id"], i, pos[i] if i is not None else None, r["path"][i] if i is not None else None, len(pos), len(r["path"])))
print(dict(c)); print(firstdiff)
