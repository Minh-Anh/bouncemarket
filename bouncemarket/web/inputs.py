"""Reading what a request sent."""
import uuid


def parse_id(text):
    """Player ids are UUIDs, the random ids browsers make. Returns None for anything else,
    including something that isn't text at all, or is missing."""
    if not isinstance(text, str):
        return None
    try:
        return str(uuid.UUID(text))
    except ValueError:
        return None


async def read_json(request):
    """The JSON object a request sent, or an empty dict if it sent anything else (nothing,
    broken JSON, a list...). So a bad request gets the handler's own 400 and not a crash."""
    try:
        data = await request.json()
    except ValueError:  # broken JSON, or bytes that aren't text
        return {}
    return data if isinstance(data, dict) else {}
