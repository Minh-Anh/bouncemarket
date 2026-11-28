"""The public page for one best-round card, /r/<share_id>, with its picture and its replay."""
import asyncio
import html
import json

import httpx
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from bouncemarket import card_image, card_text, cards, config, state
from bouncemarket.game import physics
from bouncemarket.web import pages


def site_origin(request):
    """The address the site was reached at, such as https://bouncemarket.minhanhday.com, for the
    links in a share page's preview tags, which must be whole addresses. Render answers every
    request over https, but passes it on to us as plain http, saying so in X-Forwarded-Proto."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    return f"{scheme}://{request.headers.get('host', request.url.netloc)}"


async def share_page(request):
    """The public page for one card, /r/<share_id>: the card, read-only, and a button to play.
    Anyone can open it, with nothing remembered. Its preview tags give chat and social apps a
    title, a line of text and a picture of the card (see share_image()) for when the link is posted."""
    share_id = request.path_params["share_id"]
    try:
        record = await cards.find_card(share_id)
    except httpx.HTTPError as error:
        print("Could not load a shared card:", repr(error))
        record, status = None, 503
    else:
        status = 200 if record else 404
    origin = site_origin(request)
    if record:
        title = f"+{card_text.two_places(record['profit'])} in one round on bouncemarket"
        description = card_text.what_happened(record) + "."
        image = f"{origin}/r/{share_id}.png"
    else:
        title, description, image = "bouncemarket", "a ball will fall. trade on where it lands.", ""
    message = {404: "this round can't be found.", 503: "this round can't be loaded right now. try again in a minute."}.get(status, "")
    page = (pages.FRONTEND / "share.html").read_text(encoding="utf-8")
    # The card goes in as JSON, with "<" written as < so nothing in it can end the script tag early.
    card = json.dumps(cards.public_record(record) if record else None).replace("<", "\\u003c")
    for key, value in {
        "title": title, "description": description, "url": f"{origin}/r/{share_id}", "image": image, "message": message,
    }.items():
        page = page.replace("{{" + key + "}}", html.escape(value))
    page = page.replace("{{image_tags}}", "" if not record else (
        f'<meta property="og:image" content="{html.escape(image)}">\n'
        '  <meta property="og:image:width" content="1200">\n'
        '  <meta property="og:image:height" content="630">\n'
        f'  <meta property="og:image:alt" content="{html.escape(description)}">\n'
        f'  <meta name="twitter:image" content="{html.escape(image)}">'
    ))
    page = page.replace("{{card}}", card)  # last, so nothing in the card is taken for a {{...}} to fill in
    return HTMLResponse(page, status_code=status, headers={"Cache-Control": "public, max-age=300"})


async def share_image(request):
    """The card as a 1200 by 630 picture, /r/<share_id>.png, for link previews and for the
    share menu's "copy image". Drawn by
    card_image.py the first time it's asked for, in a thread of its own so the game doesn't
    wait, then kept in memory for the next time."""
    share_id = request.path_params["share_id"]
    if share_id not in state.shared_images:
        try:
            record = await cards.find_card(share_id)
        except httpx.HTTPError as error:
            print("Could not load a shared card:", repr(error))
            return PlainTextResponse("try again in a minute", status_code=503)
        if record is None:
            return PlainTextResponse("Not Found", status_code=404)
        # The picture says where to play (SITE, not the address the request came in at: the
        # picture is kept for everyone, so no one request may choose what it says).
        cards.remember(state.shared_images, share_id, await asyncio.to_thread(card_image.render, record, config.SITE), config.IMAGES_KEPT)
    return Response(state.shared_images[share_id], media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


async def share_replay(request):
    """The ball's path for a card's round, /r/<share_id>/replay, as {"path": [[x, y], ...]}, one
    position a frame, for the replay on the share page.

    It isn't stored: the record keeps the start_x, gravity, bounce, nudge and nudge_seed the
    whole path follows from, and the drop is played again here (see replay_drop()), which takes
    a few hundredths of a second. If the physics has changed since, so that the drop no longer
    lasts as long or lands on the same side, the answer is 404 and the share page shows the card
    without a replay.
    """
    share_id = request.path_params["share_id"]
    if share_id not in state.shared_paths:
        try:
            record = await cards.find_card(share_id)
        except httpx.HTTPError as error:
            print("Could not load a shared card:", repr(error))
            return JSONResponse({"error": "try again in a minute."}, status_code=503)
        if record is None:
            return JSONResponse({"error": "no such round."}, status_code=404)
        states, bucket = physics.replay_drop(record["trajectory"])
        if bucket != record["outcome"] or len(states) != record["frames"]:
            return JSONResponse({"error": "this round can't be replayed."}, status_code=404)
        cards.remember(state.shared_paths, share_id, physics.positions(states), config.PATHS_KEPT)
    return JSONResponse({"path": state.shared_paths[share_id]}, headers={"Cache-Control": "public, max-age=86400"})
