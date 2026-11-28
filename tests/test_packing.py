"""The round log's packing (bouncemarket/packing.py): what is packed must unpack to the same."""
from bouncemarket import packing


def test_a_path_survives_packing():
    path = [(200.0, 0.0), (200.1, 0.4), (199.7, 12.3), (64.2, 551.9)]
    assert packing.unpack_path(packing.pack_path(path)) == path


def test_buys_survive_packing():
    buys = [
        # frame, the frame the page said it was showing, the player's key, side, price, points before
        (5, 3, 17, "left", 0.52, 10.0),
        (5, packing.NOT_SEEN, 4, "right", 0.5, 9.48),
        (130, 141, 17, "right", 1.0, 1234.56),
    ]
    unpacked = packing.unpack_buys(packing.pack_buys(buys))
    assert unpacked[0] == buys[0] and unpacked[2] == buys[2]
    assert unpacked[1] == (5, None, 4, "right", 0.5, 9.48)  # a page that didn't say comes back as None


def test_timing_survives_packing():
    broadcasts = [[0.001234, 0.0015, 0.0209, 456, 456], [0.6, None, None, 456, 0]]
    buy_timing = [(0.0301, 0.0015, 0), (0.0302, None, None)]
    unpacked = packing.unpack_timing(packing.pack_timing(broadcasts, buy_timing))
    assert unpacked == {"broadcasts": [tuple(b) for b in broadcasts], "buys": buy_timing}
