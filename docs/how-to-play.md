# How to play

Everyone who opens the page watches the same ball and trades in the same market. A new ball drops 5 seconds after the last one lands.

You start with 10 points. Each "buy 1 share" button buys one share on that side at the price shown. A share pays 1 point if the ball lands on its side and nothing if it doesn't. So a share bought at 0.30 makes 0.70 if it wins and loses 0.30 if it doesn't. While you hold shares, each side's card shows what you would net if the ball lands on that side: your shares on that side minus everything you paid this round.

You can buy only while the ball is falling: from the moment it drops until just before it passes the last row of pins. Shares can't be sold back; you hold them until the ball lands.

Both sides start at 0.50 each round, and the two prices always add up to 1. A side's price depends only on how many more shares have been bought on it than on the other side this round. A buy on Left and a buy on Right cancel out, as in a real market, where buying one side is the same as someone selling it to you. So a share moves the price by the same amount at the end of a round as at the start, and a few traders piling in late can swing it hard. A side 7 shares ahead costs 0.73 and 14 ahead costs 0.88, along a logistic curve, the one prediction-market makers use (`PRICE_SOFTNESS` in `bouncemarket/game/pricing.py`; higher makes each share move the price less). Every buy pushes that side's price up and the other side's down. Being right early, while a side is still cheap, pays more than piling in once everyone agrees.

The price on each side's card is what a share costs you, which is a little more than the market's chance for that side, shown in the odds bar. Your buy moves the price, so you pay the average price along that move, not the price before it: the cost a logarithmic market maker charges (`costs_for_net()` in `bouncemarket/game/pricing.py`), rounded to the nearest cent. At 0.50 a side, a share costs 0.52. Charging the price before the move gave away about half a price step on every share, and let a script buy Left at 0.50 and then Right at 0.46, for a pair sure to pay 1.00. At the market maker's cost, equal shares on both sides always cost exactly what they are sure to pay, so there is nothing to gain from buying both sides, and the house takes no cut.

A share costs at least 0.01 and at most 1.00. A side 37 shares ahead costs 1.00, which is all it can pay back, so once a ball is plainly going one way there is nothing left to make by buying it. Until rules version 4 the most was 0.99, and every share past that point still cost 0.99: a few players bought thousands a round of the side that was all but sure, winning a cent nearly every time (see [After the update](player-study.md#after-the-update-what-the-timing-log-shows)). Now their own buys carry the price to 1.00 within a few shares. The least stays at 0.01, since a free share would be worth buying however unlikely its side. Beyond 37 ahead, that puts a Left share and a Right share a cent over the 1 point the pair pays.

Each player can buy at most 10 shares in any one second (`MAX_BUYS_PER_SECOND`). A person clicks 5 to 8.5 times a second; a buy past the limit is turned down.

There is also a counterparty, so the market moves even with nobody else on: five pretend traders who buy the way a person might. Each guesses where the ball will land with a naive rule: the further it is from the middle and the lower it is, the surer they get. Each looks at the ball and the price; if the price is off from their guess by 0.03 or more, they buy one share of the side that looks too cheap and look again a moment later, at a person's clicking speed (5 to 8.5 clicks a second), and otherwise they look again after a short pause. That makes bursts of buying, sometimes two traders in the same instant. Their buying drifts the price towards where the ball seems to be heading, hardest near the bottom, and they push back when players pile onto the side the ball isn't on.

The pretend traders make way for real people. Each different player who buys in a round takes one trader's place for the rest of that round, so once five people have bought, the pretend traders have stopped and the market is all human. Players are counted by their buys, not by who has the page open, so a crowd that's only watching still gets a moving market.

The counterparty is also the seller of every share you buy: it keeps what you pay and pays out the winners.

Anyone can play straight away, and is ranked on the leaderboard from the start: until you claim a name you appear as "you", where your points would put you, with a "set username" button. Other people's guests aren't shown, only named players and you. Names are unique, ignoring capitals, and from then on your points are saved. The pencil button after your name on the leaderboard changes it to any name nobody else is using, and your points stay with you.

A username is 1 to 16 plain letters (A to Z, either case), numbers, `-` or `_`, with at least one letter or number. The page drops any other character as you type. Some names are reserved because they would look like the site itself talking (`you`, `guest`, `admin`, `moderator` and the like), and a word filter turns down slurs, obscenities and hate symbols, including the usual disguises such as numbers for letters. The rules are in `bouncemarket/game/names.py`. They are checked only when a name is set or changed, so a name saved before a rule existed stays as it is, unless the owner changes it (see [Moderating names](moderation.md)). If the owner has changed your name, it is locked and you can't change it yourself.

Anyone can start over with 10 points, whatever they have now, named or not. The circling-arrows button on your own leaderboard row does it, asking first only if you have more than 10 points to lose. If you can't afford a share on either side and hold none, a "reset to 10" button also appears under the buy buttons. A reset can't be used while you hold shares in the round being played, or someone could spend everything pushing a price around, start over, and do it again for free. Nor more than once every 10 minutes, or more than 6 times an hour from one internet address (see [Limits on resets and buys](limits.md#limits-on-resets-and-buys)). A guest's reset changes nothing in the database and nothing on anyone else's leaderboard, only their own.

## Your best round

Once a round makes you more than 1.00 point, a card for it appears above the leaderboard: the profit, a chart of the price over the round with a mark for each of your buys (circles for Left, triangles for Right), what you bought and at what average price, where the ball landed, and your shares, cost and payout. Profit is everything your winning shares paid out minus everything you paid that round, on both sides. Only a round that beats your best so far replaces the card; a tie doesn't. A new best is marked "new best round" until the next round has been played, inside the card, so it never covers the board or the buy buttons.

The card stays when you reload the page or reset to 10: a reset changes your points, not your record. Guests get one too.

The trash can at the card's top right takes the card away, after asking "delete this card?" inside the card itself. It's hidden while the card is marked "new best round", and appears in that spot once the mark goes. Your record starts over, so your next round over 1.00 makes a new card, however small. Links you've already shared keep working.

The "share" button opens a small menu:

- **copy text** copies a line and a link, such as:

  ```
  bouncemarket: bought Left x6 at avg 0.14, landed Left, +5.16 in one round https://bouncemarket.minhanhday.com/r/k3J9xQ2a
  ```

- **copy image** copies a picture of the card, with "bouncemarket" and the site's address on it, for pasting where a link won't do. It's left out in browsers that can't put a picture on the clipboard.
- **share…** opens the device's own share sheet, in browsers that have one.

The link opens a public page for anyone, with nothing to sign in to. It plays the round again: the ball falls while the card's price line draws along with it, each of your buys pops onto the chart as you made it, and a line under the board says what's happening ("bought left at 0.13", "trading closed", "landed left · +5.16 points"). Then the "play" button appears. "skip" jumps to the end and "watch again" starts over. With reduced motion turned on in the device's settings, the page shows the finished card without the replay. Posted in a chat app or social feed, the link unfurls with the same picture of the card. Links keep working after you beat that round.
