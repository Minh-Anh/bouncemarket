"""A best-round card in words, for link previews, the card's picture and the share button."""
import math


def by_side(record):
    """Each side a card's player bought, in the order left, right: how many shares, and the
    average price paid, as [(side, shares, average)]."""
    sides = []
    for name in ("left", "right"):
        bought = [(price, shares) for frame, side, price, shares in record["trades"] if side == name]
        if bought:
            count = sum(shares for price, shares in bought)
            sides.append((name, count, sum(price * shares for price, shares in bought) / count))
    return sides


def two_places(number):
    """A number to two decimal places, halves rounded up, the same as card.js does it."""
    return f"{math.floor(number * 100 + 0.5) / 100:.2f}"


def what_happened(record):
    """What a card's player bought and where the ball landed, e.g.
    "bought Left x6 at avg 0.14, landed Left", or with both sides,
    "bought Left x5 at avg 0.23 and Right x2 at avg 0.50, landed Left"."""
    bought = " and ".join(f"{side.capitalize()} x{shares} at avg {two_places(average)}" for side, shares, average in by_side(record))
    return f"bought {bought}, landed {record['outcome'].capitalize()}"


def share_text(record):
    """The line the share button sends before the link, as card.js writes it:
    "bouncemarket: bought Left x6 at avg 0.14, landed Left, +5.16 in one round"."""
    return f"bouncemarket: {what_happened(record)}, +{two_places(record['profit'])} in one round"
