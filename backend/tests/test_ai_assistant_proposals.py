"""
Stage 16 sub-step 6b — `ai_assistant.proposals` (docs/DECISIONS.md §
"Stage 16 planning", "Sub-step 6 details", 6b): the `propose_booking` tool
the orchestrator intercepts. It validates the proposal through
tenant-scoped lookups and the shared slot check, drops slots that are not
(or no longer) offered without an error, and computes the card data.

The salon runs on Asia/Tbilisi (UTC+04:00 all year). `now` is a fixed
tz-aware literal passed in, never `timezone.now()`. Isolation on two levels
(CLAUDE.md): another salon's ids are "not found", and within one salon the
higher-pk specialist is the one shown when requested.
"""

import datetime as dt
import json
from typing import Any

import pytest

from ai_assistant.proposals import PROPOSE_BOOKING_SPEC, handle_propose_booking
from catalog.models import Service, ServiceCategory
from core.tenancy import TenantContextMissingError, tenant_context
from specialists.models import Specialist, SpecialistService
from tests.conftest import make_appointment, make_working_hours

pytestmark = pytest.mark.django_db

# A Monday; 09:00-18:00 Tbilisi working hours == 05:00-14:00 UTC.
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


def _monday_hours(salon, specialist: Specialist) -> None:
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=MONDAY.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )


def _specialist(salon, *, name: dict[str, str], services=(), is_active: bool = True):
    with tenant_context(salon.id):
        specialist = Specialist.objects.create(salon=salon, name=name, is_active=is_active)
        for service in services:
            SpecialistService.objects.create(salon=salon, specialist=specialist, service=service)
    return specialist


def _utc(local_iso: str) -> dt.datetime:
    return dt.datetime.fromisoformat(local_iso).astimezone(dt.UTC)


