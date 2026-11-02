"""One cell of a result as JSON can carry it (IR-02). Pure. Shared by the
API's responses and the answer cache's entries (step 14), so that a
cached row is the row the first answer gave."""

from datetime import date, datetime, time
from decimal import Decimal

Value = str | int | float | bool | None


def value(raw: object) -> Value:
    """One cell as JSON can carry it. A numeric is given as text, exact; a
    date in ISO form."""
    if raw is None or isinstance(raw, (bool, int, float, str)):
        return raw
    if isinstance(raw, Decimal):
        return str(raw)
    if isinstance(raw, (datetime, date, time)):
        return raw.isoformat()
    return str(raw)


def json_rows(rows) -> list[list[Value]]:
    return [[value(cell) for cell in row] for row in rows]
