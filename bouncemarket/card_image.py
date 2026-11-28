"""The best-round card as a 1200 by 630 picture, for the preview that chat and social apps show
when a share link is posted, and for the share menu's "copy image" (see share_image() in
web/share.py). It says what frontend/card.js shows on the page: the profit, the price line with
a mark for every buy, what was bought, and the totals.

It is drawn twice as big and then shrunk, which smooths the edges of the lines and dots.
The text is Pillow's own built-in typeface, so the server needs no font files.
"""
import datetime
import io
import math

from PIL import Image, ImageDraw, ImageFont

from bouncemarket.card_text import by_side, two_places

WIDTH, HEIGHT = 1200, 630
SCALE = 2  # drawn at twice the size, then shrunk

# The game's palette (see frontend/game.css).
BACKGROUND = "#111111"
PANEL = "#1c1c1c"
TEXT = "#eeeeee"
MUTED = "#999999"
ACCENT = "#ffcc33"
LEFT = "#3987e5"
RIGHT = "#d95926"
GRID = "#2c2c2c"
LINE = "#c8c8c8"
OTHER_LINE = "#5a5a5a"

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

_fonts = {}


def font(size):
    """Pillow's built-in typeface at a size, in picture pixels before shrinking."""
    if size not in _fonts:
        _fonts[size] = ImageFont.load_default(size * SCALE)
    return _fonts[size]


