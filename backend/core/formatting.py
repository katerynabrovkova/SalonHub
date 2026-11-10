"""
Human-facing datetime formatting (docs/DECISIONS.md § Step (d) decisions,
guest-token delivery). The first human-facing datetime text in the
codebase — every other salon.timezone conversion (scheduling/views.py,
booking/services.py) only ever produces machine formats (isoformat(),
.date()). English by default; Ukrainian tables added in Stage 11.5,
selected via the ``lang`` argument.
"""

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

from core.i18n import SUPPORTED_LANGUAGES

# Full names for the booking emails (docs/DECISIONS.md "Notifications":
# BOOKING_CONFIRMED and APPOINTMENT_REMINDER use the full weekday and the
# month in the genitive). Ukrainian weekdays come in two cases: nominative
# for "Коли: субота, ...", genitive after "до" in the refund-deadline
# sentence ("до 14:00 п'ятниці, 13 листопада").
_WEEKDAYS_FULL = {
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "uk": ("понеділок", "вівторок", "середа", "четвер", "п'ятниця", "субота", "неділя"),
}
_WEEKDAYS_UK_GENITIVE = (
    "понеділка",
    "вівторка",
    "середи",
    "четверга",
    "п'ятниці",
    "суботи",
    "неділі",
)
_MONTHS_FULL = {
    "en": (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
    # Genitive, as after a day number: "14 листопада".
    "uk": (
        "січня",
        "лютого",
        "березня",
        "квітня",
        "травня",
        "червня",
        "липня",
        "серпня",
        "вересня",
        "жовтня",
        "листопада",
        "грудня",
    ),
}
_DURATION_UNITS = {"en": ("h", "min"), "uk": ("год", "хв")}

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


def _table_lang(lang: str | None) -> str:
    return lang if lang in SUPPORTED_LANGUAGES else "en"


def format_time_for_salon(when: dt.datetime, salon_timezone: str) -> str:
    """24-hour "HH:MM" in the salon's time zone."""
    local = when.astimezone(ZoneInfo(salon_timezone))
    return f"{local.hour:02d}:{local.minute:02d}"


def format_full_datetime_for_salon(
    when: dt.datetime, salon_timezone: str, lang: str | None = None
) -> str:
    """
    "субота, 14 листопада 2026, 14:00" / "Saturday, 14 November 2026,
    14:00" in the salon's time zone: full weekday, the day without a leading
    zero, the full month (genitive in Ukrainian), the year, the 24-hour time
    with a leading zero (docs/DECISIONS.md "Notifications", decided
    10.11.2026). Same fixed tables and language fallback as
    format_datetime_for_salon, never strftime.
    """
    table_lang = _table_lang(lang)
    local = when.astimezone(ZoneInfo(salon_timezone))
    weekday = _WEEKDAYS_FULL[table_lang][local.weekday()]
    month = _MONTHS_FULL[table_lang][local.month - 1]
    time = format_time_for_salon(when, salon_timezone)
    return f"{weekday}, {local.day} {month} {local.year}, {time}"


def format_deadline_for_salon(
    when: dt.datetime, salon_timezone: str, lang: str | None = None
) -> str:
    """
    The time and date that follow "до" / "until" in the refund-deadline
    sentence: "14:00 п'ятниці, 13 листопада" (weekday in the genitive) /
    "14:00 on Friday, 13 November". No year: the deadline is at most a few
    weeks ahead of the email.
    """
    table_lang = _table_lang(lang)
    local = when.astimezone(ZoneInfo(salon_timezone))
    time = format_time_for_salon(when, salon_timezone)
    month = _MONTHS_FULL[table_lang][local.month - 1]
    if table_lang == "uk":
        return f"{time} {_WEEKDAYS_UK_GENITIVE[local.weekday()]}, {local.day} {month}"
    return f"{time} on {_WEEKDAYS_FULL['en'][local.weekday()]}, {local.day} {month}"


def format_duration(minutes: int, lang: str | None = None) -> str:
    """90 → "1 год 30 хв" / "1 h 30 min"; whole hours and minutes-only
    durations leave out the zero part ("1 год", "45 хв")."""
    hour_unit, minute_unit = _DURATION_UNITS[_table_lang(lang)]
    hours, rest = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} {hour_unit}")
    if rest or not hours:
        parts.append(f"{rest} {minute_unit}")
    return " ".join(parts)


def format_amount(amount: Decimal, currency: str) -> str:
    """
    A money amount with its currency code: "24 UAH" for a whole amount,
    "24.50 UAH" otherwise (docs/DECISIONS.md "Notifications", decided
    10.11.2026; the same rule the assistant follows). Formats a value the
    backend already computed; never rounds it.
    """
    if amount == amount.to_integral_value():
        return f"{amount.to_integral_value()} {currency}"
    return f"{amount.quantize(Decimal('0.01'))} {currency}"
