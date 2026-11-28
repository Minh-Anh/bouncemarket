// The game page (index.html): plays the ball from the server's stream, and buys, names and
// resets on the player's behalf. board.js draws the board and card.js the best-round card.

const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d");
const FPS = 60;  // the ball moves one frame every 1/60 second
const FULL_RETRY_MS = [10000, 20000];  // turned away because the game is full, the page asks again after a wait between these
const NEW_DROP_MS = 5000;  // a drop that began this far from when we thought is a different drop (the wait between drops alone is this long)
const MIN_ONLINE_SHOWN = 5;  // the header says how many are online only when at least this many are
const FADE_RATE = 4;  // how sharp the landing highlight's fade is: higher makes the start and end quicker and the middle flatter
// The color behind the board: the page's own background, read from the palette at the top of game.css.
const FIELD_COLOR = getComputedStyle(document.documentElement).getPropertyValue("--background").trim();
let board;
let game = null;  // the latest public message: the state of the shared game
let me = null;  // the latest private message: this player's name, points and shares
let leaderboard = { top: [], around_me: [] };  // the latest leaderboard message: the top players, and the players around us if we're below them
let queue = [];  // ball positions waiting until their frame is due, each [frame, x, y]
let dropStart = null;  // on our clock, the moment the ball dropped
let shownFrame = null;  // the frame of the ball position on screen right now (null between drops)
let dropsAt = null;  // on our clock, the moment the next ball will drop (only between drops)
let updateAt = null;  // on our clock, the moment of the system update the owner announced, if any
let noticeShown = null;  // what the notice banner says now, so it's only rebuilt when that changes
let card = null;  // our best-round card, from the latest "card" message, or null if we have none
// Whether the card is a new best, shown with a yellow edge and a label: null if not, else
// "waiting" for the next ball to drop, then "dropping" until it lands, when it stops being new.
let cardFresh = null;

// A random id, remembered by this browser, that tells the server which player we are.
let playerId = localStorage.getItem("playerId");
if (!playerId) {
  playerId = crypto.randomUUID();
  localStorage.setItem("playerId", playerId);
}

// Draw the board (see board.js), plus the ball if a position is given.
// Between drops there is no ball. Instead the bucket it landed in starts out yellow,
// the ball's color, and fades away by the time the next ball drops.
function draw(ball) {
  let highlight = 0;
  if (!ball && game && game.last_bucket && dropsAt !== null) {
    // How far through the countdown we are, from 0 at landing to 1 at the next drop.
    const progress = Math.min(1, Math.max(0, 1 - (dropsAt - performance.now()) / 1000 / board.countdown_seconds));
    // A reverse S-curve: fades fast at first, lingers around half strength, then fades fast
    // again at the end. sinh is steep at both ends and flat in the middle, and dividing by
    // sinh(FADE_RATE) scales it so the highlight starts at exactly 1 and ends at exactly 0.
    highlight = 0.5 - 0.5 * Math.sinh(FADE_RATE * (2 * progress - 1)) / Math.sinh(FADE_RATE);
  }
  drawBoard(ctx, board, { ball, bucket: game && game.last_bucket, highlight, field: FIELD_COLOR });
}

// A number of points with its sign, e.g. +1.58 or −1.42 (a real minus sign, as wide as the
// plus, not a hyphen), or 0.00 with no sign. Rounded first, so a tiny loss never shows as −0.00.
const MINUS = String.fromCharCode(0x2212);  // the minus sign, written as its code so it can't be mistaken for a hyphen here
function signed(points) {
  const rounded = Math.round(points * 100) / 100;
  const sign = rounded > 0 ? "+" : rounded < 0 ? MINUS : "";
  return sign + Math.abs(rounded).toFixed(2);
}

