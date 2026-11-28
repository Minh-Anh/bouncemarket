# Limits

What stops one computer slowing the game for everyone, or turning free points into real ones. The code is in `bouncemarket/limits.py`, and in `buy()` and `reset_points()` in `bouncemarket/web/play.py`.

## Limits on open pages

Every open page holds a stream, and every broadcast is written to every stream, so one computer opening thousands of streams slows the game for everyone. Two limits stop that, both settable as environment variables on the host:

| Variable | Default | |
| --- | --- | --- |
| `MAX_STREAMS_PER_ADDRESS` | 20 | The most pages open from one internet address. A household, an office or a classroom shares one address, so it isn't small. A page past the limit gets `429`. `0` turns it off. |
| `MAX_STREAMS` | 1500 | The most pages open at once. Past it a new page gets `503`, and the players already in keep a quick game. Set it to what the server can carry. |

A page that is turned away says so, and asks again by itself every 10 to 20 seconds until there is room.

The server never sees a visitor's address directly, as every connection reaches it from Render's proxy. It reads the address from the `CF-Connecting-IP` header, which Cloudflare (in front of every Render service) sets and a visitor can't forge, and failing that from the first address in `X-Forwarded-For`, which a visitor can forge. An IPv6 address counts as its network (the first half, a /64). With neither header, as on your own computer, pages aren't counted against an address, only against `MAX_STREAMS`.

Once a minute, if anyone was turned away or one address holds many pages, the server says so in the log:

`1203 pages open from 961 addresses (the limits are 1500, and 20 from one address). Most from one address: 203.0.113.7 has 20. Turned away 2950 connections from 3 addresses in the last 60 seconds: 2948 from 203.0.113.7, ...`

A few addresses turned away thousands of times is one person's script. If the line says many pages "came with no address", the headers aren't what the server expects and the limit per address isn't working. The limits are per address, so they don't stop a flood from thousands of different addresses: that needs a filter in front of the server, such as Cloudflare's rate limiting.

## Limits on resets and buys

Every player starts with 10 points, and can reset to 10 whenever they hold no shares. In 1,200 rounds on 28 November 2026, 24 guests spent their 10 points every round, each on 10 shares bought at once, early and on either side, paying 0.75 to 0.85 for shares that won less than half the time, and reset to 10 between rounds. Together they lost 26.5 points a round, about six times what the market maker gives away. Three players made all their profit taking the other side of those bursts, within a few hundredths of a second: before any broadcast could have shown them the burst, and almost never after anyone else's of the same size. Whether one person ran both sides, or the three watched the price some faster way and the guests were someone's careless script, free points were being turned into real ones.

Four limits make that slow and costly, all settable as environment variables on the host, and `0` turns each off:

| Variable | Default | |
| --- | --- | --- |
| `MAX_BUYS_PER_ADDRESS` | 20 | The most buys in any one second from all the players at one internet address together, on top of each player's own 10 (`MAX_BUYS_PER_SECOND`). Past it a buy gets `429`. A household, an office or a classroom shares one address; 20 is two people clicking as fast as anyone can. |
| `RESET_COOLDOWN_SECONDS` | 600 | How long a player waits after a reset before they can reset again. A refused reset says how many minutes are left. |
| `MAX_RESETS_PER_ADDRESS` | 6 | The most resets in any hour from all the players at one internet address together. |
| `MAX_NEW_GUESTS_PER_ADDRESS` | 20 | The most new guests in any hour from one internet address. A new browser is a new guest with 10 points, so without it one person could make as many as they liked. A page past the limit gets `429`, says so, and keeps trying, as one past the [limit on open pages](#limits-on-open-pages) does. Players the server already knows, and players with a saved name, aren't counted. |

The address is worked out as for the [limits on open pages](#limits-on-open-pages), and with none to go by (as on your own computer) only the limits for each player apply. A player's last reset goes with them when the game moves to a new server; the counts for each address start afresh.

Someone with many internet addresses can still get round all four, but each address then brings only so many free points an hour.
