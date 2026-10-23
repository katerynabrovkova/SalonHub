"""
Stage 16 sub-step 6b — the `propose_booking` tool (docs/DECISIONS.md §
"Stage 16 planning", "Sub-step 6 details", 6b).

The orchestrator intercepts this tool: it reads and changes no data and
never books anything. `handle_propose_booking` validates the model's
proposal through tenant-scoped lookups and returns two things: the result
the model sees (what was shown, which slots were dropped, or an error code)
and the card data for the client's `proposal` event, computed by the
backend. The salon comes only from tenant context; `lang` and `now` come
from the orchestrator, never from the model.
"""

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

from django.utils import timezone

from ai_assistant.providers.base import ToolSpec
from ai_assistant.tools import ANY_SPECIALIST, NOT_FOUND, _current_salon
from catalog.models import Service
from core.i18n import resolve_translation
from payments.services import compute_deposit_breakdown
from scheduling.services import is_start_offered
from specialists.models import Specialist
from specialists.services import annotate_rating, specialist_offers_service

MAX_PROPOSAL_SLOTS = 3

PROPOSE_BOOKING_SPEC = ToolSpec(
    name="propose_booking",
    description=(
        "Show the customer a booking card for one service, with one specialist or any "
        "specialist, and up to 3 start times taken from check_availability. The card "
        "leads to the booking form; nothing is booked. Call it at most once per reply. "
        "The result says which times were shown and which were dropped because they "
        "are no longer free."
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
            "slots": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": MAX_PROPOSAL_SLOTS,
                "description": (
                    "Up to 3 start times exactly as returned by check_availability "
                    "(salon-local ISO 8601 with offset). Omit to let the customer "
                    "choose a time in the booking form."
                ),
            },
        },
        "required": ["service_id", "specialist_id"],
        "additionalProperties": False,
    },
)


INVALID_TOOL_CALL: dict[str, Any] = {"error": "invalid_tool_call"}

_ALLOWED_ARGUMENTS = frozenset({"service_id", "specialist_id", "slots"})


def _is_id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _arguments_match_spec(arguments: dict[str, Any]) -> bool:
    """The model's arguments against PROPOSE_BOOKING_SPEC: required keys,
    no unknown keys, the declared types, at most 3 slots."""
    if not set(arguments) <= _ALLOWED_ARGUMENTS:
        return False
    if "service_id" not in arguments or "specialist_id" not in arguments:
        return False
    if not _is_id(arguments["service_id"]):
        return False
    specialist_id = arguments["specialist_id"]
    if not (_is_id(specialist_id) or specialist_id == ANY_SPECIALIST):
        return False
    slots = arguments.get("slots", [])
    return (
        isinstance(slots, list)
        and len(slots) <= MAX_PROPOSAL_SLOTS
        and all(isinstance(slot, str) for slot in slots)
    )


def _parse_slot(value: str) -> dt.datetime | None:
    """An ISO 8601 datetime with an offset, or None (unparseable or naive)."""
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        return None
    return parsed


def handle_propose_booking(
    *, lang: str, now: dt.datetime, arguments: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """
    Returns (result for the model, card data or None). Bad model input never
    raises: a call that does not match the spec is `invalid_tool_call`; an
    unknown, inactive or other-salon id is `not_found`; an unlinked
    specialist is `specialist_does_not_offer_service`. Slots that cannot be
    parsed, lack an offset or are not (or no longer) offered are dropped,
    not errors; the card still shows. Raises TenantContextMissingError
    without a bound tenant.
    """
    salon = _current_salon()
    if not _arguments_match_spec(arguments):
        return INVALID_TOOL_CALL, None

    service = Service.objects.filter(pk=arguments["service_id"], is_active=True).first()
    if service is None:
        return NOT_FOUND, None

    specialist: Specialist | None = None
    if arguments["specialist_id"] != ANY_SPECIALIST:
        specialist = annotate_rating(
            Specialist.objects.filter(pk=arguments["specialist_id"], is_active=True)
        ).first()
        if specialist is None:
            return NOT_FOUND, None
        if not specialist_offers_service(specialist, service):
            return {"error": "specialist_does_not_offer_service"}, None

    offered: dict[dt.datetime, dt.datetime] = {}
    dropped: list[str] = []
    for raw_slot in arguments.get("slots", []):
        start = _parse_slot(raw_slot)
        if start is None or not is_start_offered(
            salon=salon, service=service, specialist=specialist, start=start, now=now
        ):
            dropped.append(raw_slot)
            continue
        offered.setdefault(start.astimezone(dt.UTC), start)

    salon_tz = ZoneInfo(salon.timezone)
    shown = [start.astimezone(salon_tz).isoformat() for start in sorted(offered)]
    breakdown = compute_deposit_breakdown(price=service.price, percentage=salon.deposit_percentage)
    card = {
        "service": {
            "id": service.id,
            "name": resolve_translation(service.name, lang),
            "duration_minutes": service.duration_minutes,
        },
        "specialist": (
            ANY_SPECIALIST
            if specialist is None
            else {
                "id": specialist.id,
                "name": resolve_translation(specialist.name, lang),
                "average_rating": specialist.average_rating,  # type: ignore[attr-defined]
                "review_count": specialist.review_count,  # type: ignore[attr-defined]
            }
        ),
        "price": str(service.price),
        "deposit": str(breakdown.deposit),
        "due_at_visit": str(breakdown.due_at_visit),
        "currency": salon.currency,
        "slots": shown,
    }
    return {"shown": True, "slots": shown, "dropped": dropped}, card