// Show everything except the ball, from the latest public and private messages.
function render() {
  if (!game || !me) return;
  // Everyone with the page open, us included. Shown only once there's a crowd: "1 online" or
  // "2 online" makes the game look empty, which is worse than saying nothing.
  document.getElementById("online").textContent = game.online >= MIN_ONLINE_SHOWN ? `${game.online} online` : "";

  // A share can be bought while betting is open, if we can afford its price.
  // Once we hold shares this round, each card also shows what we'd net if the ball lands on
  // its side: every share on that side pays 1, minus everything we paid this round, both sides.
  // The price shown is what a buy is charged ("costs"), so the two don't quite add up to 1.
  const holding = me.shares.left + me.shares.right > 0;
  const costs = game.costs || game.prices;  // an older server sends no costs
  for (const buyButton of document.querySelectorAll(".side button")) {
    const side = buyButton.dataset.side;
    const price = costs[side];
    document.getElementById("price-" + side).textContent = price.toFixed(2);
    document.getElementById("shares-" + side).textContent = me.shares[side];
    document.getElementById("net-" + side).textContent = holding ? ` · ${signed(me.shares[side] - me.spent)}` : "";
    buyButton.disabled = !game.betting_open || price > me.points;
  }

  // Out of points: we can't afford a share on either side and hold none that could win some
  // back. Without a way out, both buy buttons would stay grey for good, so we offer a reset.
  const broke = !holding && costs.left > me.points && costs.right > me.points;
  document.getElementById("broke-reset").hidden = !broke;

  // Market odds. The two prices always add up to 1, so each is the chance the market gives
  // that side. Rounded so the two percentages still add up to 100.
  const leftPercent = Math.round(game.prices.left * 100);
  const rightPercent = 100 - leftPercent;
  document.getElementById("odds-left-label").textContent = `left ${leftPercent}%`;
  document.getElementById("odds-right-label").textContent = `${rightPercent}% right`;
  const leftBar = document.getElementById("odds-left");
  const rightBar = document.getElementById("odds-right");
  leftBar.style.flexGrow = leftPercent;
  rightBar.style.flexGrow = rightPercent;
  leftBar.title = `left: ${leftPercent}% chance, ${game.prices.left.toFixed(2)} per share`;
  rightBar.title = `right: ${rightPercent}% chance, ${game.prices.right.toFixed(2)} per share`;
  document.getElementById("odds-status").textContent = game.dropping && !game.betting_open ? "trading closed" : "";

  // The status line: what is happening on the left, and our balance on the right.
  let status = "waiting for the first drop";
  if (game.dropping) {
    status = game.betting_open ? "trading open" : "trading closed";
  } else if (game.last_bucket) {
    status = `landed ${game.last_bucket} · ${signed(me.last_won)} points`;
  }
  document.getElementById("status").textContent = status;
  document.getElementById("balance").textContent = me.points.toFixed(2);  // updates as soon as we buy
  showCountdown();
  showNotice();
}

// The banner announcing a system update, counting down in whole minutes on our own clock.
// For a guest, "choose a username" is a button that opens the username dialog.
function showNotice() {
  const notice = document.getElementById("notice");
  if (updateAt === null) {
    notice.hidden = true;
    noticeShown = null;
    return;
  }
  const minutes = Math.ceil((updateAt - performance.now()) / 60000);
  const when = minutes > 0 ? `in ${minutes} minute${minutes === 1 ? "" : "s"}` : "starting now";
  const guest = me.name === null;
  // Rebuilt only when the words change, so a guest's click on the button is never lost to a redraw.
  const key = `${when}|${guest}`;
  if (key === noticeShown) return;
  noticeShown = key;
  const choose = document.createElement(guest ? "button" : "span");
  choose.textContent = "choose a username";
  if (guest) {
    choose.type = "button";
    choose.onclick = openNameDialog;
  }
  notice.replaceChildren(
    `System update ${when}, all non-named player scores may be wiped. If you would like to save your points, `,
    choose,
    " before the update.",
  );
  notice.hidden = false;
}