def _propose(
    salon, *, lang: str = "en", **arguments: Any
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    with tenant_context(salon.id):
        return handle_propose_booking(lang=lang, now=NOW, arguments=arguments)


def test_valid_proposal_for_a_specific_specialist(tbilisi_salon, service_category):
    with tenant_context(tbilisi_salon.id):
        service = Service.objects.create(
            salon=tbilisi_salon,
            category=service_category,
            name={"en": "Brow shaping"},
            duration_minutes=45,
            price="42.50",
        )
    jane = _specialist(tbilisi_salon, name={"en": "Jane"}, services=[service])
    _monday_hours(tbilisi_salon, jane)

    model_result, card = _propose(
        tbilisi_salon, service_id=service.id, specialist_id=jane.id, slots=[SLOT_0900, SLOT_1100]
    )

    assert model_result == {"shown": True, "slots": [SLOT_0900, SLOT_1100], "dropped": []}
    assert card == {
        "service": {"id": service.id, "name": "Brow shaping", "duration_minutes": 45},
        "specialist": {
            "id": jane.id,
            "name": "Jane",
            "average_rating": None,
            "review_count": 0,
        },
        "price": "42.50",
        "deposit": "9.00",
        "due_at_visit": "33.50",
        "currency": tbilisi_salon.currency,
        "slots": [SLOT_0900, SLOT_1100],
    }


def test_any_specialist_slots_are_checked_against_every_linked_specialist(
    tbilisi_salon, service, specialist, customer
):
    mary = _specialist(tbilisi_salon, name={"en": "Mary"}, services=[service])
    _monday_hours(tbilisi_salon, specialist)
    _monday_hours(tbilisi_salon, mary)
    # 09:00: only Jane is taken, Mary is free. 13:00: both are taken.
    make_appointment(
        salon=tbilisi_salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=_utc(SLOT_0900),
    )
    for busy in (specialist, mary):
        make_appointment(
            salon=tbilisi_salon,
            customer=customer,
            specialist=busy,
            service=service,
            start=_utc(SLOT_1300),
        )

    model_result, card = _propose(
        tbilisi_salon, service_id=service.id, specialist_id="any", slots=[SLOT_0900, SLOT_1300]
    )

    assert card is not None
    assert card["specialist"] == "any"
    assert card["slots"] == [SLOT_0900]
    assert model_result == {"shown": True, "slots": [SLOT_0900], "dropped": [SLOT_1300]}


def test_taken_slot_is_dropped_and_the_others_kept(tbilisi_salon, service, specialist, customer):
    _monday_hours(tbilisi_salon, specialist)
    make_appointment(
        salon=tbilisi_salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=_utc(SLOT_1100),
    )

    model_result, card = _propose(
        tbilisi_salon,
        service_id=service.id,
        specialist_id=specialist.id,
        slots=[SLOT_0900, SLOT_1100, SLOT_1300],
    )

    assert card is not None
    assert card["slots"] == [SLOT_0900, SLOT_1300]
    assert model_result == {
        "shown": True,
        "slots": [SLOT_0900, SLOT_1300],
        "dropped": [SLOT_1100],
    }


def test_all_slots_dropped_still_shows_the_card(tbilisi_salon, service, specialist, customer):
    _monday_hours(tbilisi_salon, specialist)
    for slot in (SLOT_0900, SLOT_1100):
        make_appointment(
            salon=tbilisi_salon,
            customer=customer,
            specialist=specialist,
            service=service,
            start=_utc(slot),
        )

    model_result, card = _propose(
        tbilisi_salon,
        service_id=service.id,
        specialist_id=specialist.id,
        slots=[SLOT_0900, SLOT_1100],
    )

    assert card is not None
    assert card["slots"] == []
    assert model_result == {"shown": True, "slots": [], "dropped": [SLOT_0900, SLOT_1100]}


def test_unparseable_naive_and_off_grid_slots_are_dropped(tbilisi_salon, service, specialist):
    _monday_hours(tbilisi_salon, specialist)
    bad_slots = ["tomorrow at nine", "2026-08-17T11:00:00", "2026-08-17T11:05:00+04:00"]

    model_result, card = _propose(
        tbilisi_salon, service_id=service.id, specialist_id=specialist.id, slots=bad_slots
    )

    assert card is not None
    assert card["slots"] == []
    assert model_result == {"shown": True, "slots": [], "dropped": bad_slots}


def test_no_slots_given_shows_the_card_without_slots(tbilisi_salon, service, specialist):
    model_result, card = _propose(tbilisi_salon, service_id=service.id, specialist_id=specialist.id)

    assert card is not None
    assert card["slots"] == []
    assert model_result == {"shown": True, "slots": [], "dropped": []}


def test_more_than_three_slots_is_an_invalid_tool_call(tbilisi_salon, service, specialist):
    _monday_hours(tbilisi_salon, specialist)
    four = [SLOT_0900, SLOT_1100, SLOT_1300, "2026-08-17T15:00:00+04:00"]

    model_result, card = _propose(
        tbilisi_salon, service_id=service.id, specialist_id=specialist.id, slots=four
    )

    assert model_result == {"error": "invalid_tool_call"}
    assert card is None


def test_unknown_inactive_and_other_salon_services_are_identically_not_found(
    tbilisi_salon, other_salon, service, service_category, specialist
):
    with tenant_context(tbilisi_salon.id):
        inactive = Service.objects.create(
            salon=tbilisi_salon,
            category=service_category,
            name={"en": "Old service"},
            duration_minutes=30,
            price="100.00",
            is_active=False,
        )
        SpecialistService.objects.create(
            salon=tbilisi_salon, specialist=specialist, service=inactive
        )
    with tenant_context(other_salon.id):
        other_category = ServiceCategory.objects.create(salon=other_salon, name={"en": "Nails"})
        foreign = Service.objects.create(
            salon=other_salon,
            category=other_category,
            name={"en": "Foreign manicure"},
            duration_minutes=60,
            price="500.00",
        )

    results = [
        _propose(tbilisi_salon, service_id=service_id, specialist_id=specialist.id)
        for service_id in (999_999, inactive.id, foreign.id)
    ]

    assert results == [({"error": "not_found"}, None)] * 3


def test_inactive_other_salon_and_unknown_specialists_are_identically_not_found(
    tbilisi_salon, other_salon, service
):
    inactive = _specialist(
        tbilisi_salon, name={"en": "Retired"}, services=[service], is_active=False
    )
    foreign = _specialist(other_salon, name={"en": "Foreign"})

    results = [
        _propose(tbilisi_salon, service_id=service.id, specialist_id=specialist_id)
        for specialist_id in (inactive.id, foreign.id, 999_999)
    ]

    assert results == [({"error": "not_found"}, None)] * 3


def test_unlinked_active_specialist_does_not_offer_the_service(tbilisi_salon, service):
    unlinked = _specialist(tbilisi_salon, name={"en": "Mary"})

    model_result, card = _propose(tbilisi_salon, service_id=service.id, specialist_id=unlinked.id)

    assert model_result == {"error": "specialist_does_not_offer_service"}
    assert card is None


def test_the_requested_higher_pk_specialist_is_shown(tbilisi_salon, service, specialist):
    mary = _specialist(tbilisi_salon, name={"en": "Mary"}, services=[service])
    assert mary.pk > specialist.pk

    _model_result, card = _propose(tbilisi_salon, service_id=service.id, specialist_id=mary.id)

    assert card is not None
    assert card["specialist"]["id"] == mary.id
    assert card["specialist"]["name"] == "Mary"


def test_names_resolved_in_the_site_language_with_english_fallback(tbilisi_salon, service_category):
    with tenant_context(tbilisi_salon.id):
        service = Service.objects.create(
            salon=tbilisi_salon,
            category=service_category,
            name={"en": "Manicure", "uk": "Манікюр"},
            duration_minutes=60,
            price="500.00",
        )
    jane = _specialist(tbilisi_salon, name={"en": "Jane"}, services=[service])

    _model_result, card = _propose(
        tbilisi_salon, lang="uk", service_id=service.id, specialist_id=jane.id
    )

    assert card is not None
    assert card["service"]["name"] == "Манікюр"
    assert card["specialist"]["name"] == "Jane"


def test_duplicate_slots_merged_and_sorted_earliest_first(tbilisi_salon, service, specialist):
    _monday_hours(tbilisi_salon, specialist)
    # The same instant as SLOT_1100, written in UTC.
    same_as_1100 = "2026-08-17T07:00:00+00:00"

    model_result, card = _propose(
        tbilisi_salon,
        service_id=service.id,
        specialist_id=specialist.id,
        slots=[SLOT_1100, same_as_1100, SLOT_0900],
    )

    assert card is not None
    assert card["slots"] == [SLOT_0900, SLOT_1100]
    assert model_result == {"shown": True, "slots": [SLOT_0900, SLOT_1100], "dropped": []}


def test_slot_in_another_offset_is_shown_in_salon_local_time(tbilisi_salon, service, specialist):
    _monday_hours(tbilisi_salon, specialist)

    model_result, card = _propose(
        tbilisi_salon,
        service_id=service.id,
        specialist_id=specialist.id,
        slots=["2026-08-17T07:00:00+00:00"],
    )

    assert card is not None
    assert card["slots"] == [SLOT_1100]
    assert model_result == {"shown": True, "slots": [SLOT_1100], "dropped": []}


def test_without_tenant_context_raises(service, specialist):
    with pytest.raises(TenantContextMissingError):
        handle_propose_booking(
            lang="en",
            now=NOW,
            arguments={"service_id": service.id, "specialist_id": specialist.id},
        )


def test_model_result_and_card_are_json_serializable(tbilisi_salon, service, specialist):
    _monday_hours(tbilisi_salon, specialist)

    model_result, card = _propose(
        tbilisi_salon, service_id=service.id, specialist_id=specialist.id, slots=[SLOT_0900]
    )

    json.dumps(model_result)
    json.dumps(card)


def test_spec_shape() -> None:
    parameters = PROPOSE_BOOKING_SPEC.parameters

    assert PROPOSE_BOOKING_SPEC.name == "propose_booking"
    assert parameters["properties"]["slots"]["maxItems"] == 3
    assert set(parameters["required"]) == {"service_id", "specialist_id"}
    for forbidden in ("salon", "lang", "now"):
        assert not any(forbidden in name for name in parameters["properties"])
