"""The game page, the files it and the share page load, and the board they draw.

The files are in frontend/ (see its README).
"""
import pathlib

from starlette.responses import JSONResponse
from starlette.staticfiles import StaticFiles

from bouncemarket import config
from bouncemarket.game import board

FRONTEND = pathlib.Path(__file__).resolve().parents[2] / "frontend"
# The files a page may load, each answered at /<name>. share.html isn't one: share_page()
# (web/share.py) fills it in for each card.
ASSETS = ("board.js", "card.css", "card.js", "game.css", "game.js", "replay.js", "share.css", "share.js")

files = StaticFiles(directory=FRONTEND)


async def frontend_file(name, request):
    """One file from frontend/. The browser may keep it, but asks every time whether it has
    changed ("no-cache") and is sent it again only if it has. So after an update no page runs
    the new index.html with a script it kept from before."""
    response = await files.get_response(name, request.scope)
    response.headers["Cache-Control"] = "no-cache"
    return response


async def index(request):
    return await frontend_file("index.html", request)


async def asset(request):
    return await frontend_file(request.url.path.removeprefix("/"), request)


async def board_geometry(request):
    """What the pages need to draw the board (see frontend/board.js)."""
    return JSONResponse({
        "width": board.WIDTH,
        "height": board.HEIGHT,
        "ball_r": board.BALL_R,
        "pin_r": board.PIN_R,
        "last_row_y": board.LAST_ROW_Y,
        "pins": board.PINS,
        "wall_slant": board.WALL_SLANT,
        "countdown_seconds": config.COUNTDOWN_SECONDS,  # the page fades the landing highlight over this long
    })
