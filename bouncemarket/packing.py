"""How a round is packed into bytes for the round log, and unpacked again (see docs/round-log.md).

The path and the buys are packed into a few bytes each, because the buys are nearly all of
what gets stored: a thousand players buying three shares a round is about 8 million buys in
ten hours. round_log.py packs and saves each round; analysis/ reads them back.
"""
import struct
import zlib

LOG_FORMAT = 1  # saved with each round; change it whenever pack_path() or pack_buys() change what they write
NOT_SEEN = 0xFFFF  # stands in for the frame a page was showing when it didn't say
TIMING_FORMAT = 1  # saved inside each round's timing; change it whenever pack_timing() changes what it writes
NO_TIME = 0xFFFFFFFF  # stands in for a time in the timing log that there isn't one of
NO_BROADCAST = 0xFFFF  # stands in for a broadcast in the timing log that there isn't one of


def pack_path(path):
    """A path as bytes. Every position is in tenths of a pixel, which makes it a whole number,
    and is stored as its change from the frame before (the first as it is): x then y, each a
    2-byte signed integer, low byte first. The changes are small and repetitive, so zlib then
    squeezes a typical path to under a kilobyte."""
    tenths = [(round(x * 10), round(y * 10)) for x, y in path]
    numbers = list(tenths[0])
    for before, after in zip(tenths, tenths[1:]):
        numbers += [after[0] - before[0], after[1] - before[1]]
    return zlib.compress(struct.pack(f"<{len(numbers)}h", *numbers), 9)


def unpack_path(data):
    """The path that pack_path() packed, as [(x, y)]."""
    data = zlib.decompress(data)
    numbers = struct.unpack(f"<{len(data) // 2}h", data)
    path = []
    x = y = 0
    for i in range(0, len(numbers), 2):
        x, y = x + numbers[i], y + numbers[i + 1]
        path.append((x / 10, y / 10))
    return path


# How pack_buys() stores each fact about a buy: one column per fact, each holding one number
# per buy, in the order the buys were made. The letter is the size of the number, as the
# struct module spells it. All the frames, then all the lags, and so on: zlib squeezes columns
# of alike numbers much smaller than it would the same numbers buy by buy.
BUY_COLUMNS = (
    ("frames", "H"),  # 2 bytes. The server's frame at the buy, minus the frame of the buy before (the first as it is)
    ("lags", "h"),  # 2 bytes, signed. How many frames behind that the page said it was showing; NO_LAG if it didn't say
    ("keys", "I"),  # 4 bytes. The player's key, from the player_keys table
    ("sides", "B"),  # 1 byte. 0 for left, 1 for right
    ("prices", "B"),  # 1 byte. The price paid, in hundredths of a point
    ("points", "I"),  # 4 bytes. The player's points just before the buy, in hundredths
)
BUY_FORMAT = "<" + "".join("{n}" + letter for name, letter in BUY_COLUMNS)  # "<" means low byte first, no padding
BUY_BYTES = struct.calcsize(BUY_FORMAT.format(n=1))  # 14 bytes a buy, before zlib
NO_LAG = -0x8000


def pack_buys(buys):
    """A round's buys as bytes, given as [(frame, seen_frame, key, side, price, points)] in the
    order they were made. BUY_COLUMNS says how. Numbers too big for their column are capped."""
    columns = {name: [] for name, letter in BUY_COLUMNS}
    last_frame = 0
    for frame, seen_frame, key, side, price, points in buys:
        columns["frames"].append(min(0xFFFF, max(0, frame - last_frame)))
        last_frame = max(last_frame, frame)
        columns["lags"].append(NO_LAG if seen_frame == NOT_SEEN else min(0x7FFF, max(-0x7FFF, frame - seen_frame)))
        columns["keys"].append(key)
        columns["sides"].append(0 if side == "left" else 1)
        columns["prices"].append(round(price * 100))
        columns["points"].append(min(0xFFFFFFFF, max(0, round(points * 100))))
    numbers = [number for name, letter in BUY_COLUMNS for number in columns[name]]
    return zlib.compress(struct.pack(BUY_FORMAT.format(n=len(buys)), *numbers), 9)


