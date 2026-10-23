"""
Stage 16 sub-step 4b — read-only grounding tools (docs/DECISIONS.md §
"Stage 16 planning", "Sub-step 4 details", 4b).

Each tool is a plain function with keyword-only arguments: `lang`, passed
by the orchestrator (never by the model), plus the arguments the model may
send. Each returns a JSON-serializable dict. The salon comes only from the
bound tenant context (core.tenancy.tenant_context); no tool takes a salon
argument. Money comes only from payments.services.compute_deposit_breakdown,
as strings like the API ("40.00"). An unknown or another salon's id returns
NOT_FOUND; tools never raise on bad model input.

check_availability (4c) also takes `now` from the orchestrator and reads
availability only from scheduling.services.

TOOLS maps each tool name to its ToolSpec (what the model sees) and its
function.
"""

import datetime as dt
import re
from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo

from django.db.models import Prefetch

from ai_assistant.providers.base import ToolSpec
from booking import services as booking_services
from catalog.models import Service
from core.i18n import resolve_translation
from core.tenancy import TenantContextMissingError, get_current_salon_id
from payments.services import compute_deposit_breakdown
from scheduling.services import (
    compute_candidate_start_times,
    compute_multi_specialist_availability,
)
from specialists.models import Specialist
from specialists.services import annotate_rating, specialist_offers_service
from tenants.models import FAQItem, Salon

NOT_FOUND: dict[str, Any] = {"error": "not_found"}

ANY_SPECIALIST = "any"
MAX_RANGE_DAYS = 14
MAX_SLOTS = 20
# Index matches dt.date.weekday() and specialists.models.DayOfWeek.
WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_TIME_PATTERN = re.compile(r"[0-9]{2}:[0-9]{2}")

_NO_PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}


def _current_salon() -> Salon:
    """
    The bound tenant's Salon. Every tool calls this first, so a missing
    tenant context fails loudly here rather than depending on which query a
    tool happens to run first (Salon itself is not tenant-scoped).
    """
    salon_id = get_current_salon_id()
    if salon_id is None:
        raise TenantContextMissingError("Assistant tools need a bound tenant context.")
    return Salon.objects.get(pk=salon_id)


