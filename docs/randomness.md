# Randomness

What a player can't know about a drop until it is over, and how players have tried to find out.

## The hidden nudges

At every pin bounce the ball's direction is turned by a tiny random angle, typically 0.17 degrees either way (`NUDGE` in `bouncemarket/config.py`, 0.003 radians), far too small to see. The angles come from a seed the server draws for each drop from the operating system's randomness, and the seed is kept secret until the ball has landed: no page is ever sent it while betting is open. Once the round is over it is saved with the round and on any card made from it, so the drop can still be replayed exactly.

Without the nudges, the whole drop followed from where the ball started. Players worked that out from the path they were sent, as Precogbot had tried across rounds (see [An attempt on the random numbers](#an-attempt-on-the-random-numbers)), only now within one round. As `start_x` is rounded to 15 significant digits, only so many starting points are possible, and a few hundred frames of path leave one: in 200 rounds on 27–28 November 2026 that took a median of 264 frames, with half the betting window still to go. From there a script knew the winner for certain. In about half of the rounds, the price had settled at 0.90 or more on the winner within a frame or so of the stream delivering the frame that settled it, a median of 34 frames before the server's own frame got there, and never once after. Several players (socramt, prrrrrrrrrrrrr, le-botty, garethgeorge, cubebot-v3, foob) won every share they bought in that gap, and they read the stream up to the newest frame sent, 48 frames ahead. Sending whole pixels instead of tenths would only have put the solve 12 frames or so later.

The path is chaotic, so a turn this small soon makes the rest of the drop impossible to work out from what has been seen of it. In 60 drops made with the nudges, even a script that knew the ball's exact position and velocity at every moment (more than the stream gives) could be 95% sure of the winner only a median of 46 frames before betting closed, against the whole drop without them. By then the stream is only 12 frames ahead, so 17% of the betting window is left once it carries the frame that decides it, against half before. In a first test, with the turns drawn a little differently, bigger turns gained little more (1.7 degrees gave 38 frames) and smaller ones gave back a lot (0.00006 degrees gave 132).

The turn is made with the same + − × ÷ and square roots as the rest of `simulate_drop()` (`bouncemarket/game/physics.py`), and drawn from a generator whose numbers Python promises not to change, so a replay comes out the same on any computer. Rounds played before the nudges have no seed, and replay without them, as they were played. A round handed over during an update by a server from before them carries on without them too.

## An attempt on the random numbers

The player behind Precogbot told the story on Hacker News. They spent about four hours trying to predict where each ball would drop, which would have meant knowing every round's winner before betting opened.

The attack was a real one. Until `e621554`, where the ball dropped from came from Python's `random.uniform()`, and nothing else drew on Python's random numbers between drops (only the owner renaming a player does). Python's random numbers come from a Mersenne Twister. It isn't made to keep secrets: from about 20,000 bits of its output, anyone can work out its whole state, and with it every number to come. Each drop's `start_x` carries 53 of those bits. The ball's path is sent to every page, the physics is in the open and the same everywhere, and the path is chaotic. So searching for the starting point that gives a path, to the tenth of a pixel it is sent at, pins `start_x` down very closely, and they did that well. Knowing the top 40 bits or so of each would take about 500 rounds, two or three hours of play; they planned on 600.

Three things got in their way, none of them meant to:

- **A restart.** Every new server starts the Mersenne Twister afresh from the operating system's randomness, so the rounds they had collected told them nothing about the next ones.
- **`start_x` rounded to 15 significant digits** (`e621554`, for exact replays from the round log). It loses about 5 of the 53 bits, which means collecting more rounds, not giving up.
- **The shuffled send order** (`e621554`, so that no page always hears first). That is what made it hopeless. Each broadcast shuffles the open pages, several hundred of them, with the same Mersenne Twister, up to 10 times a second. Tens of thousands of its numbers now go by between one drop and the next, and how many depends on the numbers themselves.

It never paid off. Had it worked, Precogbot would have bought the winning side early, at about 0.50, nearly every round. Instead its buys in the first third of the betting window won 52 to 75% of the time, hour by hour, at an average of 0.52 to 0.92: ordinary buying of whichever side looked likely. In the half hour after the update, a third of its profit (40 of 118 points) came from the 0.99 limit.

Since then, `start_x` comes from the operating system's randomness (`random.SystemRandom()`), which can't be predicted from anything a player sees. The send order has a Mersenne Twister of its own, seeded the same way, which is far quicker for shuffling a thousand pages ten times a second. Nobody is ever shown the order, and it says nothing about the ball.
