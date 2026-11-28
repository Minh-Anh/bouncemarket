# The pages

Plain HTML, CSS and JavaScript, with nothing to build. The server sends each file as it is (`bouncemarket/web/pages.py`), except `share.html`, whose `{{...}}` it fills in for each card (`bouncemarket/web/share.py`).

| | |
| --- | --- |
| `index.html`, `game.css`, `game.js` | The game page, `/`. |
| `share.html`, `share.css`, `share.js` | The public page for one best-round card, `/r/<share_id>`. |
| `board.js` | Draws the board and the ball. Both pages use it. |
| `card.js`, `card.css` | Draws the best-round card. Both pages use it. |
| `replay.js` | Plays a card's round again on the share page. |

## What a page asks the server

- `GET /board` once, for the board's size and pins.
- `GET /events?player=<id>`, a stream it keeps open, carrying four kinds of message: `game` (the same for everyone: prices, the countdown, the ball's next positions), `me` (this player's name, points and shares), `leaderboard` and `card`. [How it works](../docs/how-it-works.md) says when each is sent.
- `POST /buy`, `/name`, `/reset` and `/card/clear`, each with the player's id.

The id is a random one the browser makes and remembers. Whoever has it is that player, so it is never shown to anyone else.

## Changing a file

- A new file needs its name added to `ASSETS` in `bouncemarket/web/pages.py`.
- Browsers ask every time whether a file has changed, so an update reaches every page on its next load. Pages already open keep the scripts they have until they reload.
- The palette is written out three times: in `game.css`, in `share.css`, and in `bouncemarket/card_image.py`, which draws the card as a picture.
