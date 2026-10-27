"""
Stage 16 sub-step 10e — check_availability's `day_off` / `outside_hours`
reasons and get_specialist_info's "complete list" description
(docs/DECISIONS.md § "Sub-step 10e details").

`outside_hours` carries "hours": {"date", "ranges", "last_start"} for the
first requested day the specialist works; other reasons carry no "hours".

Asia/Tbilisi, 15-minute slots, a 90-minute manicure with a 15-minute
buffer. NOW is Tuesday 2030-01-01 00:00 UTC, frozen. Friday is 2030-01-11,
Saturday 2030-01-12.
"""

import datetime as dt
from typing import Any

import pytest
from django.utils import timezone

from ai_assistant.tools import TOOLS, check_availability
from catalog.models import Service
from core.tenancy import tenant_context
from specialists.models import DayOfWeek, Specialist
from tests.conftest import make_working_hours

pytestmark = pytest.mark.django_db

NOW = dt.datetime(2030, 1, 1, tzinfo=dt.UTC)
FRIDAY = "2030-01-11"
SATURDAY = "2030-01-12"


@pytest.fixture(autouse=True)
def _frozen_now(monkeypatch):
    monkeypatch.setattr(timezone, "now", lambda: NOW)


@pytest.fixture
def tbilisi_salon(salon):
    salon.timezone = "Asia/Tbilisi"
    salon.slot_granularity_minutes = 15
    salon.save(update_fields=["timezone", "slot_granularity_minutes"])
    return salon


@pytest.fixture
def manicure(tbilisi_salon, service_category):
    with tenant_context(tbilisi_salon.id):
        return Service.objects.create(
            salon=tbilisi_salon,
            category=service_category,
            name={"en": "Manicure"},
            duration_minutes=90,
            buffer_minutes=15,
            price="500.00",
        )


@pytest.fixture
def anna(tbilisi_salon, manicure):
    with tenant_context(tbilisi_salon.id):
        anna = Specialist.objects.create(salon=tbilisi_salon, name={"en": "Anna"})
        anna.services.set([manicure], through_defaults={"salon_id": tbilisi_salon.id})
    return anna


def _hours(salon, specialist, day: DayOfWeek, start: dt.time, end: dt.time) -> None:
    make_working_hours(
        salon=salon, specialist=specialist, day_of_week=day, start_time=start, end_time=end
    )


def _check(salon, **arguments) -> dict[str, Any]:
    arguments.setdefault("date_to", arguments["date_from"])
    with tenant_context(salon.id):
        return check_availability(lang="en", now=NOW, **arguments)


def test_outside_hours_carries_the_days_hours_and_last_start(tbilisi_salon, manicure, anna):
    _hours(tbilisi_salon, anna, DayOfWeek.SATURDAY, dt.time(9), dt.time(18))

    result = _check(
        tbilisi_salon,
        service_id=manicure.id,
        specialist_id=anna.id,
        date_from=SATURDAY,
        time_from="17:00",
        time_to="19:00",
    )

    assert result["slots"] == []
    assert result["reason"] == "outside_hours"
    assert result["hours"] == {
        "date": SATURDAY,
        "ranges": [{"start": "09:00", "end": "18:00"}],
        "last_start": "16:15",
    }
    # 17:00-19:00 fits on no day, so only other times on Saturday remain.
    assert result["alternatives"]["relaxed"] == ["time"]


def test_outside_hours_uses_the_first_requested_day_the_specialist_works(
    tbilisi_salon, manicure, anna
):
    _hours(tbilisi_salon, anna, DayOfWeek.FRIDAY, dt.time(9), dt.time(12))
    _hours(tbilisi_salon, anna, DayOfWeek.SATURDAY, dt.time(9), dt.time(18))

    result = _check(
        tbilisi_salon,
        service_id=manicure.id,
        specialist_id=anna.id,
        date_from=FRIDAY,
        date_to=SATURDAY,
        time_from="18:00",
        time_to="20:00",
    )

    assert result["reason"] == "outside_hours"
    assert result["hours"] == {
        "date": FRIDAY,
        "ranges": [{"start": "09:00", "end": "12:00"}],
        "last_start": "10:15",
    }


def test_day_off_carries_no_hours(tbilisi_salon, manicure, anna):
    _hours(tbilisi_salon, anna, DayOfWeek.MONDAY, dt.time(9), dt.time(18))

    result = _check(
        tbilisi_salon, service_id=manicure.id, specialist_id=anna.id, date_from=SATURDAY
    )

    assert result["reason"] == "day_off"
    assert "hours" not in result


def test_get_specialist_info_description_says_the_list_is_complete():
    spec, _function = TOOLS["get_specialist_info"]

    assert "all of the salon's active specialists" in spec.description
    assert "complete" in spec.description
