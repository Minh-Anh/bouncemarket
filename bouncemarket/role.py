"""Which server has the game. During an update two servers run side by side for a minute, and
the game lives in one's memory at a time: state.game["role"] says whether it is this one's.
handover.py moves the game from one to the other.
"""
import random
import secrets

from starlette.responses import JSONResponse, Response

from bouncemarket import config, state

INSTANCE = secrets.token_urlsafe(8)  # this running server's own name, made up afresh every time it starts


def set_role(role):
    state.game["role"] = role
    if role in ("live", "retired"):
        state.role_settled.set()
    else:
        state.role_settled.clear()


async def still_ours():
    """Whether the game is still this server's to run. The round loop asks before each drop
    and each payout; while a handover is under way it waits here to see how it ends."""
    await state.role_settled.wait()
    return state.game["role"] == "live"


def come_back():
    """Tells a page's browser how long to wait before it connects again, in the stream's own
    format. The browser does that by itself whenever its stream ends. Each page gets a different
    wait, by chance, so a crowd doesn't all arrive in the same instant."""
    return f"retry: {random.randint(*config.RECONNECT_MS)}\n\n"


def moved(request):
    """The answer to a request that reached a server the game has left: try again, and the
    request will reach the new one. A stream is told to connect again, and anything else gets a
    503 with "moved", at which the page asks again by itself (post() in frontend/game.js)."""
    if request.url.path == "/events":
        return Response(come_back(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
    return JSONResponse(
        {"error": "the game is moving to a new server. try again in a moment.", "moved": True},
        status_code=503,
        headers={"Retry-After": "1"},
    )
