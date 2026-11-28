// Draws the board: the walls, pins and divider, the ball, and the bucket it landed in. Used by
// the game page and by the replay on the share page (/r/<share_id>), so both look the same.
// board is what /board answers: the size, the pins, and so on, from bouncemarket/game/board.py.
(function () {
  const BALL_DRAW_SCALE = 1.5;  // how much bigger the ball is drawn than the physics ball; only the picture changes
  const BALL_GLOW = 20;  // how far the ball's soft yellow glow spreads, in canvas pixels; 0 for none
  const BALL_GLOW_LAYERS = 2;  // how many times the glow is stacked; each layer makes it brighter
  const BALL_COLOR = "#fc3";
  const LINE_COLOR = "#888";

  function circle(ctx, x, y, r, color) {
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, 2 * Math.PI);
    ctx.fill();
  }

  function polygon(ctx, points, color) {
    ctx.fillStyle = color;
    ctx.beginPath();
    for (const [x, y] of points) ctx.lineTo(x, y);
    ctx.fill();
  }

  // options.ball: the ball's position as [x, y], or nothing for no ball.
  // options.bucket: "left" or "right" to light up that bucket in the ball's color,
  // options.highlight strong, from 0 (not at all) to 1.
  // options.field: the color behind the board, usually the page's background.
  function drawBoard(ctx, board, options = {}) {
    const w = board.width, h = board.height, mid = board.width / 2, top = board.last_row_y;
    // The walls lean in, so the space the ball falls through is a trapezoid. inset(y) is how far in a wall is at height y.
    const inset = (y) => board.wall_slant * y / h;
    // The board sits on a dark rectangle, like a window cut in the panel. The rectangle is what
    // makes the board look flat: with a dark trapezoid behind the pins instead, the eye takes
    // the whole board for a rectangle tipping away, not for a flat board with leaning walls.
    ctx.fillStyle = options.field;
    ctx.fillRect(0, 0, w, h);
    if (options.bucket && options.highlight > 0) {
      ctx.globalAlpha = options.highlight;
      // The bucket: from the last row of pins down to the floor, between the wall and the divider.
      const bucket = options.bucket === "left"
        ? [[inset(top), top], [inset(h), h], [mid, h], [mid, top]]
        : [[w - inset(top), top], [w - inset(h), h], [mid, h], [mid, top]];
      polygon(ctx, bucket, BALL_COLOR);
      ctx.globalAlpha = 1;
    }
    // The ball is drawn bigger than the one in the physics, so it's easy to see on a phone. It goes
    // under the walls and pins, so where it overlaps them it looks tucked behind, not sunk into them.
    if (options.ball) {
      // A soft yellow glow makes the ball easy to spot without drawing it any bigger.
      ctx.shadowColor = "rgba(255, 204, 51, 0.8)";
      ctx.shadowBlur = BALL_GLOW;
      for (let layer = 0; layer < BALL_GLOW_LAYERS; layer++) {
        circle(ctx, options.ball[0], options.ball[1], board.ball_r * BALL_DRAW_SCALE, BALL_COLOR);
      }
      ctx.shadowBlur = 0;  // nothing else glows
    }
    ctx.strokeStyle = LINE_COLOR;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(1, 0);  // the walls and floor, as one open-topped trapezoid
    ctx.lineTo(inset(h) + 1, h - 1);
    ctx.lineTo(w - inset(h) - 1, h - 1);
    ctx.lineTo(w - 1, 0);
    ctx.moveTo(mid, top);  // divider between the buckets
    ctx.lineTo(mid, h);
    ctx.stroke();
    for (const [x, y] of board.pins) circle(ctx, x, y, board.pin_r, LINE_COLOR);
  }

  window.drawBoard = drawBoard;
})();
