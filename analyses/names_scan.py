# /// script
# dependencies = ["httpx==0.28.1", "pillow==12.0.0", "starlette==1.10.0", "uvicorn[standard]==0.54.0"]
# ///
import os, sys, json
sys.path.insert(0, os.environ.get("BOUNCEMARKET_CODE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))  # the app.py to use: this repo, or BOUNCEMARKET_CODE
import analysis, names
db = analysis.connect()
rows = list(analysis.fetch(db, "players", "id", select="id,name,points,name_locked"))
out = [{"name": r["name"], "points": float(r["points"]), "locked": r["name_locked"], "filter": names.name_problem(r["name"])} for r in rows]
out.sort(key=lambda r: r["name"].lower())
json.dump(out, open(sys.argv[1], "w"), indent=0)
print(len(out), "names;", sum(1 for r in out if r["filter"]), "fail today's filter;", sum(1 for r in out if r["locked"]), "locked")
