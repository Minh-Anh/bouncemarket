"""Fetch the most recent N rounds (unpacked, with replayed states and counterparty buys) and the
name map into a pickle, so the analysis can run repeatedly without hitting the database.

    uv run --env-file .env -m analysis.fetch_rounds 600 rounds.pkl
"""
import pickle
import sys

from analysis import reader

n, out = int(sys.argv[1]), sys.argv[2]
db = reader.connect()
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
    reader.unpack(row)
pickle.dump({"rows": rows, "names": reader.names(db)}, open(out, "wb"))
print(len(rows), "rounds", rows[0]["started_at"], "..", rows[-1]["started_at"])
