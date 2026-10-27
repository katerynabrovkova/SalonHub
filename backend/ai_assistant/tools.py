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
availability only from scheduling.services. Sub-step 10c (docs/DECISIONS.md
§ "Sub-step 10c: `check_availability` explains empty results"): it returns
at most 3 slots per day, as far apart as possible, for the first 3 days
with any; an empty result adds the reason and alternatives found by
relaxing the customer's soft wishes.

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
    OUTSIDE_HOURS,
    compute_candidate_start_times,
    compute_multi_specialist_availability,
    day_schedule,
    no_free_start_reason,
    start_in_window,
)
from specialists.models import Specialist, WorkingHours
from specialists.services import annotate_rating, specialist_offers_service
from tenants.models import FAQItem, Salon

NOT_FOUND: dict[str, Any] = {"error": "not_found"}

ANY_SPECIALIST = "any"
MAX_RANGE_DAYS = 14
SLOTS_PER_DAY = 3
MAX_DAYS = 3
HARD_WISHES = ("day", "time")
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


def _weekly_hours(specialist: Specialist) -> dict[str, list[dict[str, str]]]:
    """
    The specialist's weekly WorkingHours (sub-step 10d): {"monday": [{"start":
    "09:00", "end": "18:00"}], ...} in weekday order, each day's ranges by
    start; days without hours are absent. Reads the prefetched, already
    ordered rows. Time off is not reflected.
    """
    hours: dict[str, list[dict[str, str]]] = {}
    for row in specialist.working_hours.all():
        hours.setdefault(WEEKDAY_NAMES[row.day_of_week], []).append(
            {"start": row.start_time.strftime("%H:%M"), "end": row.end_time.strftime("%H:%M")}
        )
    return hours