// Between drops the server only sends a message once a second, so the countdown runs on
// our own clock, from the drop time we worked out when the ball landed.
function showCountdown() {
  const seconds = dropsAt === null ? 0 : Math.max(0, Math.ceil((dropsAt - performance.now()) / 1000));
  document.getElementById("countdown").textContent = game.dropping ? "" : `next drop in ${seconds}s`;
}

// Everything we ask the server to do goes through here. During an update the game moves to
// a new server, and for a moment a request can still reach the old one, which answers 503
// with "moved". Then we wait a little and ask again, and the request reaches the new one.
const MOVED_RETRIES = 4;
const MOVED_WAIT_MS = 400;
async function post(path, body) {
  for (let attempt = 0; ; attempt++) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (response.status !== 503 || attempt >= MOVED_RETRIES) return response;
    const result = await response.clone().json().catch(() => ({}));
    if (!result.moved) return response;
    await new Promise((resolve) => setTimeout(resolve, MOVED_WAIT_MS));
  }
}

// Each buy button asks the server to buy one share on its side at the current price.
// The server answers with our new points and shares. We also say which frame we were showing,
// which the server only keeps for its log: it tells an analysis what the player was looking at.
for (const buyButton of document.querySelectorAll(".side button")) {
  buyButton.onclick = async () => {
    const response = await post("/buy", { player: playerId, side: buyButton.dataset.side, frame: shownFrame });
    if (response.ok) {
      me = await response.json();
      render();
    }
  };
}

