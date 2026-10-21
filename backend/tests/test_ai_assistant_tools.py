"""
Stage 16 sub-step 4b — read-only grounding tools (docs/DECISIONS.md §
"Stage 16 planning", "Sub-step 4 details", 4b): list_services,
get_specialist_info, get_salon_info, get_faq and the TOOLS registry.

Isolation on two levels (CLAUDE.md): another salon's rows never appear, and
within one salon a lookup by id returns the requested row, tested on the
higher pk.
"""

import datetime as dt
import json
from typing import Any

import pytest
from django.utils import timezone

import booking.services
from ai_assistant.tools import (
    TOOLS,
    get_faq,
    get_salon_info,
    get_specialist_info,
    list_services,
)
from booking.models import AppointmentStatus
from catalog.models import Service, ServiceCategory
from core.tenancy import TenantContextMissingError, tenant_context
from reviews.models import Review
from specialists.models import Specialist
from tenants.models import FAQItem
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

REVIEW_TEXT = "Private review text that must never reach the model"


def _make_service(salon, category, *, name: dict[str, str], price: str = "300.00", **extra):
    with tenant_context(salon.id):
        return Service.objects.create(
            salon=salon, category=category, name=name, duration_minutes=45, price=price, **extra
        )


def _make_specialist(salon, *, name: str, services=(), **extra) -> Specialist:
    with tenant_context(salon.id):
        specialist = Specialist.objects.create(salon=salon, name={"en": name}, **extra)
        if services:
            specialist.services.set(services, through_defaults={"salon_id": salon.id})
        return specialist


def _make_review(*, salon, specialist, customer, service, rating: int, day: int) -> Review:
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=timezone.now() + dt.timedelta(days=100 + day),
        status=AppointmentStatus.COMPLETED,
    )
    with tenant_context(salon.id):
        return Review.objects.create(
            salon=salon,
            appointment=appointment,
            customer=customer,
            specialist=specialist,
            rating=rating,
            text=REVIEW_TEXT,
        )


