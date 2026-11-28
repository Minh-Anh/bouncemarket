"""End-to-end test of the round log, with the real app served by uvicorn in this process and
a fake database (nothing reaches Supabase). Six pages buy over two rounds, and it checks the
timing, the shuffled send order, the replay of start_x with its hidden nudges (and that the
drop differs without them), that no page is sent the nudge seed, the handover snapshot, a
round handed over by a server from before the nudges, and a save mixing rounds with and
without them.

    uv run -m tests.e2e.round_log [--timing-column] [--no-nudge-columns]

Without --timing-column the fake database lacks the timing column, and with
--no-nudge-columns the nudge columns, to test that rounds are still saved.
"""
import asyncio
import json
import os
import random
import sys
import uuid

os.environ["SUPABASE_URL"] = "http://fake-db.invalid"
os.environ["SUPABASE_KEY"] = "test-key"
os.environ.pop("PUBLIC_URL", None)
os.environ.pop("RENDER_EXTERNAL_URL", None)

import httpx
import uvicorn

from bouncemarket import broadcast, config, db, handover, packing, role, round_log, state, streams
from bouncemarket.game import physics
from bouncemarket.web.app import app

saved_rounds = []
# Without --timing-column, the fake database starts without the timing column, to test that
# rounds are still saved (without their timing). With it, rounds are saved with their timing.
# With --no-nudge-columns, it is also without the nudge columns.
mode = {"timing_column": "--timing-column" in sys.argv, "nudge_columns": "--no-nudge-columns" not in sys.argv}
next_key = [1]


def fake_db(request: httpx.Request):
    path = request.url.path
    if request.method == "GET":
        return httpx.Response(200, json=[])
    if path.endswith("/player_keys"):
        rows = json.loads(request.content)
        out = []
        for row in rows:
            out.append({"id": row["id"], "key": next_key[0]})
            next_key[0] += 1
        return httpx.Response(201, json=out)
    if path.endswith("/rounds"):
        rows = json.loads(request.content)
        if len({tuple(sorted(row)) for row in rows}) > 1:  # as PostgREST: every row sent at once has the same keys
            return httpx.Response(400, json={"code": "PGRST102", "message": "All object keys must match"})
        if not mode["timing_column"] and any("timing" in row for row in rows):
            return httpx.Response(400, json={"code": "PGRST204", "message": "Could not find the 'timing' column of 'rounds' in the schema cache"})
        if not mode["nudge_columns"] and any("nudge_seed" in row for row in rows):
            return httpx.Response(400, json={"code": "PGRST204", "message": "Could not find the 'nudge_seed' column of 'rounds' in the schema cache"})
        saved_rounds.extend(rows)
        return httpx.Response(201, json=[])
    return httpx.Response(201, json=[])


db.client = httpx.AsyncClient(base_url="http://fake-db.invalid/rest/v1", transport=httpx.MockTransport(fake_db))
config.COUNTDOWN_SECONDS = 1
made_logs = []
make_entry = round_log.entry


def capture_round_log():
    log = make_entry()
    if log:
        made_logs.append(log)
    return log


round_log.entry = capture_round_log
PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"
results = {"snapshot_ok": None, "game_messages": 0, "shuffle_orders": set()}


async def page(client, player, buys_wanted, stop):
    """One page: read the stream, and buy a few times while betting is open."""
    bought = 0
    round_seen = None
    async with client.stream("GET", f"{BASE}/events", params={"player": player}, timeout=None) as response:
        event = None
        async for line in response.aiter_lines():
            if stop.is_set():
                return
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: ") and event == "game":
                message = json.loads(line[6:])
                results["game_messages"] += 1
                if state.game["started_at"] != round_seen:
                    round_seen, bought = state.game["started_at"], 0
                if message.get("dropping") and message.get("betting_open") and bought < buys_wanted:
                    bought += 1
                    r = await client.post(f"{BASE}/buy", json={"player": player, "side": random.choice(["left", "right"]), "frame": message.get("frame")})
                    assert r.status_code in (200, 429), r.text


