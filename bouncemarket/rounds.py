"""The round loop: a countdown, a ball drops, it lands, and every share on its side is paid."""
import asyncio
import random
import time

import httpx

from bouncemarket import cards, config, leaderboard, players, role, round_log, state, streams
from bouncemarket.game import board, counterparty, physics

# Where each ball drops from comes from the operating system's randomness, which can't be
# predicted. Python's own random numbers (a Mersenne Twister) can: anyone who sees enough of
# them can work out every one to come, and the whole drop follows from where the ball starts.
# Players tried exactly that, working start_x out from the paths (see
# docs/randomness.md).
drop_random = random.SystemRandom()


async def run_rounds():
    """The round loop: wait for the countdown, drop a ball, wait for it to land, pay out. Forever,
    or until the game is handed to a new server (see handover.py).

    It picks up from wherever the game is, as a round handed over by the server before may
    already have its ball in the air: then it starts by waiting for that ball to land.
    """
    while True:
        if not state.game["dropping"]:
            await asyncio.sleep(max(0, state.game["drops_at"] - time.time()))
            if not await role.still_ours():
                return
            # Drop from somewhere in the middle two-thirds, so the ball never just slides down a slanted wall.
            # Rounded to 15 significant digits, as that is all the database gives back: a
            # double needs 17 to come back exactly, and the path is chaotic, so a replay from
            # the 15 the round log holds would part from the real drop a few hundred frames in.
            # Any number of 15 digits does come back exactly, so this is the drop that was played.
            start_x = float(f"{drop_random.uniform(board.WIDTH / 6, board.WIDTH * 5 / 6):.15g}")
            # 63 bits, so it fits the database's bigint. Nobody could try that many seeds against
            # the path in the few seconds a drop lasts.
            nudge_seed = drop_random.getrandbits(63)
            states, bucket = physics.simulate_drop(start_x, config.GRAVITY, config.BOUNCE, config.NUDGE, nudge_seed)
            path = physics.positions(states)
            last_row_frame = next(i for i, (x, y) in enumerate(path) if y > board.LAST_ROW_Y)
            # Pages see BUFFER_FRAMES ahead, so betting closes that long before the ball
            # passes the last row. Nobody can bet after seeing which side of the divider it took.
            close_frame = max(0, last_row_frame - config.BUFFER_FRAMES)
            state.game.update(
                dropping=True,
                started_at=time.time(),
                start_x=start_x,
                path=path,
                bucket=bucket,
                close_frame=close_frame,
                commit=config.COMMIT,
                gravity=config.GRAVITY,
                bounce=config.BOUNCE,
                nudge=config.NUDGE,
                nudge_seed=nudge_seed,
                counterparty=counterparty.Counterparty(path, close_frame, start_x),
                sent_frames=0,
                buys=[],
                broadcasts=[],
                buy_timing=[],
            )
            state.written_to.clear()
        await asyncio.sleep(max(0, state.game["started_at"] + len(state.game["path"]) * physics.FRAME_SECONDS - time.time()))
        if not await role.still_ours():
            return  # the new server has the round now, and pays it out
        rows = settle()
        cards.new_best_rounds()
        log = round_log.entry()
        if log:
            # save_rounds_forever() writes it to the database in its own time, so a slow database never holds up the game.
            state.unsaved_rounds.append(log)
            del state.unsaved_rounds[:-config.UNSAVED_ROUNDS_KEPT]
        state.game["dropping"] = False
        state.game["drops_at"] = time.time() + config.COUNTDOWN_SECONDS
        try:
            async with state.saving_players:
                for row in rows:
                    row["name"] = state.players[row["id"]]["name"]  # they may have changed it since settle() made the row
                await players.save_players(rows)
        except httpx.HTTPError as error:
            # The game carries on. These players' points are saved after the next round they play.
            print("Could not save points:", repr(error))
        leaderboard.publish_leaderboard()


def settle():
    """Pay 1 point for every share on the bucket the ball landed in, and tell each page its result.

    Players paid for their shares when they bought them. The counterparty is on the other
    side of every share: it kept what players paid, and it pays out the winners.
    Returns the database rows that need saving: the named players who played this round.
    """
    bucket = state.game["bucket"]
    rows = []
    for player_id, player in state.players.items():
        payout = player["shares"][bucket]
        if player["name"] and player["spent"] > 0:
            rows.append({"id": player_id, "name": player["name"], "points": round(player["points"] + payout, 2)})
        player["last_won"] = round(payout - player["spent"], 2)
        player["points"] = round(player["points"] + payout, 2)
        player["shares"] = {"left": 0, "right": 0}
        player["spent"] = 0

    state.game["shares"] = {"left": 0, "right": 0}
    state.game["last_bucket"] = bucket
    state.recent_buys.clear()  # nobody can buy between rounds, so nothing in it is still in the last second
    state.recent_address_buys.clear()
    # Forget the addresses whose last reset, or last new guest, was over an hour ago, so the lists don't only grow.
    long_ago = time.time() - 3600
    for recent in (state.recent_resets, state.recent_new_guests):
        for address in [address for address, times in recent.items() if not times or times[-1] <= long_ago]:
            del recent[address]
    for row in rows:
        state.named_points[row["name"]] = row["points"]
    for queue, player_id in state.streams.items():
        streams.send(queue, streams.sse("me", state.players[player_id]))
    return rows
