// The best-round card: one player's best single round, drawn from its record. Used by the game
// page (above the leaderboard) and by the share page (/r/<shareId>). makeCard() returns the
// card's element; the page puts it where it wants and calls it again when the record changes.
//
// A record looks like this (the server makes it, in new_best_rounds() in bouncemarket/cards.py):
//   {share_id, username, settled_at, outcome: "left" | "right", frames, close_frame,
//    trades: [[frame, side, price, shares], ...], prices: [[frame, left price], ...],
//    cost, payout, profit}
// frames is how long the round took, at 60 a second; trading closed at close_frame. prices is
// the left side's price, at every frame where it changed (the right side's is 1 minus it).
// username is null for a player who hadn't chosen one.
(function () {
  const SVG = "http://www.w3.org/2000/svg";
  const MINUS = String.fromCharCode(0x2212);
  const SIDES = ["left", "right"];

  // The chart's drawing area, in its own units. It's stretched to the card's width, which on a
  // phone is about 300 pixels, so one unit is about one pixel there.
  const W = 320, H = 112, PAD = { left: 30, right: 10, top: 16, bottom: 6 };

  // Two decimal places, halves rounded up, the same as two_places() in bouncemarket/card_text.py, so the card and
  // the link preview never disagree about an average such as 0.125.
  const twoPlaces = (number) => (Math.round(number * 100) / 100).toFixed(2);

  // A number of points with its sign, as the game shows them: +1.58, −1.42, or 0.00.
  function signed(points) {
    const rounded = Math.round(points * 100) / 100;
    return (rounded > 0 ? "+" : rounded < 0 ? MINUS : "") + Math.abs(rounded).toFixed(2);
  }

  function el(tag, attrs = {}, ...children) {
    const node = tag.startsWith("svg:") ? document.createElementNS(SVG, tag.slice(4)) : document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === "hidden") node.hidden = true;
      else if (key === "class") node.setAttribute("class", value);
      else if (key === "text") node.textContent = value;
      else node.setAttribute(key, value);
    }
    node.append(...children);
    return node;
  }

  // Each side's buys have their own shape as well as their own color, so they can be told
  // apart without color: a circle for left, a triangle pointing up for right.
  function marker(side, x, y, size) {
    if (side === "left") return el("svg:circle", { cx: x, cy: y, r: size, class: "bm-mark bm-left" });
    const r = size * 1.25;
    const points = [[x, y - r], [x + r * 0.95, y + r * 0.7], [x - r * 0.95, y + r * 0.7]];
    return el("svg:polygon", { points: points.map((p) => p.join(",")).join(" "), class: "bm-mark bm-right" });
  }

  // The same shape on its own, small, to sit in a line of text as a key to the chart.
  function glyph(side) {
    return el("svg:svg", { viewBox: "-6 -6 12 12", width: 11, height: 11, class: "bm-glyph", "aria-hidden": "true" }, marker(side, 0, 0.5, 4));
  }

  // Each side's buys added up: how many shares, and the average price paid.
  function bySide(record) {
    const sides = {};
    for (const [frame, side, price, shares] of record.trades) {
      sides[side] ??= { shares: 0, cost: 0 };
      sides[side].shares += shares;
      sides[side].cost += price * shares;
    }
    return SIDES.filter((side) => sides[side]).map((side) => ({
      side, shares: sides[side].shares, avg: sides[side].cost / sides[side].shares,
    }));
  }

  // The line the share button sends, before the link, e.g.
  // "bouncemarket: bought Left x6 at avg 0.14, landed Left, +5.16 in one round".
  function shareText(record) {
    const name = (side) => side[0].toUpperCase() + side.slice(1);
    const bought = bySide(record).map((s) => `${name(s.side)} x${s.shares} at avg ${twoPlaces(s.avg)}`).join(" and ");
    return `bouncemarket: bought ${bought}, landed ${name(record.outcome)}, ${signed(record.profit)} in one round`;
  }

  // The price of the side the ball landed on, over the round, with a mark for every buy.
  // The two prices always add up to 1, so one line says it all when every buy was on the side
  // that won. If some were on the other side, that side's line is drawn too, faintly, so every
  // mark sits on the price it was bought at. After trading closed the price stays put, dotted,
  // until the ball lands, and then the line goes to 1.00: what a share on that side paid out.
  //
  // Returns the drawing and setFrame(frame), which the replay on the share page calls as the
  // ball falls: it shows the lines only as far as that frame, with a dot at their tip, and each
  // buy's mark only once its frame has come. Until setFrame is called, everything shows.
  let clips = 0;  // each chart's clip needs an id of its own on the page
  function chart(record) {
    const won = record.outcome;
    const x = (frame) => PAD.left + (frame / record.frames) * (W - PAD.left - PAD.right);
    const y = (price) => PAD.top + (1 - price) * (H - PAD.top - PAD.bottom);
    const priceOf = (side, leftPrice) => (side === "left" ? leftPrice : Math.round((1 - leftPrice) * 100) / 100);

    // A step line through the prices: flat until the next change.
    function line(side) {
      let d = "";
      for (const [frame, leftPrice] of record.prices) {
        const px = x(frame).toFixed(1), py = y(priceOf(side, leftPrice)).toFixed(1);
        d += d ? `H${px}V${py}` : `M${px},${py}`;
      }
      return d + `H${x(record.close_frame).toFixed(1)}`;
    }
    const lastPrice = priceOf(won, record.prices[record.prices.length - 1][1]);

    const svg = el("svg:svg", {
      viewBox: `0 0 ${W} ${H}`, class: "bm-chart", role: "img",
      "aria-label": `${won} price over the round, from ${priceOf(won, record.prices[0][1]).toFixed(2)} to ${lastPrice.toFixed(2)} when trading closed, then landed ${won}`,
    });
    for (const price of [0, 0.5, 1]) {
      svg.append(
        el("svg:line", { x1: PAD.left, x2: W - PAD.right, y1: y(price), y2: y(price), class: "bm-grid" }),
        el("svg:text", { x: PAD.left - 5, y: y(price) + 3.5, class: "bm-axis", "text-anchor": "end", text: price.toFixed(2) }),
      );
    }
    const traded = new Set(record.trades.map(([frame, side]) => side));
    const other = won === "left" ? "right" : "left";
    // The lines go in a group cut off at the replay's frame (by clipRect), uncut at rest.
    const clipId = `bm-clip-${++clips}`;
    const clipRect = el("svg:rect", { x: 0, y: 0, width: W + 10, height: H });
    const lines = el("svg:g", { "clip-path": `url(#${clipId})` });
    svg.append(el("svg:clipPath", { id: clipId }, clipRect), lines);
    if (traded.has(other)) lines.append(el("svg:path", { d: line(other), class: "bm-line bm-line-other" }));
    lines.append(el("svg:path", { d: line(won), class: "bm-line" }));
    // Trading closed, then the ball landed: dotted, then up to what the winning side paid.
    const end = x(record.frames);
    lines.append(el("svg:path", { d: `M${x(record.close_frame)},${y(lastPrice)}H${end}V${y(1)}`, class: "bm-line bm-line-after" }));
    const marks = record.trades.map(([frame, side, price]) => {
      const mark = marker(side, x(frame), y(price), 3.6);
      svg.append(mark);
      return [frame, mark];
    });
    const tip = el("svg:circle", { r: 2.6, class: "bm-tip", visibility: "hidden" });
    const landed = [
      el("svg:circle", { cx: end, cy: y(1), r: 3.6, class: "bm-landed" }),
      el("svg:text", { x: end, y: y(1) - 7, "text-anchor": "end", class: "bm-landed-label", text: `landed ${won}` }),
    ];
    svg.append(tip, ...landed, el("svg:text", { x: PAD.left + 2, y: 10, class: "bm-axis", text: `${won} price` }));

    // The landed side's price at a frame: the last change at or before it.
    function priceAt(frame) {
      let price = record.prices[0][1];
      for (const [at, leftPrice] of record.prices) {
        if (at > frame) break;
        price = leftPrice;
      }
      return priceOf(won, price);
    }

    function setFrame(frame) {
      const done = frame >= record.frames;
      clipRect.setAttribute("width", done ? W + 10 : x(frame));
      for (const [at, mark] of marks) {
        const shown = done || at <= frame;
        if (shown && mark.getAttribute("visibility") === "hidden") mark.classList.add("bm-pop");  // just appeared
        mark.setAttribute("visibility", shown ? "visible" : "hidden");
      }
      tip.setAttribute("visibility", done ? "hidden" : "visible");
      tip.setAttribute("cx", x(Math.min(frame, record.frames)));
      tip.setAttribute("cy", y(priceAt(Math.min(frame, record.close_frame))));
      for (const node of landed) node.setAttribute("visibility", done ? "visible" : "hidden");
    }
    return { svg, setFrame };
  }

  // The day the round was played, e.g. "26 nov 2026" (lowercase, like the rest of the game).
  // In UTC, so the card on every screen and in the link preview shows the same day.
  const MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
  function date(iso) {
    const day = new Date(iso);
    return `${day.getUTCDate()} ${MONTHS[day.getUTCMonth()]} ${day.getUTCFullYear()}`;
  }

  // options.fresh: the round has only just become the best, so the card says so.
  // options.share: false leaves out the share button (the share page has its own way back).
  // options.onDelete: on the player's own card, adds a small "delete" button; called once
  // they've said yes, it deletes the card and returns whether that worked.
  function makeCard(record, options = {}) {
    const sides = bySide(record);
    const shares = sides.reduce((total, s) => total + s.shares, 0);

    const summary = el("p", { class: "bm-summary" });
    sides.forEach((s, i) => {
      summary.append(i ? " · " : "", glyph(s.side), ` ${i ? "" : "bought "}${s.side} ×${s.shares} at avg ${twoPlaces(s.avg)}`);
    });
    summary.append(` · landed ${record.outcome}`);

    const stat = (value, label) => el("span", {}, el("b", { text: value }), " " + label);
    const stats = el("p", { class: "bm-stats" },
      stat(String(shares), shares === 1 ? "share" : "shares"), " · ",
      el("span", {}, "cost ", el("b", { text: record.cost.toFixed(2) })), " · ",
      el("span", {}, "payout ", el("b", { text: record.payout.toFixed(2) })),
    );

    const footer = el("div", { class: "bm-footer" },
      el("span", { class: "bm-byline", text: [record.username, date(record.settled_at)].filter(Boolean).join(" · ") }));
    if (options.share !== false) footer.append(shareMenu(record));

    const head = el("div", { class: "bm-head" },
      el("p", { class: "bm-profit" }, el("span", { class: "bm-number", text: signed(record.profit) }), el("span", { class: "bm-label", text: "best round" })));
    if (options.fresh) head.append(el("span", { class: "bm-new", text: "new best round" }));
    if (options.onDelete) head.append(trashButton(head, options.onDelete));

    const drawing = chart(record);
    const card = el("div", { class: "bm-card panel" + (options.fresh ? " bm-fresh" : "") }, head, drawing.svg, summary, stats, footer);
    card.setFrame = drawing.setFrame;  // for the replay
    return card;
  }

  // The trash can at the top right of the player's own card, like the pencil and reset icons on
  // the leaderboard. While the card is a new best, the "new best round" label has that spot and
  // the trash can is hidden (see card.css); it shows once the label goes. It asks first, in the
  // card's top row itself ("delete this card? no / yes, delete"), never in a pop-up over the
  // game. onDelete does the deleting and says whether it worked; if it didn't, the row says so
  // and the card stays.
  function trashButton(head, onDelete) {
    const button = el("button", { type: "button", class: "bm-trash", "aria-label": "delete this card", title: "delete this card" },
      el("svg:svg", {
        viewBox: "0 0 24 24", width: 14, height: 14, fill: "none", stroke: "currentColor", "stroke-width": 2.2,
        "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true",
      },
      el("svg:path", { d: "M3 6h18" }),
      el("svg:path", { d: "M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6" }),
      el("svg:path", { d: "M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" }),
      el("svg:path", { d: "M10 11v6" }),
      el("svg:path", { d: "M14 11v6" })));
    button.onclick = () => {
      const yes = el("button", { type: "button", class: "bm-confirm-yes", text: "yes, delete" });
      const no = el("button", { type: "button", class: "bm-confirm-no", text: "no" });
      const question = el("span", { class: "bm-confirm", role: "group", "aria-label": "delete this card?" },
        el("span", { class: "bm-confirm-text", text: "delete this card? shared links keep working." }), no, yes);
      const back = () => {
        question.replaceWith(...saved);
        button.focus();
      };
      const saved = [...head.childNodes];
      head.replaceChildren(question);
      no.onclick = back;
      question.addEventListener("keydown", (event) => { if (event.key === "Escape") back(); });
      yes.onclick = async () => {
        yes.disabled = no.disabled = true;
        yes.textContent = "deleting…";
        if (!(await onDelete())) {
          question.querySelector(".bm-confirm-text").textContent = "couldn't delete it. try again in a minute.";
          yes.disabled = no.disabled = false;
          yes.textContent = "yes, delete";
        }
        // If it worked, the page takes the card away.
      };
      no.focus();
    };
    return button;
  }

  // The share button opens a small menu, inside the card so it never covers the board or the
  // buy buttons:
  //   copy text   the line and the link, e.g. "bouncemarket: bought Left x6 at avg 0.14, ..."
  //   copy image  the card as a picture (/r/<share_id>.png, drawn by bouncemarket/card_image.py), for
  //               pasting where a link won't do; it has "bouncemarket" and the site's address on it
  //   share…      the phone's own share sheet, where the browser has one
  // "copy image" is left out where the browser can't put a picture on the clipboard. After a
  // choice the button says what happened for a moment.
  function shareMenu(record) {
    const text = shareText(record);
    const url = `${location.origin}/r/${record.share_id}`;
    const button = el("button", { type: "button", class: "bm-share", text: "share", "aria-haspopup": "menu", "aria-expanded": "false" });
    const menu = el("div", { class: "bm-menu", role: "menu", hidden: "" });
    const wrap = el("span", { class: "bm-share-wrap" }, button, menu);

    const item = (label, action) => {
      const choice = el("button", { type: "button", role: "menuitem", text: label });
      choice.onclick = async () => {
        close();
        button.focus();
        await action();
      };
      menu.append(choice);
    };
    item("copy text", () => report(navigator.clipboard.writeText(`${text} ${url}`), "text copied"));
    if (window.ClipboardItem && navigator.clipboard?.write) {
      // The picture is fetched inside the ClipboardItem, so the copy still counts as part of the
      // click (Safari refuses a copy made after waiting for the picture first).
      const picture = () => fetch(`/r/${record.share_id}.png`).then((response) => {
        if (!response.ok) throw new Error("no picture");
        return response.blob();
      });
      item("copy image", () => {
        button.textContent = "copying…";
        return report(navigator.clipboard.write([new ClipboardItem({ "image/png": picture() })]), "image copied");
      });
    }
    if (navigator.share) {
      item("share…", async () => {
        try {
          await navigator.share({ text, url });
        } catch (error) {
          if (error.name !== "AbortError") report(Promise.reject(error), "", "couldn't share");  // AbortError: the sheet was closed
        }
      });
    }

    async function report(copying, done, failed = "couldn't copy") {
      try {
        await copying;
        button.textContent = done;
      } catch {
        button.textContent = failed;
      }
      setTimeout(() => { button.textContent = "share"; }, 2000);
    }

    // The menu closes on a choice, on Escape, or on a click anywhere else.
    function outside(event) {
      if (!wrap.contains(event.target)) close();
    }
    function escape(event) {
      if (event.key === "Escape") {
        close();
        button.focus();
      }
    }
    function open() {
      menu.hidden = false;
      button.setAttribute("aria-expanded", "true");
      document.addEventListener("pointerdown", outside);
      document.addEventListener("keydown", escape);
      menu.querySelector("button").focus();
    }
    function close() {
      menu.hidden = true;
      button.setAttribute("aria-expanded", "false");
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    }
    button.onclick = () => (menu.hidden ? open() : close());
    // Up and down arrows move between the choices.
    menu.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
      event.preventDefault();
      const choices = [...menu.querySelectorAll("button")];
      const at = choices.indexOf(document.activeElement);
      choices[(at + (event.key === "ArrowDown" ? 1 : -1) + choices.length) % choices.length].focus();
    });
    return wrap;
  }

  window.bestRoundCard = { makeCard, shareText, signed };
})();
