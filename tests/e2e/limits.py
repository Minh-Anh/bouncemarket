"""Test of the limits on buys per internet address, on resets and on new guests per address
(see docs/limits.md), with the real app served by uvicorn in this process and a fake database
(nothing reaches Supabase). Players say which address they come from in the CF-Connecting-IP
header, as Cloudflare does on Render.

    uv run -m tests.e2e.limits
"""
import asyncio
import json
import os
import uuid

os.environ["SUPABASE_URL"] = "http://fake-db.invalid"
os.environ["SUPABASE_KEY"] = "test-key"
os.environ.pop("PUBLIC_URL", None)
os.environ.pop("RENDER_EXTERNAL_URL", None)
os.environ.update(MAX_BUYS_PER_ADDRESS="5", RESET_COOLDOWN_SECONDS="600", MAX_RESETS_PER_ADDRESS="3", MAX_NEW_GUESTS_PER_ADDRESS="6")  # small, to reach them quickly

import httpx
import uvicorn

from bouncemarket import config, db, handover, market, state
from bouncemarket.web.app import app


def fake_db(request: httpx.Request):
    if request.method == "GET":
        return httpx.Response(200, json=[])
    if request.url.path.endswith("/player_keys"):
        return httpx.Response(201, json=[{"id": row["id"], "key": i + 1} for i, row in enumerate(json.loads(request.content))])
    return httpx.Response(201, json=[])


db.client = httpx.AsyncClient(base_url="http://fake-db.invalid/rest/v1", transport=httpx.MockTransport(fake_db))
config.COUNTDOWN_SECONDS = 1
PORT = 8798
BASE = f"http://127.0.0.1:{PORT}"
HOME, OTHER = "203.0.113.7", "198.51.100.9"  # addresses kept for examples, which no real visitor has


async def page(client, player, address):
    """Keep a page open, so the server knows the player."""
    async with client.stream("GET", f"{BASE}/events", params={"player": player}, headers={"cf-connecting-ip": address}, timeout=None) as response:
        async for _ in response.aiter_lines():
            pass


async def main():
    assert (config.MAX_BUYS_PER_ADDRESS, config.RESET_COOLDOWN_SECONDS, config.MAX_RESETS_PER_ADDRESS, config.MAX_NEW_GUESTS_PER_ADDRESS) == (5, 600, 3, 6)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning", lifespan="on"))
    serve = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    home = [str(uuid.uuid4()) for _ in range(5)]
    other = str(uuid.uuid4())
    async with httpx.AsyncClient() as client:
        pages = [asyncio.create_task(page(client, p, HOME)) for p in home] + [asyncio.create_task(page(client, other, OTHER))]
        while len(state.players) < 6:
            await asyncio.sleep(0.05)

        def buy(player, address, side="left"):
            headers = {"cf-connecting-ip": address} if address else {}
            return client.post(f"{BASE}/buy", json={"player": player, "side": side}, headers=headers)

        # Buys: 8 at once from five players at one address, which may make 5 in any second between them.
        while not market.betting_open():
            await asyncio.sleep(0.02)
        answers = await asyncio.gather(*(buy(home[i % 5], HOME) for i in range(8)))
        codes = sorted(r.status_code for r in answers)
        assert codes == [200] * 5 + [429] * 3, codes
        assert all("network" in r.json()["error"] for r in answers if r.status_code == 429)
        print("8 buys at once from one address: 5 taken, 3 refused for the address")
        # Another address isn't held up by that one.
        r = await buy(other, OTHER)
        assert r.status_code == 200, r.text
        print("a buy from another address in the same moment is taken")
        # One player alone still has the player limit, 10 a second, with the address limit off.
        config.MAX_BUYS_PER_ADDRESS = 0
        await asyncio.sleep(1.05)
        if market.betting_open():
            answers = await asyncio.gather(*(buy(home[0], HOME, "right") for _ in range(12)))
            assert sorted(r.status_code for r in answers) == [200] * 10 + [429] * 2
            print("with the address limit off, one player still gets 10 a second")
        config.MAX_BUYS_PER_ADDRESS = 5

        # Resets: wait for a round in which the players hold no shares.
        while any(state.players[p]["spent"] for p in home + [other]):
            await asyncio.sleep(0.05)

        def reset(player, address):
            return client.post(f"{BASE}/reset", json={"player": player}, headers={"cf-connecting-ip": address})

        r = await reset(home[0], HOME)
        assert r.status_code == 200 and r.json()["points"] == 10, r.text
        r = await reset(home[0], HOME)
        assert r.status_code == 429 and r.json()["error"] == "you can reset again in 10 minutes.", r.text
        print("a player's second reset is refused:", r.json()["error"])
        for p in home[1:3]:
            r = await reset(p, HOME)
            assert r.status_code == 200, r.text
        r = await reset(home[3], HOME)
        assert r.status_code == 429 and "too many resets from your network" in r.json()["error"], r.text
        print("the address's fourth reset in the hour is refused:", r.json()["error"])
        r = await reset(other, OTHER)
        assert r.status_code == 200, r.text
        print("a player at another address can still reset")
        # A refused reset doesn't use up the player's or the address's allowance.
        assert state.players[home[3]]["last_reset"] == 0 and len(state.recent_resets[HOME]) == 3
        # The cooldown is carried over in an update with the player.
        assert json.loads(json.dumps(handover.handover_snapshot()))["players"][home[0]]["last_reset"] > 0
        print("the cooldown goes with the player in a handover")

        # New guests: five have come from HOME already, and it may have 6 in an hour.
        def connect(player, address):
            return client.get(f"{BASE}/events", params={"player": player}, headers={"cf-connecting-ip": address}, timeout=5)

        sixth, seventh = str(uuid.uuid4()), str(uuid.uuid4())
        pages.append(asyncio.create_task(page(client, sixth, HOME)))
        while sixth not in state.players:
            await asyncio.sleep(0.05)
        print("the address's 6th new guest in the hour gets in")
        slots_before = state.slots[HOME]
        r = await connect(seventh, HOME)
        assert r.status_code == 429 and r.json()["error"] == "too many new players from your network lately.", (r.status_code, r.text)
        assert seventh not in state.players and state.slots[HOME] == slots_before and len(state.recent_new_guests[HOME]) == 6
        print("its 7th is turned away, isn't kept as a player, and gives its page's slot back")
        # A guest already known comes back in, and a new guest from another address is let in.
        returning = asyncio.create_task(page(client, home[4], HOME))
        newcomer = str(uuid.uuid4())
        pages += [returning, asyncio.create_task(page(client, newcomer, OTHER))]
        while newcomer not in state.players:
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.2)
        assert not returning.done(), "a known guest was turned away"
        print("a known guest from the same address still gets in, and a new one from another address")

        for task in pages:
            task.cancel()
    server.should_exit = True
    await serve
    print("all checks passed")


asyncio.run(main())
