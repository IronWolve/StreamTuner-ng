"""Times shown to the user: always the computer's own time zone, e.g. ``2026-11-06 1:45 PM``.

Stored values stay UTC (or epoch seconds); only what is drawn is converted. A one-digit hour is
padded with a digit-wide space so the minutes of a column line up.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

PAD = " "                       # a space as wide as a digit
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?)?\s*(Z|[+-]\d{2}:?\d{2})?$")


def clock(moment: datetime) -> str:
    """'1:45 PM' (hour padded to two characters)."""
    hour = moment.hour % 12 or 12
    return f"{PAD if hour < 10 else ''}{hour}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"


def stamp(moment: datetime) -> str:
    """'2026-11-06 1:45 PM'."""
    return f"{moment:%Y-%m-%d} {clock(moment)}"


def from_epoch(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds)               # naive local time


def parse_utc(text: object) -> datetime | None:
    """A UTC date/time text (ISO style, with or without 'T', 'Z' or an offset) or epoch seconds, as local time."""
    if isinstance(text, bool):
        return None
    if isinstance(text, (int, float)):
        try:
            return from_epoch(float(text))
        except (OverflowError, OSError, ValueError):
            return None
    match = _ISO.match(str(text or "").strip())
    if not match:
        return None
    year, month, day, hour, minute, second, zone = match.groups()
    try:
        moment = datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0), int(second or 0),
                          tzinfo=timezone.utc)
        if zone and zone != "Z":
            sign = 1 if zone[0] == "+" else -1
            digits = zone[1:].replace(":", "")
            moment -= sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:4]))
        return moment.astimezone().replace(tzinfo=None)
    except (ValueError, OverflowError, OSError):
        return None


def local_stamp(text: object) -> str:
    """A stored UTC date as local '2026-11-06 1:45 PM'; text that is not a date is returned unchanged."""
    moment = parse_utc(text)
    if moment is None:
        return str(text or "")
    if not isinstance(text, (int, float)) and not re.search(r"\d{2}:\d{2}", str(text)):
        return str(text).strip()                     # a date with no time of day is the same day everywhere
    return stamp(moment)


def day_heading(moment: datetime, today: date | None = None) -> str:
    today = today or date.today()
    if moment.date() == today:
        return "Today"
    if moment.date() == today - timedelta(days=1):
        return "Yesterday"
    return f"{moment:%A} {moment.day} {moment:%B %Y}"