def _parse_id(value: int | str | None) -> int | None:
    """A positive integer id from model input, or None for anything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isascii() and value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _parse_date(value: object) -> dt.date | None:
    """A "YYYY-MM-DD" date from model input, or None for anything else."""
    if not isinstance(value, str) or not _DATE_PATTERN.fullmatch(value):
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _parse_time(value: object) -> dt.time | None:
    """An "HH:MM" time from model input, or None for anything else."""
    if not isinstance(value, str) or not _TIME_PATTERN.fullmatch(value):
        return None
    try:
        return dt.time.fromisoformat(value)
    except ValueError:
        return None


def list_services(*, lang: str) -> dict[str, Any]:
    salon = _current_salon()
    services = Service.objects.filter(is_active=True).select_related("category")
    items = []
    for service in services:
        breakdown = compute_deposit_breakdown(
            price=service.price, percentage=salon.deposit_percentage
        )
        items.append(
            {
                "id": service.id,
                "name": resolve_translation(service.name, lang),
                "category": resolve_translation(service.category.name, lang),
                "description": service.description or "",
                "duration_minutes": service.duration_minutes,
                "price": str(service.price),
                "deposit": str(breakdown.deposit),
                "due_at_visit": str(breakdown.due_at_visit),
            }
        )
    return {"currency": salon.currency, "services": items}


def get_specialist_info(*, lang: str, specialist_id: int | str | None = None) -> dict[str, Any]:
    _current_salon()
    specialists = annotate_rating(
        Specialist.objects.filter(is_active=True).prefetch_related(
            Prefetch("services", queryset=Service.objects.filter(is_active=True))
        )
    ).order_by("created_at", "id")
    if specialist_id is not None:
        parsed_id = _parse_id(specialist_id)
        if parsed_id is None:
            return NOT_FOUND
        specialists = specialists.filter(pk=parsed_id)
    items = [
        {
            "id": specialist.id,
            "name": resolve_translation(specialist.name, lang),
            "bio": resolve_translation(specialist.bio, lang),
            "services": [
                {"id": service.id, "name": resolve_translation(service.name, lang)}
                for service in specialist.services.all()
            ],
            "average_rating": specialist.average_rating,  # type: ignore[attr-defined]
            "review_count": specialist.review_count,  # type: ignore[attr-defined]
        }
        for specialist in specialists
    ]
    if specialist_id is not None and not items:
        return NOT_FOUND
    return {"specialists": items}


def get_salon_info(*, lang: str) -> dict[str, Any]:
    salon = _current_salon()
    # Read through the module at call time, never imported by name, so the
    # tool always states booking's actual cutoff.
    cutoff = booking_services.REFUND_ELIGIBILITY_CUTOFF
    return {
        "name": resolve_translation(salon.name, lang),
        "timezone": salon.timezone,
        "currency": salon.currency,
        "deposit_percentage": str(salon.deposit_percentage),
        "refund_cutoff_hours": int(cutoff / dt.timedelta(hours=1)),
        "phone": salon.phone,
        "address": salon.address,
        "instagram_url": salon.instagram_url,
    }


def get_faq(*, lang: str) -> dict[str, Any]:
    _current_salon()
    return {
        "items": [
            {"question": item.question, "answer": item.answer}
            for item in FAQItem.objects.filter(is_active=True)
        ]
    }


def check_availability(
    *,
    lang: str,
    now: dt.datetime,
    service_id: int | str | None,
    specialist_id: int | str | None,
    date_from: str | None,
    date_to: str | None,
    weekdays: list[str] | None = None,
    time_from: str | None = None,
    time_to: str | None = None,
) -> dict[str, Any]:
    salon = _current_salon()

    start_date = _parse_date(date_from)
    end_date = _parse_date(date_to)
    if start_date is None or end_date is None or start_date > end_date:
        return {"error": "invalid_range"}
    if (end_date - start_date).days > MAX_RANGE_DAYS - 1:
        return {"error": "range_too_long"}

    weekday_numbers: set[int] | None = None
    if weekdays is not None and not isinstance(weekdays, list):
        return {"error": "invalid_filter"}
    if weekdays:
        if not all(day in WEEKDAY_NAMES for day in weekdays):
            return {"error": "invalid_filter"}
        weekday_numbers = {WEEKDAY_NAMES.index(day) for day in weekdays}
    start_time = _parse_time(time_from)
    end_time = _parse_time(time_to)
    if (time_from is not None and start_time is None) or (time_to is not None and end_time is None):
        return {"error": "invalid_filter"}
    if start_time is not None and end_time is not None and start_time >= end_time:
        return {"error": "invalid_filter"}

    parsed_service_id = _parse_id(service_id)
    service = (
        Service.objects.filter(pk=parsed_service_id, is_active=True).first()
        if parsed_service_id is not None
        else None
    )
    if service is None:
        return NOT_FOUND

    if specialist_id == ANY_SPECIALIST:
        candidates = list(
            compute_multi_specialist_availability(
                service=service, salon=salon, date_from=start_date, date_to=end_date, now=now
            ).keys()
        )
    else:
        parsed_specialist_id = _parse_id(specialist_id)
        specialist = (
            Specialist.objects.filter(pk=parsed_specialist_id, is_active=True).first()
            if parsed_specialist_id is not None
            else None
        )
        if specialist is None:
            return NOT_FOUND
        if not specialist_offers_service(specialist, service):
            return {"error": "specialist_does_not_offer_service"}
        candidates = compute_candidate_start_times(
            specialist=specialist,
            service=service,
            salon=salon,
            date_from=start_date,
            date_to=end_date,
            now=now,
        )

    salon_tz = ZoneInfo(salon.timezone)
    matches = [
        local_start
        for local_start in sorted(candidate.astimezone(salon_tz) for candidate in candidates)
        if (weekday_numbers is None or local_start.weekday() in weekday_numbers)
        and (start_time is None or start_time <= local_start.time())
        and (end_time is None or local_start.time() < end_time)
    ]
    return {
        "slots": [local_start.isoformat() for local_start in matches[:MAX_SLOTS]],
        "has_more": len(matches) > MAX_SLOTS,
    }


TOOLS: dict[str, tuple[ToolSpec, Callable[..., dict[str, Any]]]] = {
    "list_services": (
        ToolSpec(
            name="list_services",
            description=(
                "List the salon's services with category, description, duration, "
                "price, the deposit paid online when booking, and the amount due at "
                "the visit. All amounts are in the returned currency."
            ),
            parameters=_NO_PARAMETERS,
        ),
        list_services,
    ),
    "get_specialist_info": (
        ToolSpec(
            name="get_specialist_info",
            description=(
                "List the salon's specialists with their bio, the services each one "
                "performs, average rating and review count. Pass specialist_id to get "
                "one specialist."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "specialist_id": {
                        "type": "integer",
                        "description": "Id of one specialist, as returned by this tool.",
                    }
                },
                "additionalProperties": False,
            },
        ),
        get_specialist_info,
    ),
    "get_salon_info": (
        ToolSpec(
            name="get_salon_info",
            description=(
                "Salon name, timezone, currency, deposit percentage, the cancellation "
                "and refund rule, and the customer contacts (phone, address, Instagram)."
            ),
            parameters=_NO_PARAMETERS,
        ),
        get_salon_info,
    ),
    "get_faq": (
        ToolSpec(
            name="get_faq",
            description="The salon's frequently asked questions and their answers.",
            parameters=_NO_PARAMETERS,
        ),
        get_faq,
    ),
    "check_availability": (
        ToolSpec(
            name="check_availability",
            description=(
                "Free start times for one service, with one specialist or any "
                "specialist, between two salon-local dates (at most 14 days). "
                "Optionally keep only some weekdays and a time-of-day window "
                "(time_from inclusive, time_to exclusive). Returns at most the 20 "
                "earliest times in salon-local ISO 8601 and has_more when more exist."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "service_id": {
                        "type": "integer",
                        "description": "Id of the service, as returned by list_services.",
                    },
                    "specialist_id": {
                        "anyOf": [
                            {"type": "integer"},
                            {"type": "string", "enum": [ANY_SPECIALIST]},
                        ],
                        "description": (
                            'Id of the specialist, as returned by get_specialist_info, or "any".'
                        ),
                    },
                    "date_from": {
                        "type": "string",
                        "description": "First salon-local date, YYYY-MM-DD.",
                    },
                    "date_to": {
                        "type": "string",
                        "description": "Last salon-local date, YYYY-MM-DD, inclusive.",
                    },
                    "weekdays": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": list(WEEKDAY_NAMES),
                        },
                        "description": "Keep only these salon-local weekdays.",
                    },
                    "time_from": {
                        "type": "string",
                        "description": (
                            "Earliest salon-local start time, HH:MM, inclusive. Both bounds "
                            "are within one day and time_from must be before time_to; for "
                            "two separate windows, call the tool twice."
                        ),
                    },
                    "time_to": {
                        "type": "string",
                        "description": (
                            "Salon-local start time limit, HH:MM, exclusive. Must be after "
                            "time_from, within the same day."
                        ),
                    },
                },
                "required": ["service_id", "specialist_id", "date_from", "date_to"],
                "additionalProperties": False,
            },
        ),
        check_availability,
    ),
}
