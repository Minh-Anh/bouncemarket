# Deploys

The app is hosted on [Render](https://render.com), which reads `render.yaml`, installs `requirements.txt`, and runs `python app.py`. (`requirements.txt` and `pyproject.toml` list the same pinned versions; change both together.) Every push to `main` redeploys it. `SUPABASE_URL`, `SUPABASE_KEY` and, optionally, `ADMIN_KEY` are set under Environment in the Render dashboard.

When an update changes `db/schema.sql`, run it in the Supabase SQL editor before pushing. The old code doesn't mind the additions, and the new code may need them (see [Moderating names](moderation.md) for what happens if it is pushed first).

An update doesn't stop the game: the old server hands it to the new one, ball in the air and all, and the open pages follow. See [Updating without stopping the game](#updating-without-stopping-the-game).

## Updating without stopping the game

Render updates a service blue-green: it starts a new server beside the old one, sends every new request to the new one once it is up, and stops the old one a minute later. Open streams stay with the old server for that minute. The game lives in one server's memory, so on its own this would split it: a page would watch the ball on the old server while its buys went to the new one, which has never heard of the player, and a minute later every page would land in a different game, guests back on 10 points.

So the old server hands the game over at the moment of the switch:

1. The new server, before it takes any requests, says hello at the site's own address. No request reaches it yet, so the old server answers. The new one now waits for the game without starting one of its own, and the old one starts watching.
2. Twice a second, the old server asks the site's address "which server are you?" (`/health`). The first time the new server answers, Render has switched over.
3. The old server stops changing anything and sends the new one everything that lives only in memory: the round being played (the ball's path, where it will land, every buy), each player's points and shares, the leaderboard, and any round or card not saved yet.
4. The new server carries on from the same frame. Requests that reached it before then were kept waiting, and are answered now.
5. The old server sends each page the next second and a half of the ball's path, then ends its stream. The browser connects again by itself, between 0.05 and 0.5 seconds later, each page after a different wait so they don't all arrive at once, and reaches the new server.

Rehearsed on one computer with 20 pages, every page was back on a stream within 0.7 seconds of the switch, and none missed a frame of the ball's path. A player sees nothing: the ball keeps falling, their shares and points are as they were, and a buy made in that moment waits a fraction of a second longer for its answer. The round is paid out and logged by the new server, once, under the old server's `commit_id`, as that is the code that dropped the ball. Only a handover in the last second or so before trading closes can show. A handover never shows a page more of the ball's path than it could have seen without one, and near the close that is only 0.2 seconds ahead, so the ball may stand still for a moment, up to about half a second, until the page has reconnected.

It needs nothing set up on Render. The servers find each other through `RENDER_EXTERNAL_URL`, which Render sets, and prove who they are with a key worked out from `SUPABASE_KEY`, which both have. `render.yaml` sets `healthCheckPath: /health`; if the service was made before that line was there, set the health check path to `/health` in the Render dashboard too. Anywhere else, set `PUBLIC_URL` to the address the site is reached at. Without either there are no handovers, which is how it runs on your own computer. There must only ever be one instance of the service, as before: the game is one server's memory.

Two things to know:

- **A change of rules calls off the round in the air.** If the new code has a different `RULES_VERSION`, the round can't change rules half-way, so everyone's shares in it are refunded and a new countdown starts. Everything else carries over.
- **A change to what is handed over** in a way the new code can't read means changing `HANDOVER_FORMAT` in `bouncemarket/handover.py`. That one deploy then restarts the game, as every update once did: a new game, and guests back on 10 points. [Announce](#announcing-an-update) it if guests should be warned.

If a step fails, the game is never left without a server. The old server carries on if it can't deliver the game, tries twice more, and tries a last time when Render tells it to stop. A new server that has waited 5 seconds with requests held and been handed nothing starts a game of its own, and a handover that arrives later is merged into it: the old round is refunded and the players' points follow them. The worst case is what an update used to be.

The servers say what happened in Render's logs. The old one: `A new server has started. Watching for it to take over.` and then `Handed the game to the new server, <name>, with 12 pages open.` The new one: `Waiting to be handed the game by the old server, <name>.` and then `Took over the game from the old server, <name>: 14 players, carrying on its round.`

The code is in `bouncemarket/handover.py`, with the smaller part every request needs in `bouncemarket/role.py`.

## Announcing an update

An update normally carries everyone's points over to the new server (see [Updating without stopping the game](#updating-without-stopping-the-game)). It can't when the new server can't read what it is handed: a deploy that changes `HANDOVER_FORMAT`. Then guests (players without a username) lose their points, as they do if a handover fails. To warn them first, put up a banner on every open page that counts down to the update. With `ADMIN_KEY` and `SITE` set as for [moderating names](moderation.md), for an update in 15 minutes:

```
curl -s -X POST "$SITE/admin/notice" -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" -d '{"minutes": 15}'
```

Every page then shows "System update in 15 minutes, all non-named player scores may be wiped. If you would like to save your points, choose a username before the update.", counting down a minute at a time, with "choose a username" opening the username dialog for guests. When the time is up it says the update is starting now. It answers `{"notice": "published", "minutes": 15, "update_at": "..."}`. `minutes` can be any number from 0 to 1440, fractions included, and sending a new one restarts the countdown. Send `{"minutes": 0}` to take the banner down.

The banner lives only in the server's memory and isn't handed over, so the deploy it announces takes it down. Pages that connect while it is up see it straight away.