class Canvas:
    """A drawing at twice the size, with everything given in the picture's own pixels."""

    def __init__(self):
        self.image = Image.new("RGB", (WIDTH * SCALE, HEIGHT * SCALE), BACKGROUND)
        self.draw = ImageDraw.Draw(self.image)

    def text(self, xy, words, size, color, bold=False, anchor="ls"):
        """Write words with their left end at xy, sitting on the line at xy (anchor "ls"), and
        return where they end. Bold is the same face with a thin outline in its own color."""
        x, y = xy
        width = 1 if bold else 0
        self.draw.text((x * SCALE, y * SCALE), words, font=font(size), fill=color, anchor=anchor,
                       stroke_width=width * SCALE, stroke_fill=color)
        return x + self.draw.textlength(words, font=font(size)) / SCALE + 2 * width

    def width(self, words, size):
        return self.draw.textlength(words, font=font(size)) / SCALE

    def line(self, points, color, width):
        self.draw.line([(x * SCALE, y * SCALE) for x, y in points], fill=color, width=round(width * SCALE), joint="curve")

    def dotted(self, points, color, width, gap):
        """A line of round dots, gap apart, along straight segments."""
        r = width / 2
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            length = math.hypot(x2 - x1, y2 - y1)
            for i in range(int(length // gap) + 1):
                t = i * gap / length if length else 0
                self.circle(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t, r, color)

    def dashed(self, points, color, width, dash):
        """A line of dashes, each dash long, with gaps as long."""
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            length = math.hypot(x2 - x1, y2 - y1)
            for i in range(0, int(length // dash) + 1, 2):
                a, b = i * dash / length if length else 0, min(1, (i + 1) * dash / length) if length else 0
                self.line([(x1 + (x2 - x1) * a, y1 + (y2 - y1) * a), (x1 + (x2 - x1) * b, y1 + (y2 - y1) * b)], color, width)

    def circle(self, x, y, r, color, outline=None, outline_width=0):
        self.draw.ellipse([(x - r) * SCALE, (y - r) * SCALE, (x + r) * SCALE, (y + r) * SCALE], fill=color,
                          outline=outline, width=round(outline_width * SCALE))

    def marker(self, side, x, y, size, outline=None):
        """A buy's mark, the same shapes as on the page: a circle for left, a triangle for right."""
        if side == "left":
            self.circle(x, y, size, LEFT, outline, 3 if outline else 0)
            return
        r = size * 1.25
        points = [(x, y - r), (x + r * 0.95, y + r * 0.7), (x - r * 0.95, y + r * 0.7)]
        self.draw.polygon([(px * SCALE, py * SCALE) for px, py in points], fill=RIGHT, outline=outline,
                          width=round(3 * SCALE) if outline else 0)

    def png(self):
        out = io.BytesIO()
        self.image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS).save(out, "PNG", optimize=True)
        return out.getvalue()


def chart(canvas, record, left, top, right, bottom):
    """The price of the side the ball landed on, as card.js draws it (see chart() there)."""
    won = record["outcome"]
    other = "right" if won == "left" else "left"
    frames, close = record["frames"], record["close_frame"]
    axis = 64  # room for the price labels
    x0 = left + axis

    def x(frame):
        return x0 + frame / frames * (right - x0)

    def y(price):
        return top + (1 - price) * (bottom - top)

    def price_of(side, left_price):
        return left_price if side == "left" else round(1 - left_price, 2)

    for price in (0, 0.5, 1):
        canvas.line([(x0, y(price)), (right, y(price))], GRID, 2)
        canvas.text((x0 - 12, y(price)), f"{price:.2f}", 22, MUTED, anchor="rm")

    def steps(side):
        points = []
        for frame, left_price in record["prices"]:
            if points:
                points.append((x(frame), points[-1][1]))
            points.append((x(frame), y(price_of(side, left_price))))
        points.append((x(close), points[-1][1]))
        return points

    if any(side == other for frame, side, price, shares in record["trades"]):
        canvas.dashed(steps(other), OTHER_LINE, 3, 8)
    line = steps(won)
    canvas.line(line, LINE, 4)
    last = line[-1][1]
    canvas.dotted([(x(close), last), (x(frames), last), (x(frames), y(1))], LINE, 4, 10)
    for frame, side, price, shares in record["trades"]:
        canvas.marker(side, x(frame), y(price), 9, outline=PANEL)
    canvas.circle(x(frames), y(1), 9, ACCENT)
    label = f"landed {won}"
    canvas.text((x(frames) - canvas.width(label, 24) - 2, y(1) - 20), label, 24, ACCENT, bold=True)
    canvas.text((x0 + 4, top - 18), f"{won} price", 22, MUTED)


def render(record, site):
    """The card as PNG bytes. site is the address to play at, written at the bottom right, so
    the picture says where it came from even when it's pasted on its own, with no link."""
    canvas = Canvas()
    pad = 32
    canvas.draw.rounded_rectangle([pad * SCALE, pad * SCALE, (WIDTH - pad) * SCALE, (HEIGHT - pad) * SCALE], 28 * SCALE, fill=PANEL)
    left, right = 88, WIDTH - 88

    # The headline: the profit, and "best round" beside it. At the top right, the game's name
    # with its yellow ball, and under it who played and when.
    end = canvas.text((left, 178), "+" + two_places(record["profit"]), 120, TEXT, bold=True)
    canvas.text((end + 22, 178), "best round", 40, MUTED, bold=True)
    name = "bouncemarket"
    name_left = right - canvas.width(name, 38) - 2
    canvas.text((name_left, 116), name, 38, TEXT, bold=True)
    canvas.circle(name_left - 26, 103, 11, ACCENT)
    day = datetime.datetime.fromisoformat(record["settled_at"]).astimezone(datetime.timezone.utc)
    byline = " · ".join(part for part in [record["username"] and record["username"].lower(), f"{day.day} {MONTHS[day.month - 1]} {day.year}"] if part)
    canvas.text((right - canvas.width(byline, 28), 160), byline, 28, MUTED)

    chart(canvas, record, left, 262, right, 452)

    # What was bought, with each side's mark as a key to the chart, then the totals. ("x", not
    # the "×" the page uses: the built-in typeface doesn't have it.) Buys on both sides make a
    # long line, so the words get smaller if they wouldn't otherwise fit.
    parts = [(side, f"{'' if i else 'bought '}{side} x{shares} at avg {two_places(average)}")
             for i, (side, shares, average) in enumerate(by_side(record))]
    ending = f"  ·  landed {record['outcome']}"
    size = 34
    while size > 20 and sum(28 + canvas.width(words, size) + canvas.width("  ·  ", size) for side, words in parts) + canvas.width(ending, size) > right - left:
        size -= 2
    x, y = left, 520
    for i, (side, words) in enumerate(parts):
        if i:
            x = canvas.text((x, y), "  ·  ", size, TEXT)
        canvas.marker(side, x + 10, y - size * 0.35, 9)
        x = canvas.text((x + 28, y), words, size, TEXT)
    canvas.text((x, y), ending, size, TEXT)

    shares = sum(shares for frame, side, price, shares in record["trades"])
    x = left
    for value, label in [(str(shares), " shares" if shares != 1 else " share"), (None, "  ·  cost "), (two_places(record["cost"]), ""),
                         (None, "  ·  payout "), (two_places(record["payout"]), "")]:
        if value is not None:
            x = canvas.text((x, 566), value, 30, TEXT, bold=True)
        if label:
            x = canvas.text((x, 566), label, 30, MUTED)
    canvas.text((right - canvas.width(site, 28), 566), site, 28, ACCENT)
    return canvas.png()
