"""
Stage 16 sub-step 10c — scheduling.services.no_free_start_reason
(docs/DECISIONS.md § "Sub-step 10c: `check_availability` explains empty
results").

Why a requested window has no free start, for one specialist or for "any"
(specialist=None): None when a free start exists; otherwise, in this order,
"outside_booking_window" (no part of the window lies within lead time and
max days in advance), "day_off" (no working time on any requested day:
no hours, or all of it time off), "outside_hours" (works on a requested
day, but no start fits even with appointments ignored: working hours minus
time off, with duration, buffer and granularity), "fully_booked". Sub-step
10e split the former "not_working" into day_off and outside_hours.

The salon runs on Asia/Tbilisi (UTC+4, no DST) with 60-minute slots and a
60-minute service without buffer. NOW is Tuesday 2030-01-01 00:00 UTC
(04:00 local), frozen; the default lead time is 3 hours. Saturday is
2030-01-12.

The helper is imported inside each test, so every test fails on its own
before it exists.
"""

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from catalog.models import Service
from core.tenancy import tenant_context
from specialists.models import DayOfWeek, Specialist
from tests.conftest import make_appointment, make_time_off, make_working_hours

pytestmark = pytest.mark.django_db

NOW = dt.datetime(2030, 1, 1, tzinfo=dt.UTC)
TBILISI = ZoneInfo("Asia/Tbilisi")
SATURDAY = dt.date(2030, 1, 12)


@pytest.fixture(autouse=True)
def _frozen_now(monkeypatch):
    monkeypatch.setattr(timezone, "now", lambda: NOW)


@pytest.fixture
def tbilisi_salon(salon):
    salon.timezone = "Asia/Tbilisi"
    salon.slot_granularity_minutes = 60
    salon.save(update_fields=["timezone", "slot_granularity_minutes"])
    return salon


@pytest.fixture
def hour_service(tbilisi_salon, service_category):
    with tenant_context(tbilisi_salon.id):
        return Service.objects.create(
            salon=tbilisi_salon,
            category=service_category,
            name={"en": "Gel polish"},
            duration_minutes=60,
            price="300.00",
        )


def _specialist(salon, name: str, services=(), **extra) -> Specialist:
    with tenant_context(salon.id):
        specialist = Specialist.objects.create(salon=salon, name={"en": name}, **extra)
        if services:
            specialist.services.set(services, through_defaults={"salon_id": salon.id})
        return specialist


def _hours(salon, specialist, day: DayOfWeek, start: dt.time, end: dt.time) -> None:
    make_working_hours(
        salon=salon, specialist=specialist, day_of_week=day, start_time=start, end_time=end
    )


