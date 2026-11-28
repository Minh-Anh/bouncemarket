"""What a share costs: a logarithmic market maker, where only the difference between the shares
bought on each side counts. Change it, and RULES_VERSION (in this package's __init__.py) with it.
"""
import math

PRICE_SOFTNESS = 7  # how many more shares one side needs than the other for its price to reach 0.73 (see prices_for()); higher makes each share move the price less


def prices_for(left, right):
    """What one share on each side costs once this many shares have been bought on each side,
    by players and the counterparty. A share pays 1 point if its side wins. See prices_for_net()."""
    return prices_for_net(left - right)


def prices_for_net(net):
    """What one share on each side costs when net more shares have been bought on the left than
    on the right (fewer, if net is below 0).

    Only the difference counts, as in a real market, where buying one side is the same as
    someone selling it to you: a buy on the left and a buy on the right cancel out. So a share
    moves the price as much at the end of a round as at the start, however much has been bought
    before. The two sides start at 0.50, and a side PRICE_SOFTNESS shares ahead costs 0.73, one
    twice that far ahead 0.88 (a logistic curve, the one prediction-market makers use). The two
    prices always add up to 1.
    """
    left_price = round(1 / (1 + math.exp(-net / PRICE_SOFTNESS)), 2)
    left_price = min(0.99, max(0.01, left_price))  # never free, and never a sure loss
    return {"left": left_price, "right": round(1 - left_price, 2)}


def costs_for(left, right):
    """What buying the next share on each side is charged, once this many shares have been bought
    on each side. See costs_for_net()."""
    return costs_for_net(left - right)


def costs_for_net(net):
    """What buying the next share on each side is charged, when net more shares have been bought
    on the left than on the right.

    A buy moves the price, so the share is charged the average of the price along that move,
    not the price before it: the cost a logarithmic market maker charges, PRICE_SOFTNESS * ln(1 - p
    + p * e^(1 / PRICE_SOFTNESS)) for a side whose price is p. Charging the price before the move
    gave away half a price step on every share, and made a Left share and then a Right share cost
    less than the 1 point the pair is sure to pay.

    At this cost, buying both sides gains nothing. Equal shares on both sides bring the price back
    where it started, so the buys pair off: a Left buy at some net with a Right buy back from one
    above it. The two exact costs add up to exactly 1, and rounded to the cent they still do, as one
    is 1 minus the other. So any set of buys that's sure to pay costs exactly what it pays, and
    rounding to the nearest cent, not up, keeps the house from taking a cut. At 0.50 a side, a
    share costs 0.52.

    A share can cost as much as 1.00, once its side is 37 shares ahead (at a PRICE_SOFTNESS of
    7): it then pays back at most what it cost, so a ball that is plainly going one way is worth
    nothing more to buy. Until rules version 4 the most was 0.99, and since every share past that
    point still cost 0.99, players bought thousands a round of a side that nearly always won, a
    cent at a time. Now their own buys carry the price to 1.00 within a few shares. The least is
    still 0.01, so no share is ever free: a free share would be worth buying however unlikely.
    Past 37 ahead, that puts a pair of opposite buys a cent over the 1 point they pay.
    """
    left_chance = 1 / (1 + math.exp(-net / PRICE_SOFTNESS))
    step = math.exp(1 / PRICE_SOFTNESS)

    def cost(chance):
        exact = PRICE_SOFTNESS * math.log(1 - chance + chance * step)
        return min(1.0, max(0.01, round(exact, 2)))  # never free

    return {"left": cost(left_chance), "right": cost(1 - left_chance)}