async def main():
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning", lifespan="on"))
    serve = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    stop = asyncio.Event()
    players = [str(uuid.uuid4()) for _ in range(6)]
    async with httpx.AsyncClient() as client:
        tasks = [asyncio.create_task(page(client, p, 4, stop)) for p in players]
        # record send orders by peeking at the order queues were filled, via the streams dict
        original_send = streams.send
        order = []

        def spy(queue, message, tag=None):
            if tag is not None:
                order.append((tag, state.streams.get(queue)))
            return original_send(queue, message, tag)
        streams.send = spy
        # wait for one drop to be under way, check the handover snapshot serialises
        while not state.game["dropping"] or not state.game["buys"]:
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.5)
        snapshot = json.loads(json.dumps(handover.handover_snapshot()))
        results["snapshot_ok"] = True
        assert state.game["nudge_seed"] is not None and state.game["nudge"] == config.NUDGE
        # Nothing a page is sent while the ball falls carries the seed.
        assert str(state.game["nudge_seed"]) not in broadcast.catch_up() + broadcast.parting_message(), "the seed reached a page"
        # A server from before the nudges hands over its round in flight, and a round log, without nudge keys.
        old = json.loads(json.dumps(snapshot))
        for key in ("nudge", "nudge_seed"):
            old["game"].pop(key)
        old["from"] = "old-server"
        # wait for two rounds to be logged and saved (the first save hits the missing column)
        for _ in range(400):
            if len(saved_rounds) >= 2 and len(made_logs) >= 2:
                break
            if len(saved_rounds) >= 1:
                mode["timing_column"] = True  # pretend schema.sql was run; the server keeps saving without timing until restart
            await asyncio.sleep(0.1)
        stop.set()
        for t in tasks:
            t.cancel()
        streams.send = original_send
        by_broadcast = {}
        for tag, player in order:
            by_broadcast.setdefault(tag, []).append(player)
        results["shuffle_orders"] = {tuple(v) for v in by_broadcast.values() if len(v) == len(players)}
    server.should_exit = True
    await serve

    missing_seen = sorted(state.missing_round_columns) or "none"  # before the batch check below clears them
    # Take the old server's round over: it carries on without nudges, and its round log saves in
    # one batch with a round of ours, which has them.
    for task in state.game_tasks:
        task.cancel()
    state.game_tasks.clear()
    role.set_role("waiting")
    handover.take_over(old)
    assert state.game["nudge"] == 0.0 and state.game["nudge_seed"] is None and state.game["path"] == old["game"]["path"]
    for task in state.game_tasks:
        task.cancel()
    print("a round handed over from a server without nudges carries on without them")
    old_log = {key: value for key, value in made_logs[0].items() if key not in ("nudge", "nudge_seed")}
    before = len(saved_rounds)
    mode.update(timing_column=True, nudge_columns=True)
    state.missing_round_columns.clear()
    state.unsaved_rounds[:] = [{**old_log, "started_at": "2000-01-01T00:00:00+00:00"}, made_logs[-1]]
    await round_log.save_rounds()
    assert len(saved_rounds) == before + 2 and saved_rounds[-2]["nudge_seed"] is None and saved_rounds[-2]["nudge"] == 0.0
    print("a batch mixing rounds with and without nudges saves")

    print("saved rounds:", len(saved_rounds), "| columns found missing:", missing_seen,
          "| saved with timing:", sum("timing" in r for r in saved_rounds), "| with nudge seed:", sum("nudge_seed" in r for r in saved_rounds))
    print("handover snapshot JSON-serialisable:", results["snapshot_ok"])
    print("distinct send orders across", len(by_broadcast), "broadcasts with all pages:", len(results["shuffle_orders"]))

    # Pack and unpack the timing of the round in memory / the last log made
    logs = made_logs
    assert logs, "no round was logged"
    for log in logs:
        timing = packing.unpack_timing(bytes.fromhex(log["timing"][2:]))
        n_b, n_buys = len(timing["broadcasts"]), len(timing["buys"])
        print(f"round log: {len(log['buys'])} buys, timing has {n_buys} buys and {n_b} broadcasts")
        for b in timing["broadcasts"][:4]:
            print("  broadcast built %.4fs  first %.4fs  last %.4fs  queued %d written %d" % (b[0], b[1] or -1, b[2] or -1, b[3], b[4]))
        for (frame, *_), (taken, seen, idx) in list(zip(log["buys"], timing["buys"]))[:5]:
            built = timing["broadcasts"][idx][0] if idx is not None else None
            print(f"  buy frame {frame:3d}: taken {taken:.4f}s (frame {taken * 60:.1f}), seen broadcast {idx} written {seen and round(seen, 4)}s, "
                  f"answer {seen and round((taken - seen) * 1000, 2)} ms, send delay {built is not None and round((seen - built) * 1000, 3)} ms")
        assert n_buys == len(log["buys"])
        assert all(t is not None for t, s, i in timing["buys"])
        # start_x: 15 digits, round-trips through a 15-digit print, and replays to the same path
        sx = log["start_x"]
        assert float(f"{sx:.15g}") == sx, sx
        path = packing.unpack_path(bytes.fromhex(log["path"][2:]))
        states, bucket = physics.replay_drop(log)
        assert physics.positions(states) == path and bucket == log["bucket"]
        print("start_x", repr(sx), "round-trips through 15 digits and replays exactly, with nudge seed", log["nudge_seed"])
        # Without its seed, the drop is a different one: the nudges are really there.
        plain, _ = physics.simulate_drop(sx, log["gravity"], log["bounce"])
        assert physics.positions(plain) != path, "the drop without nudges is the same"
    assert len({log["nudge_seed"] for log in logs}) == len(logs), "two rounds had the same seed"
    # Save-format check: what was written to the fake database unpacks
    with_timing = [r for r in saved_rounds if "timing" in r and r["timing"]]
    for r in with_timing:
        t = packing.unpack_timing(bytes.fromhex(r["timing"][2:]))
        assert len(t["buys"]) == r["n_buys"]
    print("all checks passed")


asyncio.run(main())