def unpack_buys(data):
    """The buys that pack_buys() packed, in the same form. seen_frame is None where the page didn't say."""
    data = zlib.decompress(data)
    n = len(data) // BUY_BYTES
    numbers = struct.unpack(BUY_FORMAT.format(n=n), data)
    frames, lags, keys, sides, cents, points = (numbers[i * n:(i + 1) * n] for i in range(len(BUY_COLUMNS)))
    buys = []
    frame = 0
    for i in range(n):
        frame += frames[i]
        seen_frame = None if lags[i] == NO_LAG else frame - lags[i]
        buys.append((frame, seen_frame, keys[i], "right" if sides[i] else "left", cents[i] / 100, points[i] / 100))
    return buys


# The round's timing, for working out how fast each player answers what they are sent. All
# times are in microseconds since the drop, by the clock of the server that took them.
TIMING_BROADCAST_COLUMNS = (
    ("built", "I"),  # when broadcast() made the message and queued it for every page
    ("first", "I"),  # when it was handed to the network for the first page; NO_TIME if for none
    ("last", "I"),  # and for the last page
    ("queued", "I"),  # how many pages it was queued for
    ("written", "I"),  # how many it was written to before the round ended
)
TIMING_BUY_COLUMNS = (
    ("taken", "I"),  # when buy() took the buy, a moment after the request reached the server
    ("seen", "I"),  # when the newest broadcast of this round was written to the buyer's pages; NO_TIME if none had been
    ("seen_broadcast", "H"),  # which broadcast that was, counting from 0; NO_BROADCAST if none
)
TIMING_HEADER = "<BII"  # TIMING_FORMAT, how many broadcasts, how many buys


def pack_timing(broadcasts, buy_timing):
    """A round's timing as bytes, from game["broadcasts"] and game["buy_timing"], packed by
    column like pack_buys()."""
    def micro(seconds):
        return NO_TIME if seconds is None else min(NO_TIME - 1, max(0, round(seconds * 1_000_000)))

    numbers = [micro(b[0]) for b in broadcasts] + [micro(b[1]) for b in broadcasts] + [micro(b[2]) for b in broadcasts]
    numbers += [b[3] for b in broadcasts] + [b[4] for b in broadcasts]
    numbers += [micro(t[0]) for t in buy_timing] + [micro(t[1]) for t in buy_timing]
    numbers += [NO_BROADCAST if t[2] is None else min(NO_BROADCAST - 1, t[2]) for t in buy_timing]
    layout = TIMING_HEADER + "".join(f"{len(broadcasts)}{letter}" for name, letter in TIMING_BROADCAST_COLUMNS)
    layout += "".join(f"{len(buy_timing)}{letter}" for name, letter in TIMING_BUY_COLUMNS)
    return zlib.compress(struct.pack(layout, TIMING_FORMAT, len(broadcasts), len(buy_timing), *numbers), 9)


def unpack_timing(data):
    """The timing that pack_timing() packed, as {"broadcasts": [(built, first, last, queued,
    written)], "buys": [(taken, seen, seen_broadcast)]}, the times in seconds since the drop and
    None where there isn't one. "buys" lines up with the round's buys, in the same order."""
    data = zlib.decompress(data)
    version, n_broadcasts, n_buys = struct.unpack_from(TIMING_HEADER, data)
    if version != TIMING_FORMAT:
        raise ValueError(f"timing format {version}, which this version of the code can't unpack")
    layout = "<" + "".join(f"{n_broadcasts}{letter}" for name, letter in TIMING_BROADCAST_COLUMNS)
    layout += "".join(f"{n_buys}{letter}" for name, letter in TIMING_BUY_COLUMNS)
    numbers = struct.unpack_from(layout, data, struct.calcsize(TIMING_HEADER))
    columns, at = [], 0
    for count in [n_broadcasts] * len(TIMING_BROADCAST_COLUMNS) + [n_buys] * len(TIMING_BUY_COLUMNS):
        columns.append(numbers[at:at + count])
        at += count
    seconds = lambda micro: None if micro == NO_TIME else micro / 1_000_000
    built, first, last, queued, written, taken, seen, seen_broadcast = columns
    return {
        "broadcasts": [(seconds(b), seconds(f), seconds(l), q, w) for b, f, l, q, w in zip(built, first, last, queued, written)],
        "buys": [(seconds(t), seconds(s), None if i == NO_BROADCAST else i) for t, s, i in zip(taken, seen, seen_broadcast)],
    }