// The keyboard buys too: the left arrow on the left, the right arrow on the right. Each press
// clicks that side's button, so a disabled button (betting closed, can't afford it) does nothing.
// Holding a key down buys once, not over and over. Keys are left alone while a dialog is open
// or we're typing, so the arrows still move the cursor in the username box.
const BUY_KEYS = { ArrowLeft: "left", ArrowRight: "right" };
function buyButtonFor(event) {
  const side = BUY_KEYS[event.key];
  if (!side || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return null;
  if (document.querySelector("dialog[open]")) return null;
  if (event.target.closest("input, textarea, select, [contenteditable]")) return null;
  return document.querySelector(`.side button[data-side="${side}"]`);
}
document.addEventListener("keydown", (event) => {
  const button = buyButtonFor(event);
  if (!button) return;
  event.preventDefault();  // no page scrolling sideways
  if (event.repeat) return;
  button.classList.add("pressed");
  button.click();
});
document.addEventListener("keyup", (event) => {
  const side = BUY_KEYS[event.key];
  if (side) document.querySelector(`.side button[data-side="${side}"]`).classList.remove("pressed");
});
window.addEventListener("blur", () => {
  for (const button of document.querySelectorAll(".side button.pressed")) button.classList.remove("pressed");
});

// Resetting: start over with 10 points. The server refuses while we hold shares in the round
// being played, and its reason is shown in errorText, next to whatever was pressed.
// Returns whether it worked.
async function resetPoints(errorText) {
  errorText.textContent = "";
  const response = await post("/reset", { player: playerId });
  const result = await response.json();
  if (!response.ok) {
    errorText.textContent = result.error;
    return false;
  }
  me = result;
  clearResetErrors();
  render();
  return true;  // the server also sends a new leaderboard with our 10 points on it
}
function clearResetErrors() {
  for (const id of ["reset-error", "leaderboard-error", "broke-error"]) document.getElementById(id).textContent = "";
}

// Only ask first when a reset would lose something, that is when we have more than 10 points.
// With 10 or fewer there is nothing to lose, so it happens straight away.
function askToReset(errorText) {
  if (me.points > 10) openResetDialog();
  else resetPoints(errorText);
}
document.getElementById("broke-reset").onclick = () => askToReset(document.getElementById("broke-error"));

// The reset dialog: start over with 10 points, after saying yes.
const resetDialog = document.getElementById("reset-dialog");
function openResetDialog() {
  document.getElementById("reset-points").textContent = me.points.toFixed(2);
  document.getElementById("reset-error").textContent = "";
  resetDialog.showModal();
}
document.getElementById("reset-cancel").onclick = () => resetDialog.close();
document.getElementById("reset-confirm").onclick = async () => {
  if (await resetPoints(document.getElementById("reset-error"))) resetDialog.close();
};

// The name dialog: pick a name, which puts it on the leaderboard in place of "you", or change
// the name we have. Either way the server is asked the same thing: make this our name.
const claimDialog = document.getElementById("claim-dialog");
function openNameDialog() {
  const changing = me.name !== null;
  document.getElementById("claim-title").textContent = changing ? "change your username" : "set a username";
  document.getElementById("claim-text").textContent = changing
    ? "pick any username that nobody else is using. your points stay with you."
    : "your username goes on the leaderboard, and your points are saved from then on. you can change it later.";
  document.getElementById("claim-confirm").textContent = changing ? "change username" : "set username";
  document.getElementById("name").value = changing ? me.name : "";  // start from the name we have
  document.getElementById("claim-error").textContent = "";
  claimDialog.showModal();
}
document.getElementById("claim-cancel").onclick = () => claimDialog.close();

// A username is 1 to 16 letters (a to z, either case), digits, "-" or "_". Anything else is
// dropped as it's typed or pasted, so "mr. bean!" becomes "mrbean", and past 16 characters
// the newest ones are dropped. The server still has the final say, and its reason shows
// below the box. The text cursor stays where it was, among the characters that are kept.
const NAME_LENGTH = 16;
const NOT_ALLOWED = /[^A-Za-z0-9_-]/g;
const nameInput = document.getElementById("name");
function keepAllowed() {
  const cursor = nameInput.selectionStart ?? nameInput.value.length;
  let before = nameInput.value.slice(0, cursor).replace(NOT_ALLOWED, "");
  const after = nameInput.value.slice(cursor).replace(NOT_ALLOWED, "");
  // Too long: what was just typed or pasted is right before the cursor, so cut it from there.
  const tooMany = before.length + after.length - NAME_LENGTH;
  if (tooMany > 0) before = before.slice(0, Math.max(0, before.length - tooMany));
  const kept = (before + after).slice(0, NAME_LENGTH);
  if (kept === nameInput.value) return;
  nameInput.value = kept;
  nameInput.setSelectionRange(before.length, before.length);
}
// While a phone keyboard is still composing a word, the box's text isn't final, and changing
// it then can garble what's typed. So we wait until the word is done.
nameInput.addEventListener("input", (event) => { if (!event.isComposing) keepAllowed(); });
nameInput.addEventListener("compositionend", keepAllowed);

document.getElementById("claim").onsubmit = async (event) => {
  event.preventDefault();  // stop the browser reloading the page, its default for forms
  const response = await post("/name", { player: playerId, name: document.getElementById("name").value });
  const result = await response.json();
  if (response.ok) {
    me = result;
    render();
    claimDialog.close();
    // The server sends everyone a new leaderboard with our name in it.
  } else {
    document.getElementById("claim-error").textContent = result.error;
  }
};

// The top players, then, if we're ranked below them, a "…" gap and the players around us.
function showLeaderboard() {
  // A row starts with its rank, e.g. "45.", in a column of its own (empty for the gap).
  const newRow = (rank) => {
    const row = document.createElement("li");
    const rankText = document.createElement("span");
    rankText.className = "rank";
    rankText.textContent = rank === null ? "" : rank + ".";
    row.append(rankText);
    return row;
  };
  const toRow = (entry) => {
    const row = newRow(entry.rank);
    const name = document.createElement("span");
    const points = document.createElement("span");
    name.className = "name";
    name.textContent = entry.name ?? "you";  // we're ranked even before claiming a name
    points.textContent = entry.points.toFixed(2);  // always two decimal places, e.g. 10.00
    points.className = "points";
    row.append(name);
    if (entry.me) {
      row.className = "me";
      if (entry.name === null) {
        // Without a name, a button after "you" to claim one.
        const claim = document.createElement("button");
        claim.type = "button";
        claim.className = "claim-button";
        claim.textContent = "set username";
        claim.onclick = openNameDialog;
        row.append(claim);
      } else if (!me?.name_locked) {
        // With a name, a small pencil button after it, to change it, unless the server says
        // this name can't be changed.
        const rename = document.getElementById("rename-icon").content.firstElementChild.cloneNode(true);
        rename.onclick = openNameDialog;
        row.append(rename);
      }
      // And, named or not, a small reset button just left of the points. It asks first
      // when there's something to lose (see askToReset()).
      const reset = document.getElementById("reset-icon").content.firstElementChild.cloneNode(true);
      reset.onclick = () => askToReset(document.getElementById("leaderboard-error"));
      points.prepend(reset);
    }
    row.append(points);
    return row;
  };
  const rows = leaderboard.top.map(toRow);
  const aroundMe = leaderboard.around_me;
  // Only show the gap if some ranks really are skipped (not when we're 11th or 12th).
  if (aroundMe.length > 0 && aroundMe[0].rank > leaderboard.top.length + 1) {
    const gap = newRow(null);
    gap.className = "gap";
    gap.append("…");
    rows.push(gap);
  }
  rows.push(...aroundMe.map(toRow));
  // The rank column is as wide as the longest rank shown, so every name starts at the same place.
  const ranks = [...leaderboard.top, ...aroundMe].map((entry) => String(entry.rank).length);
  const list = document.getElementById("leaderboard");
  list.style.setProperty("--rank-digits", Math.max(1, ...ranks));
  list.replaceChildren(...rows);
}

// Our best single round, above the leaderboard, drawn by card.js. The server only makes a
// card for a round that made more than 1.00, so with none there's nothing here at all. A new
// best is marked as new, in the card itself, never over the board, so the buy buttons stay
// usable; it stays marked until the next round has been played. Redrawn only when the card or
// that mark changes, so a "copied" on the share button isn't wiped by the next message.
function showCard() {
  const slot = document.getElementById("best-round");
  slot.hidden = !(card && card.profit > 1);
  if (slot.hidden) return;
  slot.replaceChildren(bestRoundCard.makeCard(card, { fresh: cardFresh !== null, onDelete: deleteCard }));
}

// The card's "delete" button, once the player has said yes. Their record starts over, so the
// next round over 1.00 makes a new card; links they've shared keep working. The server also
// tells our other open pages. Returns whether it worked, for the card to say so if it didn't.
async function deleteCard() {
  const response = await post("/card/clear", { player: playerId }).catch(() => null);
  if (!response || !response.ok) return false;
  card = null;
  cardFresh = null;
  showCard();
  return true;
}

// Counts the new best down to ordinary: the card arrives as the ball lands, then the next
// ball drops, and once that one lands too the card is no longer new.
function ageCard(dropping) {
  if (cardFresh === "waiting" && dropping) {
    cardFresh = "dropping";
  } else if (cardFresh === "dropping" && !dropping) {
    cardFresh = null;
    // Just the yellow edge and the label go: redrawing the card would lose a "delete this
    // card?" the player might be in the middle of answering.
    const shown = document.querySelector("#best-round .bm-card");
    shown?.classList.remove("bm-fresh");
    shown?.querySelector(".bm-new")?.remove();
  }
}

// The public "game" message arrives up to 10 times a second: twice a second early in a drop,
// 10 times a second near the end of one, and once a second between drops. While the ball is
// falling, each one carries the next ball positions, ahead of time. We queue them and play
// them back on our own clock, so uneven arrival times don't make the ball stutter.
function receiveGame(update) {
  game = update;
  ageCard(game.dropping);
  // The announced update, if any. Its time is kept steady against network jitter, and only
  // moved if the owner announces a new one (a jump of more than a couple of seconds).
  if (game.update_in === undefined) {
    updateAt = null;
  } else {
    const at = performance.now() + game.update_in * 1000;
    if (updateAt === null || Math.abs(at - updateAt) > 2000) updateAt = at;
  }
  if (!game.dropping) {
    queue = [];
    dropStart = null;
    shownFrame = null;
    // Set once per countdown, so network jitter in later messages doesn't make the fade flicker.
    if (dropsAt === null) dropsAt = performance.now() + game.seconds_to_drop * 1000;
  } else {
    dropsAt = null;
    // game.frame is the frame the server is on right now, which tells us when the drop began.
    // If that is nowhere near when we thought it began, this is a later drop: the stream was
    // cut for a while and we missed the end of the one before. Start this one afresh.
    const began = performance.now() - game.frame * 1000 / FPS;
    if (dropStart !== null && Math.abs(began - dropStart) > NEW_DROP_MS) {
      queue = [];
      dropStart = null;
      shownFrame = null;
    }
    if (dropStart === null) dropStart = began;
    game.positions.forEach(([x, y], i) => queue.push([game.first_position + i, x, y]));
  }
  render();
}

// Runs before every screen refresh. While the ball falls, draw the newest queued position
// whose frame is due. Between drops, redraw the board so the bucket highlight fades smoothly.
function play(now) {
  if (dropStart !== null) {
    const dueFrame = (now - dropStart) / 1000 * FPS;
    let latest = null;
    while (queue.length > 0 && queue[0][0] <= dueFrame) latest = queue.shift();
    if (latest) {
      draw([latest[1], latest[2]]);
      shownFrame = latest[0];
    }
  } else if (dropsAt !== null) {
    draw(null);
    if (game) showCountdown();
  }
  requestAnimationFrame(play);
}

async function init() {
  const response = await fetch("/board");
  board = await response.json();
  canvas.width = board.width;
  canvas.height = board.height;
  draw(null);
  connect();
  requestAnimationFrame(play);
}

// The server sends a stream of messages for as long as the page is open: a private
// "me" message with our name, points and shares, the public "game" message, and the leaderboard.
// If the connection drops, the browser reconnects by itself. That is also how we follow
// the game to a new server during an update: the old server ends the stream, the browser
// connects again, and the new server carries on from the same frame.
function connect() {
  const stream = new EventSource("/events?player=" + playerId);
  const waiting = document.getElementById("waiting");
  stream.onopen = () => { waiting.hidden = true; };
  // The browser gives up, for good, only when the server answers but won't open a stream:
  // it is full, or too many pages are open from our network (see take_slot() in bouncemarket/limits.py), or
  // too many new players have come from it lately (see admit_new_guest()).
  // Then we say so and ask again ourselves, each page after a different wait.
  stream.onerror = () => {
    if (stream.readyState !== EventSource.CLOSED) return;  // just a dropped connection, which the browser retries
    waiting.hidden = false;
    setTimeout(connect, FULL_RETRY_MS[0] + Math.random() * (FULL_RETRY_MS[1] - FULL_RETRY_MS[0]));
  };
  // A "me" message comes after each round, which makes any "reset once this round is over" out of date.
  stream.addEventListener("me", (event) => { me = JSON.parse(event.data); clearResetErrors(); render(); showLeaderboard(); });
  stream.addEventListener("leaderboard", (event) => { leaderboard = JSON.parse(event.data); showLeaderboard(); });
  stream.addEventListener("game", (event) => receiveGame(JSON.parse(event.data)));
  // Our best-round card: when we connect, if we have one, and whenever a round becomes our new best.
  stream.addEventListener("card", (event) => {
    const message = JSON.parse(event.data);
    card = message.card;
    cardFresh = message.fresh ? "waiting" : null;
    showCard();
  });
}
init();