def _at(date: dt.date, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime.combine(date, dt.time(hour, minute), tzinfo=TBILISI)


def _reason(salon, service, specialist, **window: Any) -> str | None:
    from scheduling.services import no_free_start_reason

    window.setdefault("date_from", SATURDAY)
    window.setdefault("date_to", SATURDAY)
    window.setdefault("weekdays", None)
    window.setdefault("time_from", None)
    window.setdefault("time_to", None)
    with tenant_context(salon.id):
        return no_free_start_reason(
            salon=salon, service=service, specialist=specialist, now=NOW, **window
        )


def test_none_when_a_free_start_exists(tbilisi_salon, hour_service):
    jane = _specialist(tbilisi_salon, "Jane", [hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.SATURDAY, dt.time(9), dt.time(10))

    assert _reason(tbilisi_salon, hour_service, jane) is None


def test_day_off_when_no_hours_on_the_requested_weekday(tbilisi_salon, hour_service):
    jane = _specialist(tbilisi_salon, "Jane", [hour_service])
    for day in (DayOfWeek.MONDAY, DayOfWeek.TUESDAY, DayOfWeek.WEDNESDAY):
        _hours(tbilisi_salon, jane, day, dt.time(9), dt.time(18))

    reason = _reason(
        tbilisi_salon,
        hour_service,
        jane,
        date_from=dt.date(2030, 1, 7),
        date_to=dt.date(2030, 1, 13),
        weekdays=frozenset({DayOfWeek.SATURDAY}),
    )

    assert reason == "day_off"


def test_day_off_when_time_off_covers_the_working_hours(tbilisi_salon, hour_service):
    jane = _specialist(tbilisi_salon, "Jane", [hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.SATURDAY, dt.time(9), dt.time(12))
    make_time_off(
        salon=tbilisi_salon,
        specialist=jane,
        start_datetime=_at(SATURDAY, 8),
        end_datetime=_at(SATURDAY, 13),
    )

    assert _reason(tbilisi_salon, hour_service, jane) == "day_off"


def test_outside_hours_when_the_time_window_is_outside_working_hours(tbilisi_salon, hour_service):
    jane = _specialist(tbilisi_salon, "Jane", [hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.SATURDAY, dt.time(9), dt.time(12))

    reason = _reason(tbilisi_salon, hour_service, jane, time_from=dt.time(18), time_to=dt.time(21))

    assert reason == "outside_hours"


def test_outside_hours_when_the_working_hours_are_too_short_for_the_service(
    tbilisi_salon, hour_service
):
    jane = _specialist(tbilisi_salon, "Jane", [hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.SATURDAY, dt.time(9), dt.time(9, 30))

    assert _reason(tbilisi_salon, hour_service, jane) == "outside_hours"


def test_fully_booked_when_appointments_fill_the_working_hours(
    tbilisi_salon, hour_service, customer
):
    jane = _specialist(tbilisi_salon, "Jane", [hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.SATURDAY, dt.time(9), dt.time(11))
    for hour in (9, 10):
        make_appointment(
            salon=tbilisi_salon,
            customer=customer,
            specialist=jane,
            service=hour_service,
            start=_at(SATURDAY, hour),
        )

    assert _reason(tbilisi_salon, hour_service, jane) == "fully_booked"


def test_outside_booking_window_within_the_lead_time(tbilisi_salon, hour_service):
    # NOW is 04:00 local on Tuesday 2030-01-01; the 3-hour lead time ends at 07:00.
    jane = _specialist(tbilisi_salon, "Jane", [hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.TUESDAY, dt.time(0), dt.time(23))

    reason = _reason(
        tbilisi_salon,
        hour_service,
        jane,
        date_from=dt.date(2030, 1, 1),
        date_to=dt.date(2030, 1, 1),
        time_from=dt.time(4),
        time_to=dt.time(7),
    )

    assert reason == "outside_booking_window"


def test_outside_booking_window_after_max_days_in_advance(tbilisi_salon, hour_service):
    # Today (local) is 2030-01-01; with 10 days in advance the last bookable day is 01-11.
    tbilisi_salon.max_advance_days = 10
    tbilisi_salon.save(update_fields=["max_advance_days"])
    jane = _specialist(tbilisi_salon, "Jane", [hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.SATURDAY, dt.time(9), dt.time(12))

    assert _reason(tbilisi_salon, hour_service, jane) == "outside_booking_window"


def test_any_day_off_when_no_qualified_specialist_works(tbilisi_salon, hour_service):
    jane = _specialist(tbilisi_salon, "Jane", [hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.MONDAY, dt.time(9), dt.time(18))
    unlinked = _specialist(tbilisi_salon, "Unlinked")
    _hours(tbilisi_salon, unlinked, DayOfWeek.SATURDAY, dt.time(9), dt.time(18))
    former = _specialist(tbilisi_salon, "Former", [hour_service], is_active=False)
    _hours(tbilisi_salon, former, DayOfWeek.SATURDAY, dt.time(9), dt.time(18))

    assert _reason(tbilisi_salon, hour_service, None) == "day_off"


def test_any_fully_booked_when_the_only_qualified_specialist_is_booked(
    tbilisi_salon, hour_service, customer
):
    jane = _specialist(tbilisi_salon, "Jane", [hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.SATURDAY, dt.time(9), dt.time(10))
    make_appointment(
        salon=tbilisi_salon,
        customer=customer,
        specialist=jane,
        service=hour_service,
        start=_at(SATURDAY, 9),
    )
    unlinked = _specialist(tbilisi_salon, "Unlinked")
    _hours(tbilisi_salon, unlinked, DayOfWeek.SATURDAY, dt.time(9), dt.time(18))

    assert _reason(tbilisi_salon, hour_service, None) == "fully_booked"


def test_another_specialists_hours_do_not_change_the_reason(tbilisi_salon, hour_service):
    anna = _specialist(tbilisi_salon, "Anna", [hour_service])
    bella = _specialist(tbilisi_salon, "Bella", [hour_service])
    assert bella.pk > anna.pk
    _hours(tbilisi_salon, anna, DayOfWeek.MONDAY, dt.time(9), dt.time(18))
    _hours(tbilisi_salon, bella, DayOfWeek.SATURDAY, dt.time(9), dt.time(12))

    assert _reason(tbilisi_salon, hour_service, anna) == "day_off"
    assert _reason(tbilisi_salon, hour_service, None) is None
