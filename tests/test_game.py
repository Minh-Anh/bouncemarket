"""The rules of the game (bouncemarket/game/): the same inputs must always give the same round."""
from bouncemarket.game import board, counterparty, names, physics, pricing

# A drop played on 28 November 2026: where it started, and the seed of its hidden nudges.
START_X, NUDGE_SEED = 134.745077356615, 8702899481109894848
GRAVITY, BOUNCE, NUDGE = 400.0, 0.85, 0.003
BUFFER_FRAMES = 12


def drop():
    states, bucket = physics.simulate_drop(START_X, GRAVITY, BOUNCE, NUDGE, NUDGE_SEED)
    path = physics.positions(states)
    last_row_frame = next(i for i, (x, y) in enumerate(path) if y > board.LAST_ROW_Y)
    return states, bucket, path, last_row_frame - BUFFER_FRAMES


def test_a_drop_replays_exactly():
    """The round log saves five numbers for a drop, and relies on them giving this path for ever."""
    states, bucket, path, close_frame = drop()
    assert (len(states), bucket, close_frame) == (662, "right", 639)
    assert states[-1] == (287.34946835161173, 552.1157739745472, 93.84337249626647, 240.20652077954853)
    assert (path[100], path[-1]) == ((68.2, 111.5), (287.3, 552.1))
    numbers = {"start_x": START_X, "gravity": GRAVITY, "bounce": BOUNCE, "nudge": NUDGE, "nudge_seed": NUDGE_SEED}
    assert physics.replay_drop(numbers) == (states, bucket)


def test_the_nudges_change_the_drop():
    nudged, _ = physics.simulate_drop(START_X, GRAVITY, BOUNCE, NUDGE, NUDGE_SEED)
    plain, _ = physics.simulate_drop(START_X, GRAVITY, BOUNCE)
    assert physics.positions(nudged) != physics.positions(plain)
    # A round saved before the nudges has no seed, and replays without them.
    assert physics.replay_drop({"start_x": START_X, "gravity": GRAVITY, "bounce": BOUNCE})[0] == plain


def test_the_board():
    assert len(board.PINS) == sum(range(2, board.ROWS + 2))
    assert board.PINS[-1][1] == board.LAST_ROW_Y
    # The last row's center pin caps the divider between the buckets.
    assert any(x == board.WIDTH / 2 and y == board.LAST_ROW_Y for x, y in board.PINS)


def test_prices():
    assert pricing.prices_for_net(0) == {"left": 0.5, "right": 0.5}
    assert pricing.prices_for_net(7)["left"] == 0.73 and pricing.prices_for_net(14)["left"] == 0.88
    assert pricing.prices_for(10, 3) == pricing.prices_for_net(7)
    for net in range(-100, 101):
        price = pricing.prices_for_net(net)
        assert 0.01 <= price["left"] <= 0.99 and round(price["left"] + price["right"], 2) == 1


def test_costs():
    assert pricing.costs_for_net(0) == {"left": 0.52, "right": 0.52}
    assert pricing.costs_for_net(36)["left"] == 0.99 and pricing.costs_for_net(37) == {"left": 1.0, "right": 0.01}
    # Buying both sides gains nothing: a Left share, then a Right share back, cost exactly the 1 point the pair pays.
    for net in range(-36, 36):
        assert round(pricing.costs_for_net(net)["left"] + pricing.costs_for_net(net + 1)["right"], 2) == 1


def test_the_counterparty_is_the_same_every_time():
    states, bucket, path, close_frame = drop()
    first = counterparty.Counterparty(path, close_frame, START_X)
    first.advance(close_frame, [])
    assert (len(first.buys), first.buys[:3], first.buys[-1]) == (39, [(106, "left"), (187, "left"), (207, "right")], (610, "right"))
    # Asked a few frames at a time, as the server does, it buys the same.
    second = counterparty.Counterparty(path, close_frame, START_X)
    for frame in range(0, len(path), 6):
        second.advance(frame, [])
    assert second.buys == first.buys
    assert all(frame < close_frame for frame, side in first.buys)
    assert counterparty.shares_by_frame(first.buys, 200) == {"left": 2, "right": 0}


def test_the_counterparty_makes_way_for_players():
    states, bucket, path, close_frame = drop()
    # Five different players have bought by frame 14: (frame, seen frame, player, side, price, points).
    buys = [(10 + player, None, player, "left", 0.5, 10) for player in range(counterparty.COUNTERPARTY_TRADERS)]
    traders = counterparty.Counterparty(path, close_frame, START_X)
    traders.advance(close_frame, buys)
    assert all(frame <= 14 for frame, side in traders.buys)


def test_usernames():
    for name in ("Sam", "player-4821", "big_bird", "Scunthorpe", "grape", "x" * 16):
        assert names.name_problem(name) is None, name
    for name in ("", "x" * 17, "mr. bean", "-", "__"):
        assert names.name_problem(name) == names.BAD_CHARACTERS, name
    for name in ("you", "Y0U", "ad_m1n", "sh1t", "x1488x"):
        assert names.name_problem(name) == names.NOT_ALLOWED, name
