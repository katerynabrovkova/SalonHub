"""
Stage 17.8 — "any specialist" names who is free. check_availability with
specialist "any" also returns free_specialists: for every time it lists
(slots, or the alternatives' slots), the specialists free then. When one
specialist is free at every shown time, or with no times the service has
only one specialist, propose_booking makes a normal proposal for her: her
name and rating on the card and her id in it; the model's result carries
specialist_id. Otherwise the card stays "any".

The salon runs on Asia/Tbilisi (UTC+04:00 all year); `now` is a fixed
literal. Within one salon the higher-pk specialist is the one resolved.
"""

import datetime as dt
from typing import Any

import pytest

from ai_assistant.proposals import handle_propose_booking
from ai_assistant.tools import TOOLS, check_availability
from core.tenancy import tenant_context
from specialists.models import Specialist, SpecialistService
from tests.conftest import make_appointment, make_working_hours

pytestmark = pytest.mark.django_db

# A Monday; NOW is the day before.
MONDAY = dt.date(2026, 8, 17)
NOW = dt.datetime(2026, 8, 16, 0, 0, tzinfo=dt.UTC)
SLOT_0900 = "2026-08-17T09:00:00+04:00"
SLOT_1100 = "2026-08-17T11:00:00+04:00"
SLOT_1300 = "2026-08-17T13:00:00+04:00"


@pytest.fixture
def tbilisi_salon(salon):
    salon.timezone = "Asia/Tbilisi"
    salon.save(update_fields=["timezone"])
    return salon


def _specialist(salon, name: str, services=()) -> Specialist:
    with tenant_context(salon.id):
        specialist = Specialist.objects.create(salon=salon, name={"en": name})
        for service in services:
            SpecialistService.objects.create(salon=salon, specialist=specialist, service=service)
    return specialist


def _hours(salon, specialist, start: int, end: int, day: dt.date = MONDAY) -> None:
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=day.weekday(),
        start_time=dt.time(start),
        end_time=dt.time(end),
    )


def _book(salon, customer, specialist, service, local_iso: str) -> None:
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=dt.datetime.fromisoformat(local_iso).astimezone(dt.UTC),
    )


def _check(salon, **arguments: Any) -> dict[str, Any]:
    arguments.setdefault("date_from", MONDAY.isoformat())
    arguments.setdefault("date_to", MONDAY.isoformat())
    with tenant_context(salon.id):
        return check_availability(lang="en", now=NOW, **arguments)


