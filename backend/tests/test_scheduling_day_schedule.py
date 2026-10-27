"""
Stage 16 sub-step 10e — scheduling.services.day_schedule (docs/DECISIONS.md
§ "Sub-step 10e details").

One salon-local day for `service` with `specialist`, or with any qualified
specialist when `specialist` is None: the working ranges (WorkingHours of
that weekday minus time off, merged; with "any", merged across the
qualified specialists) and the last start on the slot grid whose duration
plus buffer still ends within a range (None when the service fits nowhere).
None when nobody works that day. Appointments and the booking window are
ignored: this is the day's schedule, not its availability.

Asia/Tbilisi, 15-minute slots. Saturday is 2030-01-12. The helper is
imported inside each test, so every test fails on its own before it exists.
"""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from catalog.models import Service
from core.tenancy import tenant_context
from specialists.models import DayOfWeek, Specialist
from tests.conftest import make_time_off, make_working_hours

pytestmark = pytest.mark.django_db

TBILISI = ZoneInfo("Asia/Tbilisi")
SATURDAY = dt.date(2030, 1, 12)


@pytest.fixture
def tbilisi_salon(salon):
    salon.timezone = "Asia/Tbilisi"
    salon.slot_granularity_minutes = 15
    salon.save(update_fields=["timezone", "slot_granularity_minutes"])
    return salon


@pytest.fixture
def manicure(tbilisi_salon, service_category):
    """90 minutes plus a 15-minute buffer: 105 minutes in all."""
    with tenant_context(tbilisi_salon.id):
        return Service.objects.create(
            salon=tbilisi_salon,
            category=service_category,
            name={"en": "Manicure"},
            duration_minutes=90,
            buffer_minutes=15,
            price="500.00",
        )


def _specialist(salon, name: str, services=()) -> Specialist:
    with tenant_context(salon.id):
        specialist = Specialist.objects.create(salon=salon, name={"en": name})
        if services:
            specialist.services.set(services, through_defaults={"salon_id": salon.id})
        return specialist


def _hours(salon, specialist, start: dt.time, end: dt.time, day=DayOfWeek.SATURDAY) -> None:
    make_working_hours(
        salon=salon, specialist=specialist, day_of_week=day, start_time=start, end_time=end
    )


def _schedule(salon, service, specialist, date: dt.date = SATURDAY):
    from scheduling.services import day_schedule

    with tenant_context(salon.id):
        return day_schedule(salon=salon, service=service, specialist=specialist, date=date)


def test_last_start_leaves_room_for_duration_and_buffer(tbilisi_salon, manicure):
    anna = _specialist(tbilisi_salon, "Anna", [manicure])
    _hours(tbilisi_salon, anna, dt.time(9), dt.time(18))

    schedule = _schedule(tbilisi_salon, manicure, anna)

    # 18:00 minus 105 minutes: "Анна працює до 18:00, тож останній запис о 16:15".
    assert schedule.ranges == [(dt.time(9), dt.time(18))]
    assert schedule.last_start == dt.time(16, 15)


def test_two_ranges_and_the_last_start_in_the_later_one(tbilisi_salon, manicure):
    anna = _specialist(tbilisi_salon, "Anna", [manicure])
    _hours(tbilisi_salon, anna, dt.time(14), dt.time(18))
    _hours(tbilisi_salon, anna, dt.time(9), dt.time(12))

    schedule = _schedule(tbilisi_salon, manicure, anna)

    assert schedule.ranges == [(dt.time(9), dt.time(12)), (dt.time(14), dt.time(18))]
    assert schedule.last_start == dt.time(16, 15)


def test_time_off_shortens_the_day(tbilisi_salon, manicure):
    anna = _specialist(tbilisi_salon, "Anna", [manicure])
    _hours(tbilisi_salon, anna, dt.time(9), dt.time(18))
    make_time_off(
        salon=tbilisi_salon,
        specialist=anna,
        start_datetime=dt.datetime.combine(SATURDAY, dt.time(16), tzinfo=TBILISI),
        end_datetime=dt.datetime.combine(SATURDAY, dt.time(18), tzinfo=TBILISI),
    )

    schedule = _schedule(tbilisi_salon, manicure, anna)

    assert schedule.ranges == [(dt.time(9), dt.time(16))]
    assert schedule.last_start == dt.time(14, 15)


def test_hours_too_short_for_the_service_have_no_last_start(tbilisi_salon, manicure):
    anna = _specialist(tbilisi_salon, "Anna", [manicure])
    _hours(tbilisi_salon, anna, dt.time(9), dt.time(10))

    schedule = _schedule(tbilisi_salon, manicure, anna)

    assert schedule.ranges == [(dt.time(9), dt.time(10))]
    assert schedule.last_start is None


def test_none_on_a_day_off_including_a_day_covered_by_time_off(tbilisi_salon, manicure):
    anna = _specialist(tbilisi_salon, "Anna", [manicure])
    _hours(tbilisi_salon, anna, dt.time(9), dt.time(18), day=DayOfWeek.MONDAY)
    _hours(tbilisi_salon, anna, dt.time(9), dt.time(18))
    make_time_off(
        salon=tbilisi_salon,
        specialist=anna,
        start_datetime=dt.datetime.combine(SATURDAY, dt.time(0), tzinfo=TBILISI),
        end_datetime=dt.datetime.combine(
            SATURDAY + dt.timedelta(days=1), dt.time(0), tzinfo=TBILISI
        ),
    )
    sunday = SATURDAY + dt.timedelta(days=1)

    assert _schedule(tbilisi_salon, manicure, anna) is None
    assert _schedule(tbilisi_salon, manicure, anna, sunday) is None


def test_any_merges_the_qualified_specialists_and_ignores_others(tbilisi_salon, manicure):
    jane = _specialist(tbilisi_salon, "Jane", [manicure])
    mary = _specialist(tbilisi_salon, "Mary", [manicure])
    unlinked = _specialist(tbilisi_salon, "Unlinked")
    _hours(tbilisi_salon, jane, dt.time(9), dt.time(12))
    _hours(tbilisi_salon, mary, dt.time(15), dt.time(19))
    _hours(tbilisi_salon, unlinked, dt.time(19), dt.time(22))
    assert mary.pk > jane.pk

    schedule = _schedule(tbilisi_salon, manicure, None)

    assert schedule.ranges == [(dt.time(9), dt.time(12)), (dt.time(15), dt.time(19))]
    assert schedule.last_start == dt.time(17, 15)
