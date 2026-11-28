"""Every username with its points, whether it's locked, and what today's word filter
(bouncemarket/game/names.py) says about it, written to a JSON file.

    uv run --env-file .env -m analysis.names_scan names.json
"""
import json
import sys

from analysis import reader
from bouncemarket.game import names

db = reader.connect()
rows = list(reader.fetch(db, "players", "id", select="id,name,points,name_locked"))
out = [{"name": r["name"], "points": float(r["points"]), "locked": r["name_locked"], "filter": names.name_problem(r["name"])} for r in rows]
out.sort(key=lambda r: r["name"].lower())
json.dump(out, open(sys.argv[1], "w"), indent=0)
print(len(out), "names;", sum(1 for r in out if r["filter"]), "fail today's filter;", sum(1 for r in out if r["locked"]), "locked")