def _walk(value: Any):
    """Every key and every scalar value in a nested dict/list, recursively."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)
    else:
        yield value


# --- list_services ---------------------------------------------------------


def test_list_services_returns_only_the_bound_salons_active_services(
    salon, other_salon, service_category, service
):
    _make_service(salon, service_category, name={"en": "Old service"}, is_active=False)
    with tenant_context(other_salon.id):
        other_category = ServiceCategory.objects.create(salon=other_salon, name={"en": "Nails"})
    _make_service(other_salon, other_category, name={"en": "Other salon service"})

    with tenant_context(salon.id):
        result = list_services(lang="en")

    assert [item["id"] for item in result["services"]] == [service.id]


def test_list_services_resolves_names_for_lang_with_english_fallback(salon):
    with tenant_context(salon.id):
        category = ServiceCategory.objects.create(salon=salon, name={"en": "Nails", "uk": "Нігті"})
    translated = _make_service(salon, category, name={"en": "Manicure", "uk": "Манікюр"})
    english_only = _make_service(salon, category, name={"en": "Pedicure"})

    with tenant_context(salon.id):
        result = list_services(lang="uk")

    by_id = {item["id"]: item for item in result["services"]}
    assert by_id[translated.id]["name"] == "Манікюр"
    assert by_id[translated.id]["category"] == "Нігті"
    assert by_id[english_only.id]["name"] == "Pedicure"


def test_list_services_money_comes_from_the_deposit_breakdown(salon, service_category):
    salon.currency = "EUR"
    salon.save(update_fields=["currency"])
    priced = _make_service(salon, service_category, name={"en": "Brows"}, price="42.50")

    with tenant_context(salon.id):
        result = list_services(lang="en")

    assert result["currency"] == "EUR"
    item = next(item for item in result["services"] if item["id"] == priced.id)
    assert item["price"] == "42.50"
    assert item["deposit"] == "9.00"
    assert item["due_at_visit"] == "33.50"
    assert item["duration_minutes"] == 45


@pytest.mark.parametrize("tool_name", sorted(TOOLS))
def test_tool_without_tenant_context_raises(tool_name):
    _spec, function = TOOLS[tool_name]

    with pytest.raises(TenantContextMissingError):
        function(lang="en")


# --- get_specialist_info ---------------------------------------------------


def test_get_specialist_info_lists_active_specialists_with_active_services_and_rating(
    salon, other_salon, service_category, service, customer
):
    retired_service = _make_service(
        salon, service_category, name={"en": "Retired"}, is_active=False
    )
    active = _make_specialist(salon, name="Jane", services=[service, retired_service])
    _make_specialist(salon, name="Former", services=[service], is_active=False)
    _make_specialist(other_salon, name="Elsewhere")
    _make_review(
        salon=salon, specialist=active, customer=customer, service=service, rating=4, day=0
    )

    with tenant_context(salon.id):
        result = get_specialist_info(lang="en")

    assert result["specialists"] == [
        {
            "id": active.id,
            "name": "Jane",
            "bio": "",
            "services": [{"id": service.id, "name": "Manicure"}],
            "average_rating": 4.0,
            "review_count": 1,
        }
    ]


def test_get_specialist_info_other_salons_id_is_not_found_like_a_missing_id(salon, other_salon):
    elsewhere = _make_specialist(other_salon, name="Elsewhere")

    with tenant_context(salon.id):
        other_salon_result = get_specialist_info(lang="en", specialist_id=elsewhere.id)
        missing_result = get_specialist_info(lang="en", specialist_id=elsewhere.id + 1000)

    assert other_salon_result == {"error": "not_found"}
    assert other_salon_result == missing_result


def test_get_specialist_info_by_id_returns_the_requested_specialist(salon, service):
    first = _make_specialist(salon, name="Jane", services=[service])
    second = _make_specialist(salon, name="Mary", services=[service])
    assert second.pk > first.pk

    with tenant_context(salon.id):
        result = get_specialist_info(lang="en", specialist_id=second.id)

    assert [item["id"] for item in result["specialists"]] == [second.id]
    assert result["specialists"][0]["name"] == "Mary"


def test_get_specialist_info_never_contains_review_text(salon, service, customer):
    specialist = _make_specialist(salon, name="Jane", services=[service])
    _make_review(
        salon=salon, specialist=specialist, customer=customer, service=service, rating=5, day=0
    )

    with tenant_context(salon.id):
        results = [
            get_specialist_info(lang="en"),
            get_specialist_info(lang="en", specialist_id=specialist.id),
        ]

    for result in results:
        for item in _walk(result):
            assert item != "text"
            assert not (isinstance(item, str) and REVIEW_TEXT in item)


def test_get_specialist_info_bad_id_is_not_found_without_raising(salon):
    with tenant_context(salon.id):
        non_numeric = get_specialist_info(lang="en", specialist_id="abc")
        negative = get_specialist_info(lang="en", specialist_id=-1)

    assert non_numeric == {"error": "not_found"}
    assert negative == {"error": "not_found"}


# --- get_salon_info --------------------------------------------------------


def test_get_salon_info_returns_rule_and_contacts_but_not_contact_email(salon):
    salon.phone = "+380 44 000 0000"
    salon.address = "1 Main Street, Kyiv"
    salon.instagram_url = "https://instagram.com/bella-demo"
    salon.save(update_fields=["phone", "address", "instagram_url"])

    with tenant_context(salon.id):
        result = get_salon_info(lang="en")

    assert result["name"] == "Bella Demo Salon"
    assert result["timezone"] == "Europe/Kyiv"
    assert result["currency"] == "UAH"
    assert result["deposit_percentage"] == "20.00"
    assert result["refund_cutoff_hours"] == 24
    assert result["phone"] == "+380 44 000 0000"
    assert result["address"] == "1 Main Street, Kyiv"
    assert result["instagram_url"] == "https://instagram.com/bella-demo"
    assert "contact_email" not in result
    assert salon.contact_email not in json.dumps(result)


def test_get_salon_info_cutoff_comes_from_bookings_constant(salon, monkeypatch):
    monkeypatch.setattr(booking.services, "REFUND_ELIGIBILITY_CUTOFF", dt.timedelta(hours=48))

    with tenant_context(salon.id):
        result = get_salon_info(lang="en")

    assert result["refund_cutoff_hours"] == 48


# --- get_faq ---------------------------------------------------------------


def test_get_faq_returns_only_the_bound_salons_active_items_in_order(salon, other_salon):
    with tenant_context(salon.id):
        late = FAQItem.objects.create(salon=salon, question="Late?", answer="L.", ordering=2)
        tie_first = FAQItem.objects.create(salon=salon, question="Tie A?", answer="A.", ordering=1)
        tie_second = FAQItem.objects.create(salon=salon, question="Tie B?", answer="B.", ordering=1)
        FAQItem.objects.create(salon=salon, question="Hidden?", answer="H.", is_active=False)
    with tenant_context(other_salon.id):
        FAQItem.objects.create(salon=other_salon, question="Other?", answer="O.")

    with tenant_context(salon.id):
        result = get_faq(lang="en")

    assert result == {
        "items": [
            {"question": tie_first.question, "answer": tie_first.answer},
            {"question": tie_second.question, "answer": tie_second.answer},
            {"question": late.question, "answer": late.answer},
        ]
    }


# --- registry and output shape ---------------------------------------------


def test_registry_has_exactly_the_four_tools_and_no_salon_parameter():
    assert set(TOOLS) == {"list_services", "get_specialist_info", "get_salon_info", "get_faq"}
    for name, (spec, _function) in TOOLS.items():
        assert spec.name == name
        for parameter in spec.parameters.get("properties", {}):
            assert "salon" not in parameter.lower()


def test_every_tool_output_is_json_serializable(salon, service, customer):
    specialist = _make_specialist(salon, name="Jane", services=[service])
    _make_review(
        salon=salon, specialist=specialist, customer=customer, service=service, rating=5, day=0
    )
    with tenant_context(salon.id):
        FAQItem.objects.create(salon=salon, question="Parking?", answer="Yes.")

    with tenant_context(salon.id):
        outputs = [
            list_services(lang="en"),
            get_specialist_info(lang="en"),
            get_specialist_info(lang="en", specialist_id=specialist.id),
            get_specialist_info(lang="en", specialist_id=-1),
            get_salon_info(lang="en"),
            get_faq(lang="en"),
        ]

    for output in outputs:
        json.dumps(output)
