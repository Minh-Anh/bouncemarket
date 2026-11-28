# Tests

```
uv run pytest                 # everything, about a minute
uv run pytest -m "not e2e"    # only the quick ones
```

Nothing here reaches Supabase: the end-to-end tests give the server a fake database.

| | |
| --- | --- |
| `test_game.py` | The rules in `bouncemarket/game/`: a known drop replays exactly, the prices, the counterparty, the username filter. |
| `test_packing.py` | A round's path, buys and timing survive being packed for the round log. |
| `test_cards.py` | A best-round card's words and picture. |
| `test_project.py` | `pyproject.toml` and `requirements.txt` list the same dependencies. |
| `test_e2e.py` | Runs each test in `e2e/`, in a process of its own. |
| `e2e/limits.py` | The whole server, with players at two made-up internet addresses: the [limits](../docs/limits.md) on buys, resets and new guests. |
| `e2e/round_log.py` | The whole server, with six pages buying over two rounds: the round log and its timing, the shuffled send order, exact replays, the hidden nudges, and a handover. |

An end-to-end test can be run on its own, and says what it checks as it goes:

```
uv run -m tests.e2e.limits
uv run -m tests.e2e.round_log --timing-column
```
