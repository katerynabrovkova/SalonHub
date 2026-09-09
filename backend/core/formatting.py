"""
Human-facing datetime formatting (docs/DECISIONS.md § Step (d) decisions,
guest-token delivery). The first human-facing datetime text in the
codebase — every other salon.timezone conversion (scheduling/views.py,
booking/services.py) only ever produces machine formats (isoformat(),
.date()). English by default; Ukrainian tables added in Stage 11.5,
selected via the ``lang`` argument.
"""

import datetime as dt
from zoneinfo import ZoneInfo

from core.i18n import SUPPORTED_LANGUAGES

_WEEKDAY_ABBREVIATIONS = {
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    "uk": ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"),
}
_MONTH_ABBREVIATIONS = {
    "en": (
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
    ),
    "uk": (
        "січ.",
        "лют.",
        "бер.",
        "квіт.",
        "трав.",
        "черв.",
        "лип.",
        "серп.",
        "вер.",
        "жовт.",
        "лист.",
        "груд.",
    ),
}


def format_datetime_for_salon(
    when: dt.datetime, salon_timezone: str, lang: str | None = None
) -> str:
    """
    Converts `when` into the salon's local timezone and renders it like
    "Sat, 26 Sep 2026, 14:00" — abbreviated weekday, day, abbreviated
    month, full year, comma, 24-hour HH:MM.

    ``lang`` selects the weekday/month table: "uk" renders Ukrainian
    abbreviations ("Сб, 26 вер. 2026, 14:00"); None, "en", or any code not
    in SUPPORTED_LANGUAGES all render English — the same no-request /
    fallback convention as core.i18n.resolve_translation. The component
    order is identical across languages (docs/DECISIONS.md § "Date
    formatting in localized emails").

    Deliberately does not use strftime's %a/%b: those pull weekday/month
    names from the server's system locale, so the same code would silently
    emit non-English names on a non-English-locale server. The weekday and
    month names are instead looked up from the fixed tables above, keyed by
    the converted datetime's .weekday()/.month, so the result is
    locale-proof by construction.
    """
    table_lang = lang if lang in SUPPORTED_LANGUAGES else "en"
    local = when.astimezone(ZoneInfo(salon_timezone))
    weekday = _WEEKDAY_ABBREVIATIONS[table_lang][local.weekday()]
    month = _MONTH_ABBREVIATIONS[table_lang][local.month - 1]
    return f"{weekday}, {local.day:02d} {month} {local.year}, {local.hour:02d}:{local.minute:02d}"
