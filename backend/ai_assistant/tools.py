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

TOOLS maps each tool name to its ToolSpec (what the model sees) and its
function.
"""

import datetime as dt
from collections.abc import Callable
from typing import Any

from django.db.models import Prefetch

from ai_assistant.providers.base import ToolSpec
from booking import services as booking_services
from catalog.models import Service
from core.i18n import resolve_translation
from core.tenancy import TenantContextMissingError, get_current_salon_id
from payments.services import compute_deposit_breakdown
from specialists.models import Specialist
from specialists.services import annotate_rating
from tenants.models import FAQItem, Salon

NOT_FOUND: dict[str, Any] = {"error": "not_found"}

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
}
