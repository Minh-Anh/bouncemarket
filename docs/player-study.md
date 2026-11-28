# What players are doing

A study of the round log on 27 November 2026 (the scripts are in [`analysis/`](../analysis/README.md)), of rounds played under `e09f030` (rules version 3), before the timing column, the shuffled send order and the rounding of `start_x`. The market maker figures are from 273 rounds between 17:28 and 18:28 UTC; the rest from 600 rounds between 18:40 and 20:54 UTC, with about 1,250 players and 1,000 to 1,700 buys a round. Those rounds were played before `start_x` was rounded, so only about one in ten replays exactly: the study used the saved `path` for the ball, and the counterparty worked out again from `start_x` is off by a share in about a third of rounds, which barely moves any figure here.

## The market maker loses a fixed amount each round

It lost 1,315 points in the hour, 4.82 a round, and lost 270 of the 273 rounds. Nearly all of that is built into the pricing. A logarithmic market maker's loss over a round depends only on how far ahead the winning side is at the close: nothing if the two are level, and at most `PRICE_SOFTNESS` × ln 2 = 4.85 points once the price is sure of the winner, which it nearly always is by the close. Each round splits exactly into three parts:

| Part | Points in the hour | A round |
| ---- | ------------------ | ------- |
| The pricing's built-in loss | −1,267 | −4.64 |
| Rounding to the cent, and the 0.01 and 0.99 limits | −43 | −0.16 |
| The counterparty's shares (it neither pays for them nor collects on them) | −5 | −0.02 |

So the house gives away about 4.8 points every round anyone trades: about 1,300 points an hour into players' balances at this pace, 0.7% of the points that change hands. It is left that way on purpose. A fee of a cent a share would turn it round, and a lower `PRICE_SOFTNESS` shrinks it in proportion, at the cost of each share moving the price more.

## The crowd misprices the round, in a way that can be predicted

Early in a drop the crowd piles onto whichever side the ball is on, though high up that says little: the board is chaotic. Late in a drop the price is slow to believe what is plain. The favourite's price, sampled every 6 frames, against how often the favourite won:

| Price | First third of the betting window | Middle third | Last third |
| ----- | --------------------------------- | ------------ | ---------- |
| about 0.74 | won 0.60 | 0.70 | 0.81 |
| about 0.84 | 0.59 | 0.80 | 0.94 |
| 0.95 to 0.99 | 0.76 | 0.98 | 1.00 |

Taken over all buys, shares bought at 0.10 to 0.40 made 0.09 to 0.13 each and shares bought at 0.60 to 0.90 lost about as much.

## The kinds of trader

**A speed trader (foob).** By far the biggest winner: +3,049 points over the 600 rounds, 5.08 a round, about the whole of what the market maker gives away. foob buys the underdog early (+1,029) and in the middle (+888), and the near-certain favourite late, in volume at small margins (+336 from 11,493 buys). It buys both sides in 96% of rounds, but isn't hedging: its worst case averages −22 a round. It makes money at every price, and the price of its side rises about 0.11 in the second after it buys. It is almost certainly a script: up to 10 buys in one frame (exactly `MAX_BUYS_PER_SECOND`, all at once), and about 50 a round.

Its edge is a race. Each buy was valued again as if it had come d frames later, at the price the market had reached by then, leaving out foob's own later buys:

| Delay | 0 | 1 frame (17 ms) | 2 | 3 (50 ms) | 6 (100 ms) | 12 (200 ms) | 30 (0.5 s) | 60 (1 s) |
| ----- | - | --------------- | - | --------- | ---------- | ----------- | ---------- | -------- |
| Profit a share | +0.120 | +0.104 | +0.079 | +0.061 | +0.042 | +0.018 | +0.066 | +0.049 |

Half of it is gone in 50 ms and 85% in 200 ms. Some comes back after half a second, as the crowd that rushed in behind overshoots and the price falls back: that slower part is the mispricing above.

