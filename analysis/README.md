# Analysis

Reads the [round log](../docs/round-log.md) back out of the database. Run everything from the repository's top folder.

| | |
| --- | --- |
| `reader.py` | Gives each logged round with its path, buys and timing unpacked, the ball's exact state at every frame, and the counterparty's buys, under the rules the round was played by. Everything else here uses it. |
| `export.py` | Writes the whole log as a spreadsheet, one line per buy: `uv run --env-file .env -m analysis.export buys.csv` (or `buys.csv.gz`). The top of the file lists the columns. |
| the rest | The scripts behind [What players are doing](../docs/player-study.md), below. They are working notes more than finished programs. |

## The study scripts

Each prints a table. Most work from `rounds.pkl`, a local copy of recent rounds, so they can be run again and again without asking the database:

```
uv run --env-file .env -m analysis.fetch_rounds 600 rounds.pkl    # download the last 600 rounds once
uv run -m analysis.delay rounds.pkl foob,martin                   # then study them as often as you like
```

The copy holds each round's path, buys (with each player's key, not their id), replayed states, counterparty buys and timing, and every name; `.gitignore` keeps it out of git. Where a script takes player names, give them comma-separated; the rest of the players are shown together as "everyone else". Only the scripts that read the database need `--env-file .env`.

| Script | What it shows |
| ------ | ------------- |
| `fetch_rounds N FILE` | Downloads the last N rounds into FILE. Reads the database. |
| `market_maker [SINCE]` | The market maker's profit over the last hour (or since a time such as `2026-11-27T21:59Z`), split exactly into the pricing's built-in loss, rounding and the price limits, and the counterparty's shares; and players' profit by point in the betting window and by price paid. Reads the database. |
| `names_scan OUT.json` | Every username with its points, whether it's locked, and what today's word filter says about it. Reads the database. |
| `player_profile FILE NAMES` | A side-by-side profile of each player: buys, profit, win rate, buys a round, whether they buy both sides, gaps between buys, how far behind the server their page says it is, and how often they buy the side the ball is on. |
| `player_rounds FILE NAME` | How one player builds a round: their worst case (whether they're hedging), bursts, whether they buy with or against others' recent buying, how the price moves in the second after, and profit by price paid. |
| `player_phases FILE NAME` | One player's buys by point in the betting window, and how their cheap buys sit against the ball. |
| `calibration FILE NAME` | How often the favourite wins at each price, early, in the middle and late in a drop; and the player's underdog and favourite buys in each. |
| `delay FILE NAMES` | What each buy would have made had it come d frames later: how fast a player's edge decays, and so whether it is a race. |
| `race FILE [NAME]` | The race at the start of each drop: who reaches the server first, and the player against the next three fastest. |
| `followers FILE NAME [N]` | Who buys the same side within 200 ms after the player's bursts. |
| `buffered FILE NAMES` | Whether players lean towards what the buffered frames show, beyond a control of frames not yet sent to anyone. |
| `lookahead FILE NAMES` | An earlier, cruder test of the same: whether a player's side follows the ball's sideways movement over the next frames more than the last. |
| `timing FILE NAMES` | From the timing log: how long a broadcast takes to reach every page; the opening race with each player's place in the send order taken out; how fast each player answers broadcasts, and about how many pages they keep open; and profit by how fast a buy answered. |
| `replay_check FILE` | Whether the last 60 rounds replay exactly from their `start_x`. |

## Rounds played by older code

`reader.py` keeps every version of the pricing and the counterparty (`RULES_VERSION`), so their prices and counterparty buys still come out right. The physics is not kept: a round replays exactly only under the commit that played it, its `commit_id`. To study such rounds, check that commit out beside this one and make the copy there, by that commit's own README:

```
git worktree add ../bouncemarket-e09f030 e09f030
```

A `rounds.pkl` made by one version can be read by the scripts of another.
