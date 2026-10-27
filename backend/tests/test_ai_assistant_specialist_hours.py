"""
Stage 16 sub-step 10d — get_specialist_info returns each specialist's weekly
working hours (docs/DECISIONS.md § "Sub-step 10d details").

Shape: "working_hours": {"monday": [{"start": "09:00", "end": "18:00"}],
...}, keyed by lowercase English weekday in Monday-to-Sunday order, each
day's ranges sorted by start; days without hours are absent, and a
specialist without hours gets {}. Weekly schedule only: time off is not
reflected.

Isolation on two levels (CLAUDE.md): another salon's specialists and hours
never appear, and within one salon each specialist shows only their own
hours, tested on the higher pk.
"""

import datetime as dt

import pytest

from ai_assistant.tools import get_specialist_info
from core.tenancy import tenant_context
from specialists.models import DayOfWeek, Specialist
from tests.conftest import make_time_off, make_working_hours

pytestmark = pytest.mark.django_db


def _specialist(salon, name: str, services=()) -> Specialist:
    with tenant_context(salon.id):
        specialist = Specialist.objects.create(salon=salon, name={"en": name})
        if services:
            specialist.services.set(services, through_defaults={"salon_id": salon.id})
        return specialist


def _hours(salon, specialist, day: DayOfWeek, start: dt.time, end: dt.time) -> None:
    make_working_hours(
        salon=salon, specialist=specialist, day_of_week=day, start_time=start, end_time=end
    )


def _info(salon, **arguments) -> dict:
    with tenant_context(salon.id):
        return get_specialist_info(lang="en", **arguments)


def _hours_by_id(result: dict) -> dict:
    return {item["id"]: item["working_hours"] for item in result["specialists"]}


def test_working_hours_shape_in_weekday_order(salon, service):
    jane = _specialist(salon, "Jane", [service])
    # Created out of weekday order on purpose.
    _hours(salon, jane, DayOfWeek.WEDNESDAY, dt.time(10), dt.time(14, 30))
    _hours(salon, jane, DayOfWeek.MONDAY, dt.time(9), dt.time(18))

    working_hours = _hours_by_id(_info(salon))[jane.id]

    assert working_hours == {
        "monday": [{"start": "09:00", "end": "18:00"}],
        "wednesday": [{"start": "10:00", "end": "14:30"}],
    }
    assert list(working_hours) == ["monday", "wednesday"]


def test_several_ranges_on_one_day_are_sorted_by_start(salon, service):
    jane = _specialist(salon, "Jane", [service])
    _hours(salon, jane, DayOfWeek.MONDAY, dt.time(14), dt.time(18))
    _hours(salon, jane, DayOfWeek.MONDAY, dt.time(9), dt.time(12))

    working_hours = _hours_by_id(_info(salon))[jane.id]

    assert working_hours == {
        "monday": [{"start": "09:00", "end": "12:00"}, {"start": "14:00", "end": "18:00"}]
    }


def test_specialist_without_hours_has_empty_working_hours(salon, service):
    jane = _specialist(salon, "Jane", [service])

    assert _hours_by_id(_info(salon))[jane.id] == {}


def test_time_off_does_not_change_the_weekly_hours(salon, service):
    jane = _specialist(salon, "Jane", [service])
    _hours(salon, jane, DayOfWeek.MONDAY, dt.time(9), dt.time(18))
    start = dt.datetime(2030, 1, 7, 0, tzinfo=dt.UTC)
    make_time_off(
        salon=salon,
        specialist=jane,
        start_datetime=start,
        end_datetime=start + dt.timedelta(days=7),
    )

    assert _hours_by_id(_info(salon))[jane.id] == {"monday": [{"start": "09:00", "end": "18:00"}]}


def test_another_salons_specialists_and_hours_never_appear(salon, other_salon, service):
    jane = _specialist(salon, "Jane", [service])
    _hours(salon, jane, DayOfWeek.MONDAY, dt.time(9), dt.time(12))
    elsewhere = _specialist(other_salon, "Elsewhere")
    _hours(other_salon, elsewhere, DayOfWeek.MONDAY, dt.time(15), dt.time(20))
    _hours(other_salon, elsewhere, DayOfWeek.SATURDAY, dt.time(10), dt.time(16))

    result = _info(salon)

    assert _hours_by_id(result) == {jane.id: {"monday": [{"start": "09:00", "end": "12:00"}]}}


def test_each_specialist_shows_only_their_own_hours(salon, service):
    jane = _specialist(salon, "Jane", [service])
    mary = _specialist(salon, "Mary", [service])
    assert mary.pk > jane.pk
    _hours(salon, jane, DayOfWeek.MONDAY, dt.time(9), dt.time(12))
    _hours(salon, mary, DayOfWeek.SATURDAY, dt.time(10), dt.time(14))

    listed = _hours_by_id(_info(salon))
    by_id = _hours_by_id(_info(salon, specialist_id=mary.id))

    assert listed == {
        jane.id: {"monday": [{"start": "09:00", "end": "12:00"}]},
        mary.id: {"saturday": [{"start": "10:00", "end": "14:00"}]},
    }
    assert by_id == {mary.id: {"saturday": [{"start": "10:00", "end": "14:00"}]}}