foob is also first to the server. At the start of a drop, when every page hears of it from the same broadcast, foob's first buy arrived before any other player's in 63% of rounds, typically at frame 5 and never at frame 0 (so it answers the broadcast; it doesn't time its request from the countdown), 33 to 100 ms ahead of the next fastest (switcharoo-alpha, kitsu_test, clawde). In a tenth of rounds its whole loop, from the broadcast to its buy arriving, took about 50 ms or less. Without the timing log, its network, its code and its place in the old fixed send order (the order pages connected) can't be told apart; the timing log and the shuffled order were added to settle that (see [Timing](round-log.md#timing)).

**A pack that follows within 200 ms.** In the 200 ms after one of foob's bursts, other players bought 137,615 shares on its side and 86,635 on the other. The fastest followers nearly always take its side: doober (84%), goober (85%), hydrogen (88%), prrrrrrrrrrrrr (91%), clawde (77%), martin (92%), Zone (100%), tokenmaxer_bot (98%), Precogbot (96%). For doober, hydrogen and prrrrrrrrrrrrr these are 40 to 50% of all they buy. The log can't tell whether they react to the same signal a moment later or chase foob's move on the price.

**Slow-edge traders (martin, tokenmaxer_bot).** martin made +1,334 points (98.7% of shares won, at an average price of 0.92), tokenmaxer_bot +337. Their edge doesn't decay with delay; it grows (martin +0.081 a share at once, +0.112 a second later), as they tend to buy just after foob's bursts, at the top of the move, and then the price falls back. They win by being right, not by being fast.

**Traders reading the buffered frames, a little.** Early in a drop the stream carries positions 48 frames (0.8 s) ahead of the server's frame, and a normal page runs about 12 behind it, so a script reading the stream itself sees the ball's near future. To look for that, a model of the chance of landing left from the ball's position and sideways speed (a grid, learned on half the rounds and tested on the other half) gave the change in chance for the side bought over three steps: from what a page shows to the server's frame, from there to the newest frame sent, and, as a control, from there to frames not yet sent to anyone. Leaning towards the last can only be forecasting; leaning more towards the frames sent than towards the control is a sign of reading them. The model does predict better from the frames sent than from the server's frame.

| Player | Page → server | Server → newest sent | Newest sent → not yet sent (control) | Lean on the frames sent, beyond the control |
| ------ | ------------- | -------------------- | ------------------------------------ | -------------------------------------------- |
| Zone | 53.1% | 59.1% | 55.6% | +3.5 |
| Precogbot | 54.3% | 58.1% | 55.0% | +3.1 |
| martin | 53.5% | 58.0% | 55.0% | +3.0 |
| doober | 52.4% | 55.7% | 53.5% | +2.2 |
| tokenmaxer_bot | 54.2% | 58.2% | 56.8% | +1.4 |
| foob | 52.6% | 54.2% | 54.2% | 0.0 |
| prrrrrrrrrrrrr | 56.2% | 54.3% | 58.9% | −4.6 |
| everyone else | 49.5% | 49.3% | 49.5% | |

Zone, Precogbot and martin lean about 3 points more towards the frames they've been sent than towards ones nobody has, which fits reading the stream, but most of their lean is forecasting, and with buys coming in bursts a gap that size could be noise. foob shows none of it: it is fast, not peeking. prrrrrrrrrrrrr is the odd one, better at frames not yet sent than at ones it has, which fits running the physics forward. The lookahead was kept at 48 frames: trading on the buffered frames is part of the game. Comparing the excess early in a drop (48 frames ahead) with the final stretch (12 ahead) would show more.

**Everyone else** (all but foob, martin and tokenmaxer_bot) loses slowly: −842 points over 626,319 buys in the 600 rounds, about −0.001 a share, mostly buying the side the ball is on (55% of buys), with no edge at any delay.

## After the update: what the timing log shows

A second study, of the first 141 rounds played under `e621554` (21:59 to 22:29 UTC on 27 November 2026), with the timing log, the shuffled send order and `start_x` rounded: 126,156 buys by 671 players, about 456 pages open. Every round replays exactly. Half an hour is a small sample beside the 600 rounds above, so read the smaller differences with care. The scripts are the same, plus [`timing.py`](../analysis/timing.py).

**The house loses more, through the 0.99 limit.** It lost every round, 7.56 points each: the pricing's built-in loss is the same 4.83, but rounding and the price limits now cost 2.81 a round, against 0.16 before. 58,682 buys, 46% of all of them, were at the 0.99 limit, and 99.8% of those won: +456 points, a cent at a time, for shares the pricing would have charged about 0.998 for. A dozen players do it in volume, all late in the drop and none losing a single one: le-botty, prrrrrrrrrrrrr, hydrogen, Precogbot, Zone, kittens and martin about 4,000 each. For Zone (98%), kittens (99%) and martin (91%) it is nearly all they buy. Neither the pricing nor the limits changed in the update; players have copied what foob did late in a drop. The take is capped only by `MAX_BUYS_PER_SECOND`. Rules version 4 ended it by letting the price reach 1.00. Priced that way, 57,080 of these 58,682 buys would have cost 1.00, and together they would have lost 115 points instead of making 456.

**Who wins.** doober (+416) and foob (+409) are level, then hydrogen (+249), prrrrrrrrrrrrr (+197), hurf (+152), le-botty (+139) and Precogbot (+118). foob makes 2.9 a round, against 5.1 before, and 0.069 a share, against 0.12. martin has nearly stopped making money (+18; +0.005 a share, against +0.08). The cheap side is still where the money is: shares bought at 0.10 to 0.40 made 0.08 to 0.13 each, and at 0.60 to 0.90 lost 0.05 to 0.07. only_left (−253) and only_right always buy the same side.

**A broadcast reaches every page within about 20 ms.** It is written to the first page and the last 19.7 ms apart (median; 90% within 23 ms). So a page's place in the order is now worth at most 20 ms, and on average nothing.

**foob is fast; the send order doesn't explain it.** It still bought first in 73 of the 141 rounds, 59% of those it bought early in (63% before). Its buy reached the server a median 25 ms after the broadcast was written to it, and 13 ms in its fastest tenth: there and back across the network, plus whatever its code takes, in that time. So it sits very near the server and its code adds next to nothing. In 30 of the 134 rounds it played, it let the first broadcast go and bought on a later one. Its lead over doober in the opening race (83 ms) is four times the whole spread of the send order, and the round's first buyer had been written in the first half of the order in only 33 of 139 rounds. The question the first study left open is settled: it is foob's network and code, not its place in the order.

**The money is in the first 40 ms after a broadcast.** Early in a drop a broadcast goes out about every 0.6 s, carrying the next 0.8 s of the ball's path and a price that has not moved since the last one. Profit a share of every buy in that part of the drop, by how long after the broadcast was written to the buyer it was taken:

| Answered within | 0–40 ms | 40–80 ms | 80–150 ms | 150–300 ms | 300–600 ms |
| --------------- | ------- | -------- | --------- | ---------- | ---------- |
| Everyone | +0.057 (11,270 buys) | +0.018 | +0.005 | −0.001 | −0.003 |
| foob | +0.161 (2,008) | +0.034 | +0.054 | −0.026 | +0.053 |
| doober | +0.154 (968) | +0.034 | −0.008 | +0.060 | +0.066 |
| everyone but the nine players studied | +0.004 | −0.001 | −0.006 | −0.013 | −0.018 |

The first 40 ms took about 640 points over the half hour, more than half of everything players made. The price the broadcast showed is stale by the time anyone answers, and the first to answer buy it before it moves. Few answer that fast as a habit. With broadcasts 0.6 s apart, a player who buys at random times has about a sixth of their buys within 100 ms of one by chance. alef has 99% (median 26 ms, but only 321 buys), foob 64% (26 ms), hydrogen 43% (54 ms) and garethgeorge 42% (49 ms). kitsu has 74%, but never under 77 ms: a script on a distant network. Nearly everyone else is at chance.

**Some answer on a timer.** doober's first buy of a drop comes 137 to 230 ms after the write, every round. Its fastest answers later in the drop take 13 ms, so its network is fast and the wait is its own. hurf answers about 156 ms after each broadcast, give or take 8 ms. prrrrrrrrrrrrr's first buy reached the server before any broadcast of the round had been written to it in 20 rounds: it times its first buy off the countdown and buys without seeing anything.

**Some keep several pages open.** When a player has more than one page open, the log keeps the latest write to any of them, so their place in the order looks late. From how late, prrrrrrrrrrrrr keeps about 4.6 pages open, foob 4.1, alef 3.5, le-botty 3.2, tokenmaxer_bot 3.1 and goober 2.9. With the order shuffled, that is the one way to be written early more often, but over 20 ms it is worth only a few milliseconds. It also makes their answers look a few milliseconds quicker than they are. Logging the earliest write as well would measure it.

**hurf's page isn't the game's.** Every page reports the frame it is showing behind the server's frame. hurf's reports one about 29 frames (0.5 s) ahead, so it is showing the buffered frames. But hurf wins on price, not on the future: it buys the cheap side (average price 0.41, won 47%, +0.06 a share), and its edge hardly fades with delay (76% left after 200 ms).

**The sign of reading the buffered frames has gone.** With exact replays, no player leans towards the frames they have been sent any more than towards the frames nobody has: Zone 54.2% against 55.8% for the control, Precogbot 50.4% against 51.6%. The 3-point lean the first study found was most likely noise, as it allowed.

**The pack still follows foob.** In the 200 ms after one of foob's 1,441 bursts, others bought 24,093 shares on its side and 13,593 on the other; garethgeorge (90%), hydrogen (81%) and prrrrrrrrrrrrr (78%) most of all.
