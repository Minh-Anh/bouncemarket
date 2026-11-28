"""The web app: every address the server answers at, and what runs when it starts and stops."""
import asyncio
import contextlib

import httpx
from starlette.applications import Starlette
from starlette.routing import Route

from bouncemarket import cards, config, handover, leaderboard, limits, round_log, state
from bouncemarket.web import admin, pages, play, share


@contextlib.asynccontextmanager
async def lifespan(app):
    """When the server starts: load the leaderboard, start saving the round log and the cards,
    and either wait to be handed the game by the server before us (see handover.py) or,
    if there isn't one, start the round loop and the broadcaster straight away, even if the
    leaderboard takes a few tries to load.

    When it stops: if it still has the game, try once more to hand it to a new server, then
    save what is left to save.
    """
    jobs = [leaderboard.load_ranking(), cards.load_card_holders(), round_log.save_rounds_forever(), cards.save_cards_forever(), limits.log_streams()]
    tasks = [asyncio.create_task(job) for job in jobs]
    predecessor = await handover.find_predecessor()
    if predecessor:
        print(f"Waiting to be handed the game by the old server, {predecessor}.")
        tasks.append(asyncio.create_task(handover.keep_saying_hello()))
    else:
        handover.start_afresh()
    yield
    if state.watching:
        tasks.append(state.watching)
    for task in tasks + state.game_tasks:
        task.cancel()
    await asyncio.gather(*tasks, *state.game_tasks, return_exceptions=True)  # all stopped, so nothing changes under the handover
    if config.PUBLIC_URL and state.game["role"] == "live":
        with contextlib.suppress(Exception):  # whatever goes wrong, still save what is left below
            await handover.hand_over(config.LAST_CHANCE_SECONDS)
    for save, unsaved in ((round_log.save_rounds, state.unsaved_rounds), (cards.save_cards, state.unsaved_cards)):
        if unsaved:
            with contextlib.suppress(httpx.HTTPError, KeyError):
                await save()


app = Starlette(
    routes=[
        Route("/", pages.index),
        Route("/board", pages.board_geometry),
        *(Route("/" + name, pages.asset) for name in pages.ASSETS),
        Route("/r/{share_id}.png", share.share_image),  # before /r/{share_id}, which would take "<id>.png" for an id
        Route("/r/{share_id}/replay", share.share_replay),
        Route("/r/{share_id}", share.share_page),
        Route("/health", handover.health),
        # ours() keeps these to the one server that has the game: see handover.py.
        Route("/events", handover.ours(play.events)),
        Route("/buy", handover.ours(play.buy), methods=["POST"]),
        Route("/name", handover.ours(play.set_name), methods=["POST"]),
        Route("/reset", handover.ours(play.reset_points), methods=["POST"]),
        Route("/card/clear", handover.ours(play.clear_card), methods=["POST"]),
        Route("/admin/names", handover.ours(admin.admin_names)),
        Route("/admin/rename", handover.ours(admin.admin_rename), methods=["POST"]),
        Route("/admin/unlock", handover.ours(admin.admin_unlock), methods=["POST"]),
        Route("/admin/notice", handover.ours(admin.admin_notice), methods=["POST"]),
        Route("/handover/hello", handover.handover_hello, methods=["POST"]),
        Route("/handover/take", handover.handover_take, methods=["POST"]),
    ],
    lifespan=lifespan,
)
