"""The database: a Supabase project, reached over its web API. db/schema.sql makes its tables."""
import httpx

from bouncemarket import config

if not (config.SUPABASE_URL and config.SUPABASE_KEY):
    raise SystemExit("SUPABASE_URL and SUPABASE_KEY aren't set. Put them in .env and run with uv run --env-file .env (see the README).")

# Named players are kept in a Supabase database table, so their points survive a restart.
# We talk to it over its web API. The secret key gives full access, so it stays on the
# server (in an environment variable) and the page never talks to Supabase itself.
client = httpx.AsyncClient(
    base_url=config.SUPABASE_URL + "/rest/v1",
    headers={"apikey": config.SUPABASE_KEY, "Authorization": "Bearer " + config.SUPABASE_KEY},
    timeout=10,
)


async def read_all(columns, table="/players", order="id", where=None):
    """Every row of the players table (or another table), with the columns asked for, e.g.
    "id,name,points", in the order of a column that is unique, so no row is missed or read twice.
    where picks rows, in the database's form, e.g. {"cleared": "eq.false"}.
    The database hands rows over at most PAGE_SIZE at a time, so we ask page by page."""
    rows = []
    while True:
        params = {**(where or {}), "select": columns, "limit": config.PAGE_SIZE, "offset": len(rows), "order": order}
        response = await client.get(table, params=params)
        response.raise_for_status()
        page = response.json()
        rows += page
        if len(page) < config.PAGE_SIZE:
            return rows


def to_bytea(data):
    """Bytes in the text form the database takes for a bytea (raw bytes) column: \\x and then hexadecimal."""
    return "\\x" + data.hex()


def database_problem(error):
    """What went wrong talking to the database, in a few words, for the owner."""
    if isinstance(error, httpx.HTTPStatusError):
        text = error.response.text[:300]
        hint = " run db/schema.sql in the supabase sql editor to add it." if "name_locked" in text else ""
        return f"the database answered {error.response.status_code}: {text}{hint}"
    return "the database couldn't be reached: " + repr(error)
