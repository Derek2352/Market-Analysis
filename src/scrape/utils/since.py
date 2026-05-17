from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_RE = re.compile(r"^(\d+)([dhwm])$")


def parse_since(value: str) -> datetime:
    """Parse a relative window like '90d', '24h', '2w', '6m' → UTC datetime.

    Suffixes: d=day, h=hour, w=week, m=30 days (calendar approximation).
    """
    m = _RE.match(value.strip().lower())
    if not m:
        raise ValueError(
            f"Invalid --since value: {value!r}. Use e.g. '90d', '24h', '2w', '6m'."
        )
    n, unit = int(m.group(1)), m.group(2)
    delta = {
        "d": timedelta(days=n),
        "h": timedelta(hours=n),
        "w": timedelta(weeks=n),
        "m": timedelta(days=30 * n),
    }[unit]
    return datetime.now(timezone.utc) - delta
