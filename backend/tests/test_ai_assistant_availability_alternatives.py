"""
Stage 16 sub-step 10c — check_availability explains an empty result
(docs/DECISIONS.md § "Sub-step 10c: `check_availability` explains empty
results").

With free slots the result keeps its shape, {"slots", "has_more"}. With no
slots it adds "reason" (from scheduling.services.no_free_start_reason) and
"alternatives": {"slots", "relaxed"}. The alternatives search starts at
date_from, stays within 14 days, and relaxes soft wishes in order: the
same time of day on other days (relaxed ["day"]), other times on the
requested days (["time"]), any time (["day", "time"]). The first step that
finds slots wins. Found slots and alternatives alike give at most 3 slots
per day, as far apart as possible, for the first 3 days with any.
`hard` ("day", "time") keeps a wish; service and specialist are always kept.

The salon runs on Asia/Tbilisi (UTC+4, no DST) with 60-minute slots and a
60-minute service without buffer. NOW is Tuesday 2030-01-01 00:00 UTC
(04:00 local), frozen. Saturday 2030-01-12 is the usual requested day, so
the search runs from 01-12 to 01-25.

Isolation on two levels (CLAUDE.md): another salon's specialists never
count, and within one salon another specialist's hours never change the
requested specialist's result, tested with the higher pk.
"""

import datetime as dt
import json
from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from ai_assistant.tools import TOOLS, check_availability
from catalog.models import Service, ServiceCategory
from core.tenancy import tenant_context
from specialists.models import DayOfWeek, Specialist
from tests.conftest import make_appointment, make_time_off, make_working_hours

pytestmark = pytest.mark.django_db

NOW = dt.datetime(2030, 1, 1, tzinfo=dt.UTC)
TBILISI = ZoneInfo("Asia/Tbilisi")
SATURDAY = "2030-01-12"
WEEKDAYS = (
    DayOfWeek.MONDAY,
    DayOfWeek.TUESDAY,
    DayOfWeek.WEDNESDAY,
    DayOfWeek.THURSDAY,
    DayOfWeek.FRIDAY,
)


@pytest.fixture(autouse=True)
def _frozen_now(monkeypatch):
    monkeypatch.setattr(timezone, "now", lambda: NOW)


@pytest.fixture
def tbilisi_salon(salon, other_salon):
    for each in (salon, other_salon):
        each.timezone = "Asia/Tbilisi"
        each.slot_granularity_minutes = 60
        each.save(update_fields=["timezone", "slot_granularity_minutes"])
    return salon


def _service(salon, category) -> Service:
    with tenant_context(salon.id):
        return Service.objects.create(
            salon=salon,
            category=category,
            name={"en": "Gel polish"},
            duration_minutes=60,
            price="300.00",
        )


@pytest.fixture
def hour_service(tbilisi_salon, service_category):
    return _service(tbilisi_salon, service_category)


def _specialist(salon, name: str, services=()) -> Specialist:
    with tenant_context(salon.id):
        specialist = Specialist.objects.create(salon=salon, name={"en": name})
        if services:
            specialist.services.set(services, through_defaults={"salon_id": salon.id})
        return specialist


def _hours(salon, specialist, days, start_hour: int, end_hour: int) -> None:
    for day in days if isinstance(days, tuple) else (days,):
        make_working_hours(
            salon=salon,
            specialist=specialist,
            day_of_week=day,
            start_time=dt.time(start_hour),
            end_time=dt.time(end_hour),
        )


def _at(day: int, hour: int) -> dt.datetime:
    return dt.datetime(2030, 1, day, hour, tzinfo=TBILISI)


def _local(day: int, hour: int) -> str:
    """Salon-local January 2030 start time, formatted like the slots endpoint."""
    return _at(day, hour).isoformat()


def _day_off(salon, specialist, day: int) -> None:
    make_time_off(
        salon=salon,
        specialist=specialist,
        start_datetime=_at(day, 0),
        end_datetime=_at(day, 0) + dt.timedelta(days=1),
    )


def _check(salon, **arguments) -> dict[str, Any]:
    arguments.setdefault("date_from", SATURDAY)
    arguments.setdefault("date_to", arguments["date_from"])
    with tenant_context(salon.id):
        return check_availability(lang="en", now=NOW, **arguments)


EVENING = {"time_from": "18:00", "time_to": "21:00"}


# --- Shape ---------------------------------------------------------------------


