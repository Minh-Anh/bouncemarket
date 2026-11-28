"""The rules of the game: the board, the physics of a drop, what a share costs, the pretend
traders, and which usernames are allowed.

Nothing in this package reads the environment, the database or the server's memory, so the
analysis scripts can use it on their own. A logged round is replayed by these rules, so a
change to the pricing or the counterparty needs a new RULES_VERSION (and analysis/reader.py
taught the old one), and a change to the board or the physics means old rounds only replay
under the commit they were played by.
"""
RULES_VERSION = 4  # saved with each round; change it whenever the pricing or the counterparty changes, so the analysis replays each round under its own rules
