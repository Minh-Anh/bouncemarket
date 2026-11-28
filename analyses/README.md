# Analyses

The scripts behind [What players are doing](../README.md#what-players-are-doing). Each reads the round log, through `analysis.py` and `app.py` in the folder above, and prints a table. Run them from the repository's top folder, with the database settings in `.env`:

```
uv run --env-file .env analyses/fetch_rounds.py 600 rounds.pkl     # download the last 600 rounds once
uv run --env-file .env analyses/delay.py rounds.pkl foob,martin    # then study them as often as you like
```

Most work from `rounds.pkl`, a local copy of recent rounds made by `fetch_rounds.py`, so they can be run again and again without asking the database. The copy holds each round's path, buys (with each player's key, not their id), replayed states and counterparty buys, and every name; `.gitignore` keeps it out of git. Where a script takes player names, give them comma-separated; the rest of the players are shown together as "everyone else".

To study rounds played by another version of the code, give its `app.py`, `analysis.py` and `names.py` in a folder of their own, for example `git show e09f030:app.py > old/app.py` for each, and run with `BOUNCEMARKET_CODE=old`.

| Script | What it shows |
| ------ | ------------- |
| `fetch_rounds.py N FILE` | Downloads the last N rounds into FILE, with their timing where they have it. |
| `market_maker.py [SINCE]` | The market maker's profit over the last hour (or since a time such as `2026-11-27T21:59Z`), split exactly into the pricing's built-in loss, rounding and the price limits, and the counterparty's shares; and players' profit by point in the betting window and by price paid. Reads the database itself. |
| `player_profile.py FILE NAMES` | A side-by-side profile of each player: buys, profit, win rate, buys a round, whether they buy both sides, gaps between buys, how far behind the server their page says it is, and how often they buy the side the ball is on. |
| `player_rounds.py FILE NAME` | How one player builds a round: their worst case (whether they're hedging), bursts, whether they buy with or against others' recent buying, how the price moves in the second after, and profit by price paid. |
| `player_phases.py FILE NAME` | One player's buys by point in the betting window, and how their cheap buys sit against the ball. |
| `calibration.py FILE NAME` | How often the favourite wins at each price, early, in the middle and late in a drop; and the player's underdog and favourite buys in each. |
| `delay.py FILE NAMES` | What each buy would have made had it come d frames later: how fast a player's edge decays, and so whether it is a race. |
| `race.py FILE [NAME]` | The race at the start of each drop: who reaches the server first, and the player against the next three fastest. |
| `followers.py FILE NAME [N]` | Who buys the same side within 200 ms after the player's bursts. |
| `buffered.py FILE NAMES` | Whether players lean towards what the buffered frames show, beyond a control of frames not yet sent to anyone. |
| `lookahead.py FILE NAMES` | An earlier, cruder test of the same: whether a player's side follows the ball's sideways movement over the next frames more than the last. |
| `timing.py FILE NAMES` | From the timing log: how long a broadcast takes to reach every page; the opening race with each player's place in the send order taken out; how fast each player answers broadcasts, and about how many pages they keep open; and profit by how fast a buy answered. |
| `replay_check.py FILE` | Whether the last 60 rounds replay exactly from their `start_x`. |
| `names_scan.py OUT.json` | Every username with its points, whether it's locked, and what today's word filter in `names.py` says about it. Reads the database itself. |
| `timing_e2e_test.py [--timing-column]` | Not an analysis: a test of the timing log. It runs the server in-process against a fake database (nothing reaches Supabase), with six pages buying over two rounds, and checks the timing, the shuffled send order, the replay of `start_x` and the handover snapshot. Without `--timing-column` the fake database lacks the column, to test that rounds are still saved. |

The first study in the README came from rounds played before the timing log; the second, [after the update](../README.md#after-the-update-what-the-timing-log-shows), from rounds with it. Only `timing.py` reads the timing so far; `analysis.py` and `fetch_rounds.py` give it to any that want it (see [Timing](../README.md#timing)).
