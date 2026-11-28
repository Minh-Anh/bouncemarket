# The round log

Every round that at least one player bought in is saved to the database, for analysis later: how well the market's odds track the real ones, and how players differ in the way they play. Rounds nobody bought in aren't saved.

Each is one row of the `rounds` table:

| Column | Meaning |
| ------ | ------- |
| `started_at` | When the ball dropped. |
| `commit_id` | The version of the code that dropped the ball (Render's `RENDER_GIT_COMMIT`, or `local`). A round handed over during an update keeps the old server's. |
| `gravity`, `bounce` | The physics options the ball was dropped with. |
| `start_x` | Where along the top the ball dropped from, to 15 significant digits. |
| `nudge`, `nudge_seed` | How big the ball's hidden turns at each pin bounce typically were, in radians, and the seed they were drawn from (see [The hidden nudges](randomness.md#the-hidden-nudges)). Empty for rounds played before them, which had none. Run `db/schema.sql` to add these columns: until then the server saves rounds without them, and they can't be replayed exactly. |
| `frames`, `close_frame` | How many frames (60 a second) the ball took to land, and the frame betting closed at. |
| `bucket` | Where the ball landed. |
| `path` | The ball's position at every frame, packed. |
| `buys`, `n_buys` | Every buy, in order, packed, and how many there are. |
| `format` | How `path` and `buys` are packed (`LOG_FORMAT` in `bouncemarket/packing.py`). |
| `rules` | Which pricing and counterparty rules the round was played by (`RULES_VERSION` in `bouncemarket/game/__init__.py`). Rounds logged before the column existed are 1: each side's price was its fraction of all shares bought, and the counterparty bought one share at a time on whichever half the ball was in. In 2, prices follow the logistic curve and the counterparty is today's pretend traders, but a share was charged the price before the buy. 3 charges the market maker's cost of the buy instead. 4 lets that cost reach 1.00, where 3 stopped at 0.99. `analysis/reader.py` keeps all four, so old rounds still read back right. |
| `timing` | When each broadcast went out and each buy was taken, to the microsecond, packed (see [Timing](#timing)). Empty for rounds logged before the column existed. |

A drop is decided entirely by `start_x`, `gravity`, `bounce`, `nudge` and `nudge_seed` (see [The hidden nudges](randomness.md#the-hidden-nudges)): the same five numbers give exactly the same path on any computer. Rounds played before the nudges have none, and replay without them. So the analysis plays each round again to get the ball's exact position and velocity at every frame, and nothing about the ball is stored per buy. The saved `path` (under a kilobyte) is there to check the replay against, and to fall back on if the physics ever changes. If it does, a round still replays under the version in its `commit_id`.

The database gives `start_x` back to 15 significant digits, where a number like it needs 17 to come back exactly, and the path is chaotic: a replay from 15 digits parts from the real drop a few hundred frames in. So the server rounds `start_x` to 15 digits before it drops the ball, and the drop that is played is the one that is saved. Rounds logged before that was done mostly don't replay to their saved path; `analysis/export.py` says how many, and falls back on the saved path for their positions. Their counterparty buys, worked out again from `start_x`, can be off by a share or two as well.

For each buy, the log keeps the server's frame when it took the buy, the frame the player's page said it was showing when the button was pressed (a little earlier, and what the player was actually looking at), who bought, which side, the price paid, and the player's points just before. The page's frame is the page's word, so it's only good for analysis. Everything else about the market at that moment follows from the buys before it and the path: the counterparty's buys are worked out again from the path, where the ball dropped from and the players' buys (its traders' timing has chance in it, but seeded from where the ball dropped, so it comes out the same), and the price from the shares.

The buys are nearly all of what gets stored (a thousand players buying three shares a round is about 8 million buys in ten hours), so they are packed small: about 7 bytes each, against about 80 as database rows of their own. The price of that is that the database can't search inside them. `pack_buys()` in `bouncemarket/packing.py` describes the layout. In place of each player's long id, a buy holds a small number, the player's key, from the `player_keys` table. Every player gets one the first time they buy, named or not.

Rounds are saved a second or so after they finish, by a separate task, so a slow database never holds up the game. If the database can't be reached, up to 20 rounds wait in memory and are saved when it is back.

## Timing

Frames are a sixtieth of a second, too coarse to tell who answers fastest, so each round also keeps its timing, in microseconds since the drop by the server's clock (`pack_timing()` in `bouncemarket/packing.py`):

- For each broadcast during the drop: when it was made, when it was written to the first page and to the last, and how many pages it was queued for and written to. "Written" is when the server handed it to the network for that page, which comes in the broadcast's random order.
- For each buy, in the same order as the buys: when the server took it, and the newest broadcast of the round that had been written to the buyer's pages by then, and when.

The time from that write to the buy is how long the player took to answer what they had just been sent: their network, there and back, plus however long their own page or program took. It no longer includes where their page came in the order of sending, which is measured separately. A player whose answer is always fast at the start of a drop (when there is nothing to work out but "it has started") has a fast network; one who is also fast once there is a price to weigh has fast code too. The server can't see further into a player's network than that, as Render's proxy stands between them.

The `timing` column must exist before it is saved: run `db/schema.sql` again before pushing. If the new code runs first, rounds are saved without their timing, the server says so in its log, and it tries again with timing after its next restart.

## Reading it back

```
uv run --env-file .env -m analysis.export buys.csv
```

writes one line per buy ever made: the round, the player (their key, and their name if they have one), the frame, the side, the price, their points, the ball's position and velocity at the server's frame and at the frame the page was showing, the shares players and the counterparty had bought on each side just before, where the ball landed, what the share made, and from the round's timing, when the buy was taken to the microsecond, how long the player took to answer the newest broadcast they had been sent, and how long that broadcast took to reach their page in its sending order. End the file name in `.gz` to have it compressed. The top of `analysis/export.py` lists the columns.

It also says if any rounds didn't replay to the path that was saved, or if any buys weren't at the price today's rules would charge. Either means the rules have changed since those rounds were played, or they were played before `start_x` was rounded (see above).

To work with whole rounds in Python instead, `rounds()` in `analysis/reader.py` gives each one with its path, its exact state at every frame, and its buys.
