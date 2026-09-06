"""
Stage 9(d), commit C-1 — core.formatting.format_datetime_for_salon
(docs/DECISIONS.md § Step (d) decisions, guest-token delivery). Covers the
new `core.formatting.format_datetime_for_salon(when, salon_timezone) -> str`,
not yet written.

Written against the agreed design before any implementation exists — this
file is expected to fail on collection (ImportError: cannot import name
'format_datetime_for_salon' from 'core.formatting') until it is written,
same two-step-red shape as test_guest_token_derive.py's own docstring
describes.

The formatter converts a timezone-aware UTC datetime into a salon's local
timezone and renders it as "Fri, 26 Sep 2026, 14:00" — abbreviated weekday,
day, abbreviated month, full year, comma, 24-hour HH:MM. The English
weekday/month abbreviations must not depend on the server's system locale —
a naive strftime("%a, %d %b %Y, %H:%M") would silently localize on a
non-English-locale server, so that is pinned explicitly here
(test_english_names_regardless_of_locale), not assumed.

Fixed literal UTC datetimes throughout, never timezone.now().
"""

import datetime as dt
import locale

from core.formatting import format_datetime_for_salon

# 2026-09-26 is a Saturday. Kyiv is still on EEST (+03:00) in September —
# DST doesn't end until the last Sunday of October.
WHEN = dt.datetime(2026, 9, 26, 11, 0, tzinfo=dt.UTC)


def test_formats_utc_datetime_in_salon_timezone():
    result = format_datetime_for_salon(WHEN, "Europe/Kyiv")

    assert result == "Sat, 26 Sep 2026, 14:00"


def test_uses_24_hour_clock():
    result = format_datetime_for_salon(WHEN, "Europe/Kyiv")

    assert "14:00" in result
    assert "PM" not in result
    assert "AM" not in result


def test_different_timezone_shifts_the_local_time():
    in_kyiv = format_datetime_for_salon(WHEN, "Europe/Kyiv")
    in_utc = format_datetime_for_salon(WHEN, "UTC")

    assert in_kyiv != in_utc
    assert "14:00" in in_kyiv
    assert "11:00" in in_utc


def test_english_names_regardless_of_locale():
    original = locale.setlocale(locale.LC_TIME)
    try:
        try:
            locale.setlocale(locale.LC_TIME, "uk_UA.UTF-8")
        except locale.Error:
            pass  # locale not installed in this environment; the exact-string
            # assertion below is still the load-bearing check either way.
        result = format_datetime_for_salon(WHEN, "Europe/Kyiv")
    finally:
        locale.setlocale(locale.LC_TIME, original)

    assert result == "Sat, 26 Sep 2026, 14:00"
    assert "Sep" in result
