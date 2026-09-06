"""
Human-facing datetime formatting (docs/DECISIONS.md § Step (d) decisions,
guest-token delivery). The first human-facing datetime text in the
codebase — every other salon.timezone conversion (scheduling/views.py,
booking/services.py) only ever produces machine formats (isoformat(),
.date()). English-only until Stage 11.5 localization.
"""

import datetime as dt
from zoneinfo import ZoneInfo

_WEEKDAY_ABBREVIATIONS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTH_ABBREVIATIONS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def format_datetime_for_salon(when: dt.datetime, salon_timezone: str) -> str:
    """
    Converts `when` into the salon's local timezone and renders it like
    "Sat, 26 Sep 2026, 14:00" — abbreviated weekday, day, abbreviated
    month, full year, comma, 24-hour HH:MM.

    Deliberately does not use strftime's %a/%b: those pull weekday/month
    names from the server's system locale, so the same code would silently
    emit non-English names on a non-English-locale server. The weekday and
    month names are instead looked up from the fixed English tables above,
    keyed by the converted datetime's .weekday()/.month, so the result is
    locale-proof by construction.
    """
    local = when.astimezone(ZoneInfo(salon_timezone))
    weekday = _WEEKDAY_ABBREVIATIONS[local.weekday()]
    month = _MONTH_ABBREVIATIONS[local.month - 1]
    return f"{weekday}, {local.day:02d} {month} {local.year}, {local.hour:02d}:{local.minute:02d}"
