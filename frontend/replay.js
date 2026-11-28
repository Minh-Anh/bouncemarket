// The replay on the share page (/r/<share_id>): the round played again in real time. The ball
// falls on the board while the card's price line draws along with it, each buy's mark pops in
// at the moment it was made, and a line under the board says what's happening, as the game's
// status line does. When the ball lands, its bucket lights up and the play button appears.
//
// The ball's path comes from /r/<share_id>/replay, which plays the drop again on the server from
// the numbers it was made from. If there's no path to be had, or the viewer has asked their
// device for less motion, the page just shows the card, and the play button, as they are.
(function () {
  const FPS = 60;  // the ball moves one frame every 1/60 second, as in the game
  const NEWS_FRAMES = 45;  // how long "bought left at 0.13" stays on the line under the board

  async function start({ record, card, canvas, status, skip, again, play }) {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let board, path;
    try {
      const [boardResponse, replayResponse] = await Promise.all([fetch("/board"), fetch(`/r/${record.share_id}/replay`)]);
      if (!boardResponse.ok || !replayResponse.ok) return;
      board = await boardResponse.json();
      path = (await replayResponse.json()).path;
    } catch {
      return;  // offline, or the server is restarting: the card on its own will do
    }
    canvas.width = board.width;
    canvas.height = board.height;
    const ctx = canvas.getContext("2d");
    const field = getComputedStyle(document.documentElement).getPropertyValue("--background").trim();
    canvas.closest("[hidden]")?.removeAttribute("hidden");

    // What the line under the board says at a frame.
    function news(frame) {
      if (frame >= path.length) return `landed ${record.outcome} · ${bestRoundCard.signed(record.profit)} points`;
      const recent = record.trades.filter(([at]) => at <= frame && frame - at < NEWS_FRAMES);
      if (recent.length) {
        const [at, side, price] = recent[recent.length - 1];
        return `bought ${side} at ${price.toFixed(2)}`;
      }
      return frame < record.close_frame ? "trading open" : "trading closed";
    }

    let started, frameShown = -1, running = false;
    function show(frame) {
      const done = frame >= path.length;
      drawBoard(ctx, board, {
        ball: done ? null : path[frame],
        bucket: record.outcome,
        highlight: done ? 0.5 : 0,
        field,
      });
      card.setFrame(frame);
      status.textContent = news(frame);
      frameShown = frame;
    }
    function tick(now) {
      if (!running) return;
      const frame = Math.min(path.length, Math.floor((now - started) / 1000 * FPS));
      if (frame !== frameShown) show(frame);
      if (frame >= path.length) finish();
      else requestAnimationFrame(tick);
    }
    function finish() {
      running = false;
      show(path.length);
      skip.hidden = true;
      again.hidden = false;
      play.hidden = false;
      play.classList.add("arrive");
      // On a phone the board and card fill the screen, so bring the play button into view,
      // scrolling only if it isn't already.
      play.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
    function run() {
      running = true;
      skip.hidden = false;
      again.hidden = true;
      play.hidden = true;
      play.classList.remove("arrive");
      show(0);
      started = performance.now();
      requestAnimationFrame(tick);
    }
    skip.onclick = finish;
    again.onclick = run;
    run();
  }

  window.replayRound = start;
})();
