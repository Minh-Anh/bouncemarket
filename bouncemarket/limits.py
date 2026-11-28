"""Limits on how many pages and new guests one internet address may have, so that one computer
can't slow the game for everyone or make itself free points. See docs/limits.md.

The limits on buys and resets are in buy() and reset_points() (web/play.py), where they apply.
"""
import asyncio
import collections
import ipaddress
import time

from starlette.responses import JSONResponse

from bouncemarket import config, state


def client_address(request):
    """The internet address a page is connecting from, to count the pages each address has
    open. None if we can't tell, and then the page isn't counted against any address.

    We never see the address itself: on Render every connection reaches us from Render's own
    proxy, and the visitor's address is in a header added on the way. CF-Connecting-IP is set
    by Cloudflare, which Render puts in front of every service, whatever the visitor sent, so
    it can't be forged. Failing that, the first address in X-Forwarded-For, which a visitor
    can forge by sending the header themselves: the limit on all pages together (MAX_STREAMS)
    still holds then. With neither header, as on your own computer, there is nothing to go by.

    An IPv6 address is cut down to its network, the first half (a /64): the second half is
    the visitor's own to choose, so one visitor has as many full addresses as they like.
    """
    text = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[0]
    try:
        address = ipaddress.ip_address(text.strip())
    except ValueError:
        return None
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped  # an IPv4 address written the IPv6 way, ::ffff:1.2.3.4
    if address.version == 6:
        return str(ipaddress.ip_network((address, 64), strict=False))
    return str(address)


def take_slot(address):
    """Count one more page for this address. Returns None if it may open, or the answer to
    send if it may not: the game is full (MAX_STREAMS), or this address already has
    MAX_STREAMS_PER_ADDRESS open. Without these, one computer can open thousands of streams,
    and every broadcast is then written thousands of times, which slows the game for everyone.

    A page that is turned away shows a message and asks again by itself (connect() in
    frontend/game.js). The slot is counted from this moment, before the player is even looked up,
    so a burst of connections arriving together can't all slip in under the limit. It is
    given back by give_back_slot(), when the stream ends or was never started.
    """
    if state.slots_taken >= config.MAX_STREAMS:
        problem, status = "the game is full right now.", 503
    elif address is not None and config.MAX_STREAMS_PER_ADDRESS and state.slots[address] >= config.MAX_STREAMS_PER_ADDRESS:
        problem, status = "too many pages are open from your network.", 429
    else:
        state.slots[address] += 1
        state.slots_taken += 1
        return None
    state.turned_away[address] += 1
    return JSONResponse({"error": problem}, status_code=status, headers={"Retry-After": "15"})


def give_back_slot(address):
    state.slots_taken -= 1
    state.slots[address] -= 1
    if state.slots[address] <= 0:
        del state.slots[address]


def admit_new_guest(address):
    """Count one more new guest for this address, and say whether they may come in: at most
    MAX_NEW_GUESTS_PER_ADDRESS in any hour. Every new guest starts with 10 points, and a new
    browser is a new guest, so without this one person could make as many as they liked and
    spend their points pushing prices around for another player to take (see
    docs/limits.md). A guest turned away isn't counted, and their page asks again by
    itself, like one turned away by take_slot()."""
    if not config.MAX_NEW_GUESTS_PER_ADDRESS or address is None:
        return True
    now = time.time()
    times = state.recent_new_guests.setdefault(address, collections.deque())
    while times and times[0] <= now - 3600:
        times.popleft()
    if len(times) >= config.MAX_NEW_GUESTS_PER_ADDRESS:
        return False
    times.append(now)
    return True


async def log_streams():
    """Every STREAM_LOG_SECONDS, write to the log who has many pages open and who was turned
    away, if anyone has or was: enough to tell one computer opening thousands of pages from a
    crowd of people. Addresses with only a few pages open are never written down."""
    while True:
        await asyncio.sleep(config.STREAM_LOG_SECONDS)
        many = max(5, config.MAX_STREAMS_PER_ADDRESS // 2)
        busiest = [(address, count) for address, count in state.slots.most_common(6) if address is not None and count >= many]
        if not busiest and not state.turned_away and state.slots[None] < many:
            continue  # many with no address is worth saying too: on Render it would mean the headers aren't what client_address() expects
        said = [f"{state.slots_taken} pages open from {len(state.slots) - (None in state.slots)} addresses (the limits are {config.MAX_STREAMS}, and {config.MAX_STREAMS_PER_ADDRESS} from one address)."]
        if state.slots[None]:
            said.append(f"{state.slots[None]} of them came with no address, and aren't counted against one.")
        if busiest:
            said.append("Most from one address: " + ", ".join(f"{address} has {count}" for address, count in busiest[:5]) + ".")
        if state.turned_away:
            worst = ", ".join(f"{count} from {address or 'no address'}" for address, count in state.turned_away.most_common(5))
            said.append(f"Turned away {sum(state.turned_away.values())} connections from {len(state.turned_away)} addresses in the last {config.STREAM_LOG_SECONDS} seconds: {worst}.")
            state.turned_away.clear()
        print(" ".join(said))