def get_specialist_info(*, lang: str, specialist_id: int | str | None = None) -> dict[str, Any]:
    _current_salon()
    specialists = annotate_rating(
        Specialist.objects.filter(is_active=True).prefetch_related(
            Prefetch("services", queryset=Service.objects.filter(is_active=True)),
            Prefetch(
                "working_hours",
                queryset=WorkingHours.objects.order_by("day_of_week", "start_time"),
            ),
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
            "working_hours": _weekly_hours(specialist),
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
    hard: list[str] | None = None,
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
    if hard is not None and (
        not isinstance(hard, list) or not all(wish in HARD_WISHES for wish in hard)
    ):
        return {"error": "invalid_filter"}

    parsed_service_id = _parse_id(service_id)
    service = (
        Service.objects.filter(pk=parsed_service_id, is_active=True).first()
        if parsed_service_id is not None
        else None
    )
    if service is None:
        return NOT_FOUND

    # One engine call covers the requested range and the alternatives search.
    search_end = start_date + dt.timedelta(days=MAX_RANGE_DAYS - 1)
    specialist: Specialist | None = None
    if specialist_id == ANY_SPECIALIST:
        candidates = list(
            compute_multi_specialist_availability(
                service=service, salon=salon, date_from=start_date, date_to=search_end, now=now
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
            date_to=search_end,
            now=now,
        )

    salon_tz = ZoneInfo(salon.timezone)
    starts = sorted(candidate.astimezone(salon_tz) for candidate in candidates)

    def matching(
        *, date_to: dt.date, weekdays: set[int] | None, use_time: bool
    ) -> list[dt.datetime]:
        return [
            start
            for start in starts
            if start_in_window(
                start,
                tz=salon_tz,
                date_from=start_date,
                date_to=date_to,
                weekdays=weekdays,
                time_from=start_time if use_time else None,
                time_to=end_time if use_time else None,
            )
        ]

    matches = matching(date_to=end_date, weekdays=weekday_numbers, use_time=True)
    if matches:
        shown = _spread(matches)
        return {
            "slots": [start.isoformat() for start in shown],
            "has_more": len(matches) > len(shown),
        }

    reason = no_free_start_reason(
        salon=salon,
        service=service,
        specialist=specialist,
        date_from=start_date,
        date_to=end_date,
        weekdays=weekday_numbers,
        time_from=start_time,
        time_to=end_time,
        now=now,
    )

    # Relaxation steps in order (DECISIONS § "Sub-step 10c"): the same time
    # on other days, other times on the requested days, any time. A step
    # that would relax a missing or a hard wish is skipped.
    hard_wishes = set(hard or ())
    day_wish = weekday_numbers is not None or end_date < search_end
    time_wish = start_time is not None or end_time is not None
    may_move_day = day_wish and "day" not in hard_wishes
    may_move_time = time_wish and "time" not in hard_wishes
    steps: list[tuple[list[str], dt.date, set[int] | None, bool]] = []
    if may_move_day:
        steps.append((["day"], search_end, None, True))
    if may_move_time:
        steps.append((["time"], end_date, weekday_numbers, False))
    if may_move_day and may_move_time:
        steps.append((["day", "time"], search_end, None, False))

    alternatives: dict[str, Any] = {"slots": [], "relaxed": []}
    for relaxed, step_date_to, step_weekdays, use_time in steps:
        found = matching(date_to=step_date_to, weekdays=step_weekdays, use_time=use_time)
        if found:
            alternatives = {
                "slots": [start.isoformat() for start in _spread(found)],
                "relaxed": relaxed,
            }
            break

    empty: dict[str, Any] = {"slots": [], "has_more": False, "reason": reason}
    if reason == OUTSIDE_HOURS:
        empty["hours"] = _first_working_day_hours(
            salon=salon,
            service=service,
            specialist=specialist,
            date_from=start_date,
            date_to=end_date,
            weekdays=weekday_numbers,
        )
    empty["alternatives"] = alternatives
    return empty


def _clock(value: dt.time) -> str:
    return value.strftime("%H:%M")


def _first_working_day_hours(
    *,
    salon: Salon,
    service: Service,
    specialist: Specialist | None,
    date_from: dt.date,
    date_to: dt.date,
    weekdays: set[int] | None,
) -> dict[str, Any] | None:
    """For `outside_hours` (sub-step 10e): the first requested day someone
    works, with its ranges and last possible start."""
    day = date_from
    while day <= date_to:
        if weekdays is None or day.weekday() in weekdays:
            schedule = day_schedule(salon=salon, service=service, specialist=specialist, date=day)
            if schedule is not None:
                return {
                    "date": day.isoformat(),
                    "ranges": [
                        {"start": _clock(start), "end": _clock(end)}
                        for start, end in schedule.ranges
                    ],
                    "last_start": (
                        _clock(schedule.last_start) if schedule.last_start is not None else None
                    ),
                }
        day += dt.timedelta(days=1)
    return None


def _tie_rank(start: dt.datetime) -> int:
    """Whole hour first, then half hour, then anything else (salon-local)."""
    return {0: 0, 30: 1}.get(start.minute, 2)


def _farthest_apart(day_starts: list[dt.datetime]) -> list[dt.datetime]:
    """
    Up to SLOTS_PER_DAY starts of one day, as far apart as possible (the
    smallest gap maximised). The first and last are always kept, since some
    best choice always contains them; the third is the start nearest the
    middle, ties going to a whole hour, then a half hour, then the earlier.
    """
    if len(day_starts) <= SLOTS_PER_DAY:
        return day_starts
    first, last = day_starts[0], day_starts[-1]
    middle = first + (last - first) / 2
    third = min(
        day_starts[1:-1],
        key=lambda start: (abs(start - middle), _tie_rank(start), start),
    )
    return [first, third, last]


def _spread(starts: list[dt.datetime]) -> list[dt.datetime]:
    """Sorted salon-local starts → the farthest-apart starts of each of the
    first MAX_DAYS days that have any."""
    by_day: dict[dt.date, list[dt.datetime]] = {}
    for start in starts:
        by_day.setdefault(start.date(), []).append(start)
    return [
        start
        for day_starts in list(by_day.values())[:MAX_DAYS]
        for start in _farthest_apart(day_starts)
    ]


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
                "performs, average rating, review count, and weekly working hours "
                "per weekday (the regular schedule; days off and bookings are not in "
                "it). Without specialist_id it returns all of the salon's active "
                "specialists: the list is complete. Pass specialist_id to get one "
                "specialist."
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
                "(time_from inclusive, time_to exclusive). Returns up to 3 times per "
                "day, spread out, for the first 3 days with any, in salon-local ISO "
                "8601, and has_more when more free times exist. When nothing "
                "matches, it also returns the reason (day_off, outside_hours, "
                "fully_booked or outside_booking_window; with outside_hours also hours: "
                "that day's working ranges and the last possible start for this "
                "service) and alternatives: the nearest free times "
                "within 14 days of date_from, with relaxed naming the wishes that "
                "were dropped (day, time). Wishes listed in hard are never dropped."
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
                    "hard": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(HARD_WISHES)},
                        "description": (
                            'Wishes the customer insists on ("only"): "day" keeps the '
                            'requested days, "time" keeps the time-of-day window, when '
                            "looking for alternatives. Omit for soft wishes."
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
