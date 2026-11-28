"""A best-round card in words and as a picture (bouncemarket/card_text.py and card_image.py)."""
import io

from PIL import Image

from bouncemarket import card_image, card_text

RECORD = {
    "share_id": "k3J9xQ2a", "username": "Sam", "settled_at": "2026-11-27T21:59:08+00:00",
    "outcome": "left", "frames": 480, "close_frame": 400,
    "trades": [[30, "left", 0.14, 1], [35, "left", 0.15, 1], [210, "left", 0.125, 1], [200, "right", 0.5, 1]],
    "prices": [[0, 0.5], [30, 0.54], [120, 0.3], [300, 0.9]],
    "cost": 0.915, "payout": 3, "profit": 2.09,
}


def test_the_share_line():
    assert card_text.what_happened(RECORD) == "bought Left x3 at avg 0.14 and Right x1 at avg 0.50, landed Left"
    assert card_text.share_text(RECORD) == "bouncemarket: bought Left x3 at avg 0.14 and Right x1 at avg 0.50, landed Left, +2.09 in one round"


def test_halves_round_up():
    assert card_text.two_places(0.125) == "0.13"  # as frontend/card.js rounds them


def test_the_picture():
    picture = Image.open(io.BytesIO(card_image.render(RECORD, "bouncemarket.example")))
    assert picture.format == "PNG" and picture.size == (1200, 630)
