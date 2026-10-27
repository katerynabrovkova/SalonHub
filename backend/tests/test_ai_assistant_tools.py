"""
Stage 16 sub-step 4b/4c — read-only grounding tools (docs/DECISIONS.md §
"Stage 16 planning", "Sub-step 4 details", 4b and 4c): list_services,
get_specialist_info, get_salon_info, get_faq, check_availability and the
TOOLS registry.

Isolation on two levels (CLAUDE.md): another salon's rows never appear, and
within one salon a lookup by id returns the requested row, tested on the
higher pk.
"""

import datetime as dt
import json
from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

import booking.services
from ai_assistant.tools import (
    TOOLS,
    check_availability,
    get_faq,
    get_salon_info,
    get_specialist_info,
    list_services,
)
from booking.models import AppointmentStatus
from catalog.models import Service, ServiceCategory
from core.tenancy import TenantContextMissingError, tenant_context
from reviews.models import Review
from scheduling.services import (
    compute_candidate_start_times,
    compute_multi_specialist_availability,
)
from specialists.models import DayOfWeek, Specialist
from tenants.models import FAQItem
from tests.conftest import make_appointment, make_working_hours

pytestmark = pytest.mark.django_db

REVIEW_TEXT = "Private review text that must never reach the model"


def _make_service(
    salon,
    category,
    *,
    name: dict[str, str],
    price: str = "300.00",
    duration_minutes: int = 45,
    **extra,
):
    with tenant_context(salon.id):
        return Service.objects.create(
            salon=salon,
            category=category,
            name=name,
            duration_minutes=duration_minutes,
            price=price,
            **extra,
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
def test_tool_without_tenant_context_raises(tool_name, request):
    _spec, function = TOOLS[tool_name]
    extra_arguments: dict[str, Callable[[], dict[str, Any]]] = {
        "check_availability": lambda: {
            "now": NOW,
            "service_id": request.getfixturevalue("service").id,
            "specialist_id": "any",
            "date_from": WEEK_FROM,
            "date_to": WEEK_TO,
        },
    }
    arguments = extra_arguments.get(tool_name, dict)()

    with pytest.raises(TenantContextMissingError):
        function(lang="en", **arguments)


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
            # Sub-step 10d: weekly hours (test_ai_assistant_specialist_hours.py).
            "working_hours": {},
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


def test_registry_has_exactly_the_five_tools_and_no_salon_parameter():
    assert set(TOOLS) == {
        "list_services",
        "get_specialist_info",
        "get_salon_info",
        "get_faq",
        "check_availability",
    }
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


# --- check_availability ----------------------------------------------------
#
# The salon runs on Asia/Tbilisi (UTC+4, no DST) with 60-minute slots and a
# 60-minute service without buffer, so each working hour is one slot. NOW is
# Tuesday 2030-01-01 00:00 UTC; the test week is Monday 2030-01-07 to Sunday
# 2030-01-13, well past the lead time and inside max_advance_days.

NOW = dt.datetime(2030, 1, 1, tzinfo=dt.UTC)
TBILISI = ZoneInfo("Asia/Tbilisi")
WEEK_FROM = "2030-01-07"
WEEK_TO = "2030-01-13"


@pytest.fixture
def tbilisi_salon(salon, other_salon):
    for each in (salon, other_salon):
        each.timezone = "Asia/Tbilisi"
        each.slot_granularity_minutes = 60
        each.save(update_fields=["timezone", "slot_granularity_minutes"])
    return salon


@pytest.fixture
def hour_service(tbilisi_salon, service_category):
    return _make_service(
        tbilisi_salon, service_category, name={"en": "Gel polish"}, duration_minutes=60
    )


def _hours(salon, specialist, day: DayOfWeek, start_hour: int, end_hour: int) -> None:
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=day,
        start_time=dt.time(start_hour),
        end_time=dt.time(end_hour),
    )


def _local(day: int, hour: int) -> str:
    """Salon-local January 2030 start time, formatted like the slots endpoint."""
    return dt.datetime(2030, 1, day, hour, tzinfo=TBILISI).isoformat()


def _endpoint_format(candidates) -> list[str]:
    return [candidate.astimezone(TBILISI).isoformat() for candidate in candidates]


def _check(salon, **arguments) -> dict[str, Any]:
    arguments.setdefault("date_from", WEEK_FROM)
    arguments.setdefault("date_to", WEEK_TO)
    with tenant_context(salon.id):
        return check_availability(lang="en", now=NOW, **arguments)


