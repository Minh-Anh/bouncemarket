# bouncemarket

A ball drops from a random spot along the top of a board of pins, bounces its way down, and lands in one of two buckets. Everyone who opens the page watches the same ball and trades on where it will land.

https://bouncemarket.minhanhday.com/ · [How to play](docs/how-to-play.md)

## Set up

1. Install [uv](https://docs.astral.sh/uv/): `curl -LsSf https://astral.sh/uv/install.sh | sh`. It installs Python (3.11 or newer) and the dependencies on first run.
2. Make a [Supabase](https://supabase.com) project, which stores the named players and the round log, and run [`db/schema.sql`](db/schema.sql) in its SQL editor.
3. Create a file called `.env` in this folder (git ignores it) with the project's URL and its secret key, both found under the project's API settings:

   ```
   SUPABASE_URL=https://your-project.supabase.co
   SUPABASE_KEY=your-secret-key
   ADMIN_KEY=a-long-random-key
   ```

   Use the secret key (also called the service role key), not the publishable one, and never put it in a page or commit it. `ADMIN_KEY` is optional: it is the owner's key for [moderating names](docs/moderation.md), and without it those routes don't exist. Make it long and random, for example with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.

## Run

```
uv run --env-file .env app.py
```

Then open http://localhost:8000. The [server's README](bouncemarket/README.md#settings) lists the settings.

## Test

```
uv run pytest                 # everything, about a minute
uv run pytest -m "not e2e"    # only the quick ones
```

Nothing in the tests reaches Supabase. See [`tests/`](tests/README.md).

## Deploy

The app is hosted on [Render](https://render.com), and every push to `main` redeploys it. An update doesn't stop the game: the old server hands it to the new one, ball in the air and all. When an update changes `db/schema.sql`, run it in Supabase before pushing. See [Deploys](docs/deploys.md).

The dependencies are pinned in `pyproject.toml`, which uv reads, and in `requirements.txt`, which Render installs. Change both together.

## What is where

| | |
| --- | --- |
| `app.py` | Starts the server. |
| [`bouncemarket/`](bouncemarket/README.md) | The server: the game, the market, the streams to the pages, the database. |
| [`frontend/`](frontend/README.md) | The pages: HTML, CSS and JavaScript, sent as they are. |
| [`db/`](db/README.md) | The database's tables. |
| [`analysis/`](analysis/README.md) | Reads the round log back, and the scripts behind the study of how people play. |
| [`tests/`](tests/README.md) | The tests. |
| [`docs/`](docs/README.md) | How the game is played, how it works, and how to run it. |
| `render.yaml`, `requirements.txt` | What Render builds and runs. |