def test_found_slots_keep_the_original_shape(tbilisi_salon, hour_service):
    jane = _specialist(tbilisi_salon, "Jane", [hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.SATURDAY, 9, 11)

    result = _check(tbilisi_salon, service_id=hour_service.id, specialist_id=jane.id)

    assert result == {"slots": [_local(12, 9), _local(12, 10)], "has_more": False}


def test_empty_result_carries_reason_and_alternatives(tbilisi_salon, hour_service):
    jane = _specialist(tbilisi_salon, "Jane", [hour_service])
    _hours(tbilisi_salon, jane, WEEKDAYS, 9, 21)

    result = _check(tbilisi_salon, service_id=hour_service.id, specialist_id=jane.id, **EVENING)

    assert result == {
        "slots": [],
        "has_more": False,
        "reason": "day_off",
        "alternatives": {
            "slots": [_local(day, hour) for day in (14, 15, 16) for hour in (18, 19, 20)],
            "relaxed": ["day"],
        },
    }
    json.dumps(result)


# --- Reasons -------------------------------------------------------------------


def _no_hours(salon, service, customer):
    jane = _specialist(salon, "Jane", [service])
    _hours(salon, jane, DayOfWeek.MONDAY, 9, 12)
    return {"specialist_id": jane.id}


def _time_off(salon, service, customer):
    jane = _specialist(salon, "Jane", [service])
    _hours(salon, jane, DayOfWeek.SATURDAY, 9, 12)
    _day_off(salon, jane, 12)
    return {"specialist_id": jane.id}


def _booked(salon, service, customer):
    jane = _specialist(salon, "Jane", [service])
    _hours(salon, jane, DayOfWeek.SATURDAY, 9, 11)
    for hour in (9, 10):
        make_appointment(
            salon=salon, customer=customer, specialist=jane, service=service, start=_at(12, hour)
        )
    return {"specialist_id": jane.id}


def _lead_time(salon, service, customer):
    # NOW is 04:00 local on 01-01; the 3-hour lead time ends at 07:00.
    jane = _specialist(salon, "Jane", [service])
    _hours(salon, jane, DayOfWeek.TUESDAY, 0, 23)
    return {
        "specialist_id": jane.id,
        "date_from": "2030-01-01",
        "time_from": "04:00",
        "time_to": "07:00",
    }


def _max_advance(salon, service, customer):
    # Today (local) is 01-01; with 10 days in advance the last bookable day is 01-11.
    salon.max_advance_days = 10
    salon.save(update_fields=["max_advance_days"])
    jane = _specialist(salon, "Jane", [service])
    _hours(salon, jane, DayOfWeek.SATURDAY, 9, 12)
    return {"specialist_id": jane.id}


def _any_day_off(salon, service, customer):
    jane = _specialist(salon, "Jane", [service])
    _hours(salon, jane, DayOfWeek.MONDAY, 9, 12)
    unlinked = _specialist(salon, "Unlinked")
    _hours(salon, unlinked, DayOfWeek.SATURDAY, 9, 12)
    return {"specialist_id": "any"}


@pytest.mark.parametrize(
    ("build", "reason"),
    [
        (_no_hours, "day_off"),
        (_time_off, "day_off"),
        (_booked, "fully_booked"),
        (_lead_time, "outside_booking_window"),
        (_max_advance, "outside_booking_window"),
        (_any_day_off, "day_off"),
    ],
    ids=["no_hours_that_weekday", "time_off", "fully_booked", "lead_time", "max_advance", "any"],
)
def test_empty_result_reason(
    tbilisi_salon,
    hour_service,
    customer,
    build: Callable[..., dict[str, Any]],
    reason: str,
):
    arguments = build(tbilisi_salon, hour_service, customer)

    result = _check(tbilisi_salon, service_id=hour_service.id, **arguments)

    assert result["slots"] == []
    assert result["reason"] == reason


# --- Relaxation steps ----------------------------------------------------------


def test_step_1_same_time_of_day_on_other_days_comes_first(tbilisi_salon, hour_service):
    jane = _specialist(tbilisi_salon, "Jane", [hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.SATURDAY, 9, 12)
    _hours(tbilisi_salon, jane, DayOfWeek.MONDAY, 18, 21)

    result = _check(tbilisi_salon, service_id=hour_service.id, specialist_id=jane.id, **EVENING)

    # Saturday morning (step 2) would also match; step 1 wins.
    assert result["alternatives"] == {
        "slots": [_local(day, hour) for day in (14, 21) for hour in (18, 19, 20)],
        "relaxed": ["day"],
    }


def test_step_2_other_times_on_the_requested_days(tbilisi_salon, hour_service):
    jane = _specialist(tbilisi_salon, "Jane", [hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.SATURDAY, 9, 12)

    result = _check(tbilisi_salon, service_id=hour_service.id, specialist_id=jane.id, **EVENING)

    # No evening on any day (step 1); Saturday 01-12 has a morning.
    assert result["alternatives"] == {
        "slots": [_local(12, 9), _local(12, 10), _local(12, 11)],
        "relaxed": ["time"],
    }


def test_step_3_any_time_on_other_days(tbilisi_salon, hour_service):
    jane = _specialist(tbilisi_salon, "Jane", [hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.MONDAY, 9, 10)

    result = _check(tbilisi_salon, service_id=hour_service.id, specialist_id=jane.id, **EVENING)

    assert result["alternatives"] == {
        "slots": [_local(14, 9), _local(21, 9)],
        "relaxed": ["day", "time"],
    }


# --- Hard wishes ---------------------------------------------------------------


def test_hard_day_skips_the_steps_that_move_the_day(tbilisi_salon, hour_service):
    jane = _specialist(tbilisi_salon, "Jane", [hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.SATURDAY, 9, 12)
    _hours(tbilisi_salon, jane, DayOfWeek.MONDAY, 18, 21)

    result = _check(
        tbilisi_salon,
        service_id=hour_service.id,
        specialist_id=jane.id,
        hard=["day"],
        **EVENING,
    )

    assert result["alternatives"] == {
        "slots": [_local(12, 9), _local(12, 10), _local(12, 11)],
        "relaxed": ["time"],
    }


def test_hard_time_skips_the_steps_that_move_the_time(tbilisi_salon, hour_service):
    jane = _specialist(tbilisi_salon, "Jane", [hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.SATURDAY, 9, 12)

    result = _check(
        tbilisi_salon,
        service_id=hour_service.id,
        specialist_id=jane.id,
        hard=["time"],
        **EVENING,
    )

    # Without hard, step 2 finds Saturday morning
    # (test_step_2_other_times_on_the_requested_days).
    assert result["alternatives"] == {"slots": [], "relaxed": []}


def test_hard_day_and_time_give_no_alternatives(tbilisi_salon, hour_service):
    jane = _specialist(tbilisi_salon, "Jane", [hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.SATURDAY, 9, 12)
    _hours(tbilisi_salon, jane, DayOfWeek.MONDAY, 18, 21)

    result = _check(
        tbilisi_salon,
        service_id=hour_service.id,
        specialist_id=jane.id,
        hard=["day", "time"],
        **EVENING,
    )

    assert result == {
        "slots": [],
        "has_more": False,
        # Jane works Saturday, just not in the evening (sub-step 10e).
        "reason": "outside_hours",
        "hours": {
            "date": SATURDAY,
            "ranges": [{"start": "09:00", "end": "12:00"}],
            "last_start": "11:00",
        },
        "alternatives": {"slots": [], "relaxed": []},
    }


@pytest.mark.parametrize("hard", [["weekday"], "day"], ids=["unknown_value", "not_a_list"])
def test_bad_hard_is_invalid_filter(tbilisi_salon, hour_service, hard):
    jane = _specialist(tbilisi_salon, "Jane", [hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.SATURDAY, 9, 12)

    result = _check(tbilisi_salon, service_id=hour_service.id, specialist_id=jane.id, hard=hard)

    assert result == {"error": "invalid_filter"}


def test_tool_spec_declares_hard():
    spec, _function = TOOLS["check_availability"]
    hard = spec.parameters["properties"]["hard"]

    assert hard["type"] == "array"
    assert hard["items"] == {"type": "string", "enum": ["day", "time"]}
    assert "hard" not in spec.parameters["required"]


# --- 14-day cap and spread -----------------------------------------------------


def test_alternatives_stay_within_14_days_of_date_from(tbilisi_salon, hour_service):
    jane = _specialist(tbilisi_salon, "Jane", [hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.MONDAY, 9, 10)
    _day_off(tbilisi_salon, jane, 7)
    _day_off(tbilisi_salon, jane, 14)

    # From Monday 01-07 the search ends on 01-20; Monday 01-21 is day 15.
    from_monday = _check(
        tbilisi_salon, service_id=hour_service.id, specialist_id=jane.id, date_from="2030-01-07"
    )
    # From Tuesday 01-08 the search ends on 01-21, its 14th day.
    from_tuesday = _check(
        tbilisi_salon, service_id=hour_service.id, specialist_id=jane.id, date_from="2030-01-08"
    )

    assert from_monday["alternatives"] == {"slots": [], "relaxed": []}
    assert from_tuesday["alternatives"] == {"slots": [_local(21, 9)], "relaxed": ["day"]}


def test_alternatives_spread_at_most_3_per_day_over_the_first_3_days(tbilisi_salon, hour_service):
    jane = _specialist(tbilisi_salon, "Jane", [hour_service])
    _hours(tbilisi_salon, jane, WEEKDAYS, 9, 21)

    result = _check(tbilisi_salon, service_id=hour_service.id, specialist_id=jane.id)

    # Free starts 09:00-20:00 each day: the first, the last, and the one
    # nearest the middle (14:00 and 15:00 tie; the earlier wins).
    assert result["alternatives"] == {
        "slots": [_local(day, hour) for day in (14, 15, 16) for hour in (9, 14, 20)],
        "relaxed": ["day"],
    }


# --- Spread within a day (decided 27.10.2026) ------------------------------------
#
# 15-minute slots, a 60-minute service, Monday 2030-01-07. Up to 3 slots a
# day, as far apart as possible: the smallest gap between chosen slots is
# maximised. Acceptance examples decided 27.10.2026; they assert the
# smallest gap and coverage, and exact times only where they are forced.


@pytest.fixture
def quarter_salon(tbilisi_salon):
    tbilisi_salon.slot_granularity_minutes = 15
    tbilisi_salon.save(update_fields=["slot_granularity_minutes"])
    return tbilisi_salon


def _quarter(day: int, hour: int, minute: int = 0) -> str:
    return dt.datetime(2030, 1, day, hour, minute, tzinfo=TBILISI).isoformat()


def _times(slots: list[str]) -> list[dt.time]:
    return [dt.datetime.fromisoformat(slot).astimezone(TBILISI).time() for slot in slots]


def _smallest_gap(slots: list[str]) -> dt.timedelta:
    starts = [dt.datetime.fromisoformat(slot) for slot in slots]
    return min(later - earlier for earlier, later in zip(starts, starts[1:], strict=False))


MONDAY = "2030-01-07"


def _monday(salon, **arguments) -> dict[str, Any]:
    return _check(salon, date_from=MONDAY, **arguments)


def test_spread_free_day_without_a_time_wish(quarter_salon, hour_service):
    jane = _specialist(quarter_salon, "Jane", [hour_service])
    _hours(quarter_salon, jane, DayOfWeek.MONDAY, 9, 18)

    result = _monday(quarter_salon, service_id=hour_service.id, specialist_id=jane.id)

    times = _times(result["slots"])
    assert len(times) == 3
    assert result["slots"] == sorted(result["slots"])
    assert _smallest_gap(result["slots"]) >= dt.timedelta(hours=3)
    assert times[0] < dt.time(12)
    assert times[-1] >= dt.time(16)
    assert result["has_more"] is True


def _jane_morning_mary_afternoon(salon, service):
    jane = _specialist(salon, "Jane", [service])
    mary = _specialist(salon, "Mary", [service])
    _hours(salon, jane, DayOfWeek.MONDAY, 9, 12)
    _hours(salon, mary, DayOfWeek.MONDAY, 15, 18)
    return jane, mary


def _covers_both(slots: list[str]) -> bool:
    times = _times(slots)
    jane_hours = any(dt.time(9) <= time <= dt.time(11) for time in times)
    mary_hours = any(dt.time(15) <= time <= dt.time(17) for time in times)
    return jane_hours and mary_hours


def test_spread_any_covers_both_specialists(quarter_salon, hour_service):
    _jane_morning_mary_afternoon(quarter_salon, hour_service)

    result = _monday(quarter_salon, service_id=hour_service.id, specialist_id="any")

    assert len(result["slots"]) == 3
    assert _covers_both(result["slots"])
    assert _smallest_gap(result["slots"]) >= dt.timedelta(hours=1)


def test_spread_any_covers_both_specialists_with_11_booked(quarter_salon, hour_service, customer):
    jane, _mary = _jane_morning_mary_afternoon(quarter_salon, hour_service)
    make_appointment(
        salon=quarter_salon,
        customer=customer,
        specialist=jane,
        service=hour_service,
        start=_at(7, 11),
    )

    result = _monday(quarter_salon, service_id=hour_service.id, specialist_id="any")

    assert len(result["slots"]) == 3
    assert _quarter(7, 11) not in result["slots"]
    assert _covers_both(result["slots"])
    assert _smallest_gap(result["slots"]) >= dt.timedelta(hours=1)


def test_spread_narrow_window_keeps_all_slots_inside_it(quarter_salon, hour_service):
    jane = _specialist(quarter_salon, "Jane", [hour_service])
    _hours(quarter_salon, jane, DayOfWeek.MONDAY, 9, 18)

    result = _monday(
        quarter_salon,
        service_id=hour_service.id,
        specialist_id=jane.id,
        time_from="09:00",
        time_to="10:00",
    )

    # 09:15 and 09:30 are equally near the middle (09:22:30); the half hour wins.
    assert result["slots"] == [_quarter(7, 9), _quarter(7, 9, 30), _quarter(7, 9, 45)]


def test_spread_only_three_free_slots_are_all_chosen(quarter_salon, hour_service):
    jane = _specialist(quarter_salon, "Jane", [hour_service])
    # 09:00-10:30 with a 60-minute service: only 09:00, 09:15 and 09:30 fit.
    make_working_hours(
        salon=quarter_salon,
        specialist=jane,
        day_of_week=DayOfWeek.MONDAY,
        start_time=dt.time(9),
        end_time=dt.time(10, 30),
    )

    result = _monday(quarter_salon, service_id=hour_service.id, specialist_id=jane.id)

    assert result == {
        "slots": [_quarter(7, 9), _quarter(7, 9, 15), _quarter(7, 9, 30)],
        "has_more": False,
    }


def test_spread_morning_with_10_to_11_booked(quarter_salon, hour_service, customer):
    jane = _specialist(quarter_salon, "Jane", [hour_service])
    _hours(quarter_salon, jane, DayOfWeek.MONDAY, 9, 18)
    make_appointment(
        salon=quarter_salon,
        customer=customer,
        specialist=jane,
        service=hour_service,
        start=_at(7, 10),
    )

    result = _monday(
        quarter_salon,
        service_id=hour_service.id,
        specialist_id=jane.id,
        time_from="09:00",
        time_to="12:00",
    )

    # Free starts: 09:00, then 11:00, 11:15, 11:30, 11:45 (09:15-10:45
    # overlap the booking). Only one start precedes it, so two of the three
    # fall in 11:00-11:45, at most 45 minutes apart: the only choice with a
    # 45-minute smallest gap is 09:00, 11:00, 11:45.
    assert result["slots"] == [_quarter(7, 9), _quarter(7, 11), _quarter(7, 11, 45)]
    assert _smallest_gap(result["slots"]) == dt.timedelta(minutes=45)


# --- Isolation -----------------------------------------------------------------


def test_any_ignores_another_salons_specialists(tbilisi_salon, other_salon, hour_service):
    jane = _specialist(tbilisi_salon, "Jane", [hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.MONDAY, 9, 10)
    with tenant_context(other_salon.id):
        other_category = ServiceCategory.objects.create(salon=other_salon, name={"en": "Nails"})
    other_service = _service(other_salon, other_category)
    elsewhere = _specialist(other_salon, "Elsewhere", [other_service])
    _hours(other_salon, elsewhere, DayOfWeek.SATURDAY, 9, 21)

    result = _check(tbilisi_salon, service_id=hour_service.id, specialist_id="any")

    assert result == {
        "slots": [],
        "has_more": False,
        "reason": "day_off",
        "alternatives": {"slots": [_local(14, 9), _local(21, 9)], "relaxed": ["day"]},
    }


def test_another_specialist_does_not_change_the_requested_specialists_result(
    tbilisi_salon, hour_service
):
    anna = _specialist(tbilisi_salon, "Anna", [hour_service])
    bella = _specialist(tbilisi_salon, "Bella", [hour_service])
    assert bella.pk > anna.pk
    _hours(tbilisi_salon, anna, DayOfWeek.MONDAY, 9, 10)
    _hours(tbilisi_salon, bella, DayOfWeek.SATURDAY, 9, 12)

    result = _check(tbilisi_salon, service_id=hour_service.id, specialist_id=anna.id)

    assert result == {
        "slots": [],
        "has_more": False,
        "reason": "day_off",
        "alternatives": {"slots": [_local(14, 9), _local(21, 9)], "relaxed": ["day"]},
    }