def test_check_availability_specific_specialist_matches_the_engine(tbilisi_salon, hour_service):
    jane = _make_specialist(tbilisi_salon, name="Jane", services=[hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.MONDAY, 9, 12)
    _hours(tbilisi_salon, jane, DayOfWeek.WEDNESDAY, 14, 16)

    with tenant_context(tbilisi_salon.id):
        engine = compute_candidate_start_times(
            specialist=jane,
            service=hour_service,
            salon=tbilisi_salon,
            date_from=dt.date(2030, 1, 7),
            date_to=dt.date(2030, 1, 13),
            now=NOW,
        )
    expected = _endpoint_format(engine)
    assert expected == [_local(7, 9), _local(7, 10), _local(7, 11), _local(9, 14), _local(9, 15)]

    result = _check(tbilisi_salon, service_id=hour_service.id, specialist_id=jane.id)

    assert result == {"slots": expected, "has_more": False}


def test_check_availability_any_matches_the_multi_specialist_keys(tbilisi_salon, hour_service):
    jane = _make_specialist(tbilisi_salon, name="Jane", services=[hour_service])
    mary = _make_specialist(tbilisi_salon, name="Mary", services=[hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.MONDAY, 9, 11)
    _hours(tbilisi_salon, mary, DayOfWeek.MONDAY, 10, 12)
    _hours(tbilisi_salon, mary, DayOfWeek.TUESDAY, 9, 10)

    with tenant_context(tbilisi_salon.id):
        engine = compute_multi_specialist_availability(
            service=hour_service,
            salon=tbilisi_salon,
            date_from=dt.date(2030, 1, 7),
            date_to=dt.date(2030, 1, 13),
            now=NOW,
        )
    expected = _endpoint_format(engine.keys())
    assert expected == [_local(7, 9), _local(7, 10), _local(7, 11), _local(8, 9)]

    result = _check(tbilisi_salon, service_id=hour_service.id, specialist_id="any")

    assert result == {"slots": expected, "has_more": False}


def test_check_availability_weekday_filter_uses_salon_local_days(tbilisi_salon, hour_service):
    jane = _make_specialist(tbilisi_salon, name="Jane", services=[hour_service])
    # Local Saturday 00:00-02:00 is Friday 20:00-22:00 UTC.
    _hours(tbilisi_salon, jane, DayOfWeek.SATURDAY, 0, 2)
    _hours(tbilisi_salon, jane, DayOfWeek.FRIDAY, 10, 12)
    saturday_midnight = dt.datetime(2030, 1, 12, 0, tzinfo=TBILISI)
    assert saturday_midnight.astimezone(dt.UTC).weekday() == DayOfWeek.FRIDAY

    result = _check(
        tbilisi_salon, service_id=hour_service.id, specialist_id=jane.id, weekdays=["saturday"]
    )

    assert result == {"slots": [_local(12, 0), _local(12, 1)], "has_more": False}


def test_check_availability_empty_weekdays_means_no_weekday_filter(tbilisi_salon, hour_service):
    jane = _make_specialist(tbilisi_salon, name="Jane", services=[hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.MONDAY, 9, 11)
    _hours(tbilisi_salon, jane, DayOfWeek.SATURDAY, 9, 10)

    unfiltered = _check(tbilisi_salon, service_id=hour_service.id, specialist_id=jane.id)
    empty_list = _check(
        tbilisi_salon, service_id=hour_service.id, specialist_id=jane.id, weekdays=[]
    )

    assert unfiltered == {
        "slots": [_local(7, 9), _local(7, 10), _local(12, 9)],
        "has_more": False,
    }
    assert empty_list == unfiltered


def test_check_availability_time_filter_uses_salon_local_time_half_open(
    tbilisi_salon, hour_service
):
    jane = _make_specialist(tbilisi_salon, name="Jane", services=[hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.MONDAY, 9, 13)

    result = _check(
        tbilisi_salon,
        service_id=hour_service.id,
        specialist_id=jane.id,
        time_from="10:00",
        time_to="12:00",
    )

    # 10:00 equals time_from and is kept; 12:00 equals time_to and is not.
    assert result == {"slots": [_local(7, 10), _local(7, 11)], "has_more": False}


def test_check_availability_spreads_each_days_slots_and_reports_more(tbilisi_salon, hour_service):
    """Was ..._more_than_20_matches_returns_the_20_earliest; the 20-earliest
    cap was replaced by the spread on 27.10.2026 (sub-step 10c). Free starts
    09:00-20:00: the first, the last, and the one nearest the middle (14:00
    and 15:00 tie; the earlier wins)."""
    jane = _make_specialist(tbilisi_salon, name="Jane", services=[hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.MONDAY, 9, 21)
    _hours(tbilisi_salon, jane, DayOfWeek.TUESDAY, 9, 21)

    result = _check(tbilisi_salon, service_id=hour_service.id, specialist_id=jane.id)

    expected = [_local(day, hour) for day in (7, 8) for hour in (9, 14, 20)]
    assert result == {"slots": expected, "has_more": True}


def test_check_availability_found_slots_cover_the_first_3_days_with_any(
    tbilisi_salon, hour_service
):
    """Was ..._exactly_20_matches_has_no_more; replaced with the spread's
    day limit on 27.10.2026 (sub-step 10c)."""
    jane = _make_specialist(tbilisi_salon, name="Jane", services=[hour_service])
    for day in (DayOfWeek.MONDAY, DayOfWeek.TUESDAY, DayOfWeek.WEDNESDAY, DayOfWeek.THURSDAY):
        _hours(tbilisi_salon, jane, day, 9, 10)

    result = _check(tbilisi_salon, service_id=hour_service.id, specialist_id=jane.id)

    assert result == {"slots": [_local(7, 9), _local(8, 9), _local(9, 9)], "has_more": True}


def test_check_availability_14_days_inclusive_is_accepted_15_is_too_long(
    tbilisi_salon, hour_service
):
    jane = _make_specialist(tbilisi_salon, name="Jane", services=[hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.MONDAY, 9, 10)

    fourteen_days = _check(
        tbilisi_salon,
        service_id=hour_service.id,
        specialist_id=jane.id,
        date_from="2030-01-07",
        date_to="2030-01-20",
    )
    fifteen_days = _check(
        tbilisi_salon,
        service_id=hour_service.id,
        specialist_id=jane.id,
        date_from="2030-01-07",
        date_to="2030-01-21",
    )

    assert fourteen_days == {"slots": [_local(7, 9), _local(14, 9)], "has_more": False}
    assert fifteen_days == {"error": "range_too_long"}


@pytest.mark.parametrize(
    ("date_from", "date_to"),
    [
        ("2030-01-08", "2030-01-07"),
        ("2030-02-30", "2030-03-01"),
        ("not-a-date", "2030-01-07"),
    ],
    ids=["from_after_to", "impossible_date", "unparseable"],
)
def test_check_availability_bad_range_is_invalid_range(
    tbilisi_salon, hour_service, date_from, date_to
):
    jane = _make_specialist(tbilisi_salon, name="Jane", services=[hour_service])

    result = _check(
        tbilisi_salon,
        service_id=hour_service.id,
        specialist_id=jane.id,
        date_from=date_from,
        date_to=date_to,
    )

    assert result == {"error": "invalid_range"}


@pytest.mark.parametrize(
    "bad_filter",
    [
        {"weekdays": ["funday"]},
        {"weekdays": "monday"},
        {"weekdays": {"monday": True}},
        {"time_from": "25:00"},
    ],
    ids=["unknown_weekday", "weekdays_not_a_list", "weekdays_dict", "hour_25"],
)
def test_check_availability_bad_filter_is_invalid_filter(tbilisi_salon, hour_service, bad_filter):
    jane = _make_specialist(tbilisi_salon, name="Jane", services=[hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.MONDAY, 9, 12)

    result = _check(tbilisi_salon, service_id=hour_service.id, specialist_id=jane.id, **bad_filter)

    assert result == {"error": "invalid_filter"}


@pytest.mark.parametrize(
    ("time_from", "time_to"),
    [("18:00", "10:00"), ("12:00", "12:00")],
    ids=["from_after_to", "equal_bounds"],
)
def test_check_availability_time_from_not_before_time_to_is_invalid_filter(
    tbilisi_salon, hour_service, time_from, time_to
):
    jane = _make_specialist(tbilisi_salon, name="Jane", services=[hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.MONDAY, 9, 21)

    result = _check(
        tbilisi_salon,
        service_id=hour_service.id,
        specialist_id=jane.id,
        time_from=time_from,
        time_to=time_to,
    )

    assert result == {"error": "invalid_filter"}


def test_check_availability_unavailable_service_or_specialist_is_not_found(
    tbilisi_salon, other_salon, hour_service, service_category
):
    jane = _make_specialist(tbilisi_salon, name="Jane", services=[hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.MONDAY, 9, 12)
    retired_service = _make_service(
        tbilisi_salon,
        service_category,
        name={"en": "Retired"},
        duration_minutes=60,
        is_active=False,
    )
    with tenant_context(tbilisi_salon.id):
        jane.services.add(retired_service, through_defaults={"salon_id": tbilisi_salon.id})
    former = _make_specialist(
        tbilisi_salon, name="Former", services=[hour_service], is_active=False
    )
    _hours(tbilisi_salon, former, DayOfWeek.MONDAY, 9, 12)
    with tenant_context(other_salon.id):
        other_category = ServiceCategory.objects.create(salon=other_salon, name={"en": "Nails"})
    other_service = _make_service(
        other_salon, other_category, name={"en": "Elsewhere"}, duration_minutes=60
    )
    elsewhere = _make_specialist(other_salon, name="Elsewhere", services=[other_service])
    _hours(other_salon, elsewhere, DayOfWeek.MONDAY, 9, 12)
    missing_id = max(hour_service.id, retired_service.id, other_service.id, elsewhere.id) + 1000

    cases = {
        "inactive_service": {"service_id": retired_service.id, "specialist_id": jane.id},
        "inactive_service_any": {"service_id": retired_service.id, "specialist_id": "any"},
        "inactive_specialist": {"service_id": hour_service.id, "specialist_id": former.id},
        "other_salon_service": {"service_id": other_service.id, "specialist_id": jane.id},
        "other_salon_service_any": {"service_id": other_service.id, "specialist_id": "any"},
        "other_salon_specialist": {"service_id": hour_service.id, "specialist_id": elsewhere.id},
        "missing_service": {"service_id": missing_id, "specialist_id": jane.id},
        "missing_specialist": {"service_id": hour_service.id, "specialist_id": missing_id},
    }

    results = {name: _check(tbilisi_salon, **arguments) for name, arguments in cases.items()}

    assert results == {name: {"error": "not_found"} for name in cases}


def test_check_availability_unlinked_specialist_does_not_offer_service(tbilisi_salon, hour_service):
    jane = _make_specialist(tbilisi_salon, name="Jane")
    _hours(tbilisi_salon, jane, DayOfWeek.MONDAY, 9, 12)

    result = _check(tbilisi_salon, service_id=hour_service.id, specialist_id=jane.id)

    assert result == {"error": "specialist_does_not_offer_service"}


def test_check_availability_returns_the_requested_specialists_slots(tbilisi_salon, hour_service):
    first = _make_specialist(tbilisi_salon, name="Jane", services=[hour_service])
    second = _make_specialist(tbilisi_salon, name="Mary", services=[hour_service])
    assert second.pk > first.pk
    _hours(tbilisi_salon, first, DayOfWeek.MONDAY, 9, 10)
    _hours(tbilisi_salon, second, DayOfWeek.TUESDAY, 14, 16)

    result = _check(tbilisi_salon, service_id=hour_service.id, specialist_id=second.id)

    assert result == {"slots": [_local(8, 14), _local(8, 15)], "has_more": False}


def test_check_availability_no_matching_slots_is_an_empty_list(tbilisi_salon, hour_service):
    """Updated in sub-step 10c (approved 27.10.2026): an empty result now
    carries the reason and alternatives (test_ai_assistant_availability_alternatives.py)."""
    jane = _make_specialist(tbilisi_salon, name="Jane", services=[hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.MONDAY, 9, 12)

    result = _check(
        tbilisi_salon, service_id=hour_service.id, specialist_id=jane.id, weekdays=["sunday"]
    )

    assert result == {
        "slots": [],
        "has_more": False,
        "reason": "not_working",
        "alternatives": {
            "slots": [_local(7, 9), _local(7, 10), _local(7, 11)]
            + [_local(14, 9), _local(14, 10), _local(14, 11)],
            "relaxed": ["day"],
        },
    }


def test_check_availability_output_is_json_serializable(tbilisi_salon, hour_service):
    jane = _make_specialist(tbilisi_salon, name="Jane", services=[hour_service])
    _hours(tbilisi_salon, jane, DayOfWeek.MONDAY, 9, 12)

    outputs = [
        _check(tbilisi_salon, service_id=hour_service.id, specialist_id=jane.id),
        _check(tbilisi_salon, service_id=hour_service.id, specialist_id="any"),
        _check(tbilisi_salon, service_id=hour_service.id, specialist_id=-1),
    ]

    for output in outputs:
        json.dumps(output)