def _propose(salon, **arguments: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    with tenant_context(salon.id):
        return handle_propose_booking(lang="en", now=NOW, arguments=arguments)


# --- check_availability ------------------------------------------------------


def test_any_lists_who_is_free_at_each_slot(tbilisi_salon, service, specialist, customer):
    mary = _specialist(tbilisi_salon, "Mary", [service])
    _hours(tbilisi_salon, specialist, 9, 12)
    _hours(tbilisi_salon, mary, 9, 18)
    _book(tbilisi_salon, customer, specialist, service, SLOT_0900)

    result = _check(tbilisi_salon, service_id=service.id, specialist_id="any")

    jane_entry = {"id": specialist.id, "name": "Jane"}
    mary_entry = {"id": mary.id, "name": "Mary"}
    assert set(result["free_specialists"]) == set(result["slots"])
    assert result["free_specialists"][SLOT_0900] == [mary_entry]
    later = [slot for slot in result["slots"] if slot != SLOT_0900]
    assert later
    for slot in later:
        free = result["free_specialists"][slot]
        assert mary_entry in free
        assert all(entry in (jane_entry, mary_entry) for entry in free)


def test_any_with_no_slots_lists_who_is_free_at_the_alternatives(
    tbilisi_salon, service, specialist
):
    _hours(tbilisi_salon, specialist, 9, 11)

    result = _check(
        tbilisi_salon,
        service_id=service.id,
        specialist_id="any",
        time_from="15:00",
        time_to="18:00",
    )

    assert result["slots"] == []
    alternatives = result["alternatives"]["slots"]
    assert alternatives
    assert result["free_specialists"] == {
        slot: [{"id": specialist.id, "name": "Jane"}] for slot in alternatives
    }


def test_a_specific_specialist_gets_no_free_specialists(tbilisi_salon, service, specialist):
    _hours(tbilisi_salon, specialist, 9, 12)

    result = _check(tbilisi_salon, service_id=service.id, specialist_id=specialist.id)

    assert "free_specialists" not in result


def test_spec_describes_free_specialists():
    spec, _function = TOOLS["check_availability"]

    assert "free_specialists" in spec.description


# --- propose_booking ---------------------------------------------------------


def test_one_specialist_free_at_every_shown_time_gets_a_normal_proposal(
    tbilisi_salon, service, specialist, customer
):
    mary = _specialist(tbilisi_salon, "Mary", [service])
    assert mary.pk > specialist.pk
    _hours(tbilisi_salon, specialist, 9, 18)
    _hours(tbilisi_salon, mary, 9, 18)
    # Jane is taken at 11:00, so only Mary is free at both shown times.
    _book(tbilisi_salon, customer, specialist, service, SLOT_1100)

    model_result, card = _propose(
        tbilisi_salon, service_id=service.id, specialist_id="any", slots=[SLOT_0900, SLOT_1100]
    )

    assert card is not None
    assert card["specialist"] == {
        "id": mary.id,
        "name": "Mary",
        "average_rating": None,
        "review_count": 0,
    }
    assert card["slots"] == [SLOT_0900, SLOT_1100]
    assert model_result == {
        "shown": True,
        "slots": [SLOT_0900, SLOT_1100],
        "dropped": [],
        "specialist_id": mary.id,
    }


def test_two_specialists_free_at_every_shown_time_stay_any(tbilisi_salon, service, specialist):
    mary = _specialist(tbilisi_salon, "Mary", [service])
    _hours(tbilisi_salon, specialist, 9, 18)
    _hours(tbilisi_salon, mary, 9, 18)

    model_result, card = _propose(
        tbilisi_salon, service_id=service.id, specialist_id="any", slots=[SLOT_0900, SLOT_1100]
    )

    assert card is not None
    assert card["specialist"] == "any"
    assert "specialist_id" not in model_result


def test_different_specialists_at_different_times_stay_any(
    tbilisi_salon, service, specialist, customer
):
    mary = _specialist(tbilisi_salon, "Mary", [service])
    _hours(tbilisi_salon, specialist, 9, 18)
    _hours(tbilisi_salon, mary, 9, 18)
    _book(tbilisi_salon, customer, specialist, service, SLOT_0900)
    _book(tbilisi_salon, customer, mary, service, SLOT_1300)

    model_result, card = _propose(
        tbilisi_salon, service_id=service.id, specialist_id="any", slots=[SLOT_0900, SLOT_1300]
    )

    assert card is not None
    assert card["specialist"] == "any"
    assert card["slots"] == [SLOT_0900, SLOT_1300]
    assert "specialist_id" not in model_result


def test_a_service_only_she_performs_names_her_without_times(tbilisi_salon, service, specialist):
    other = _specialist(tbilisi_salon, "Unlinked")
    _hours(tbilisi_salon, other, 9, 18)

    model_result, card = _propose(tbilisi_salon, service_id=service.id, specialist_id="any")

    assert card is not None
    assert card["specialist"]["id"] == specialist.id
    assert card["slots"] == []
    assert model_result["specialist_id"] == specialist.id


def test_two_specialists_without_times_stay_any(tbilisi_salon, service, specialist):
    _specialist(tbilisi_salon, "Mary", [service])

    _model_result, card = _propose(tbilisi_salon, service_id=service.id, specialist_id="any")

    assert card is not None
    assert card["specialist"] == "any"


def test_an_inactive_second_specialist_does_not_count(tbilisi_salon, service, specialist):
    mary = _specialist(tbilisi_salon, "Mary", [service])
    with tenant_context(tbilisi_salon.id):
        Specialist.objects.filter(pk=mary.pk).update(is_active=False)

    _model_result, card = _propose(tbilisi_salon, service_id=service.id, specialist_id="any")

    assert card is not None
    assert card["specialist"]["id"] == specialist.id
