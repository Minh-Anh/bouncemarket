# /// script
# dependencies = ["httpx==0.28.1", "pillow==12.0.0", "starlette==1.10.0", "uvicorn[standard]==0.54.0"]
# ///
"""Fetch the most recent N rounds (unpacked, with replayed states and counterparty buys) and the
name map into a pickle, so the analysis can run repeatedly without hitting the database."""
import pickle
import os
import sys

sys.path.insert(0, os.environ.get("BOUNCEMARKET_CODE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))  # the app.py to use: this repo, or BOUNCEMARKET_CODE
import analysis
import app

n, out = int(sys.argv[1]), sys.argv[2]
db = analysis.connect()
rows = []
params = {"select": "*", "order": "id.desc", "limit": 40}
while len(rows) < n:
    r = db.get("/rounds", params=params)
    r.raise_for_status()
    page = r.json()
    if not page:
        break
    rows += page
    params["id"] = f"lt.{page[-1]['id']}"
rows = rows[:n][::-1]
for row in rows:
    row["path"] = app.unpack_path(analysis.from_bytea(row["path"]))
    row["buys"] = app.unpack_buys(analysis.from_bytea(row["buys"]))
    states, bucket = app.replay_drop(row)
    row["states"] = states if app.positions(states) == row["path"] and bucket == row["bucket"] else None
    row["bot_buys"] = analysis.bot_buys(row)
    row["timing"] = app.unpack_timing(analysis.from_bytea(row["timing"])) if row.get("timing") else None
pickle.dump({"rows": rows, "names": analysis.names(db)}, open(out, "wb"))
print(len(rows), "rounds", rows[0]["started_at"], "..", rows[-1]["started_at"])
