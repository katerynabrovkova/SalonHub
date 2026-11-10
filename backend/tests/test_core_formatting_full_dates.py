"""
Full dates and durations for the booking emails (docs/DECISIONS.md
"Notifications": `BOOKING_CONFIRMED` and `APPOINTMENT_REMINDER` use the full
weekday and the genitive month; register "In progress: guest manage page",
sub-step (b)).

The functions under test do not exist yet. They are reached through the
module (`formatting.<name>`), not imported by name, so each test fails on its
own with AttributeError instead of the whole file failing at collection.
Names this file expects:

- `format_full_datetime_for_salon(when, salon_timezone, lang)`:
  "субота, 14 листопада 2026, 14:00" / "Saturday, 14 November 2026, 14:00";
  the day without a leading zero, the hour with one (decided 10.11.2026).
- `format_duration(minutes, lang)`: "1 год 30 хв" / "1 h 30 min".

Fixed literal datetimes only, never the real clock.
"""

import datetime as dt

import pytest

from core import formatting

# 12:00 UTC is 14:00 in Kyiv in November (EET, +02:00). 2026-11-14 is a Saturday.
SATURDAY_NOON_UTC = dt.datetime(2026, 11, 14, 12, 0, tzinfo=dt.UTC)


# --- 1. Full date in the salon's time zone ------------------------------------------


def test_full_datetime_in_ukrainian_and_english_in_the_salon_time_zone():
    uk = formatting.format_full_datetime_for_salon(SATURDAY_NOON_UTC, "Europe/Kyiv", lang="uk")
    en = formatting.format_full_datetime_for_salon(SATURDAY_NOON_UTC, "Europe/Kyiv", lang="en")
    in_utc = formatting.format_full_datetime_for_salon(SATURDAY_NOON_UTC, "UTC", lang="en")

    assert uk == "субота, 14 листопада 2026, 14:00"
    assert en == "Saturday, 14 November 2026, 14:00"
    # Same instant, another zone: proves the salon's zone is applied.
    assert in_utc == "Saturday, 14 November 2026, 12:00"


# --- 2. Ukrainian months in the genitive --------------------------------------------


@pytest.mark.parametrize(
    ("month", "genitive"),
    [
        (1, "січня"),
        (2, "лютого"),
        (3, "березня"),
        (4, "квітня"),
        (5, "травня"),
        (6, "червня"),
        (7, "липня"),
        (8, "серпня"),
        (9, "вересня"),
        (10, "жовтня"),
        (11, "листопада"),
        (12, "грудня"),
    ],
)
def test_every_ukrainian_month_is_in_the_genitive(month, genitive):
    # 12:00 UTC on the 15th stays on the 15th in Kyiv in every month.
    when = dt.datetime(2026, month, 15, 12, 0, tzinfo=dt.UTC)

    result = formatting.format_full_datetime_for_salon(when, "Europe/Kyiv", lang="uk")

    assert f", 15 {genitive} 2026, " in result


# --- 3. Duration --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("minutes", "lang", "expected"),
    [
        (90, "uk", "1 год 30 хв"),
        (60, "uk", "1 год"),
        (45, "uk", "45 хв"),
        (90, "en", "1 h 30 min"),
        (60, "en", "1 h"),
        (45, "en", "45 min"),
    ],
)
def test_duration_in_hours_and_minutes(minutes, lang, expected):
    assert formatting.format_duration(minutes, lang=lang) == expected
