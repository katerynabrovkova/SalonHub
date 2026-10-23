"""
Stage 6.C/6.D/6.E/6.F/6.H — availability engine (docs/ARCHITECTURE.md § 6;
docs/DECISIONS.md § Stage 6 decisions, § Stage 6.C decisions, § Stage 6.D
decisions, § Stage 6.E decisions, § Stage 6.F decisions, § Stage 6.H
decisions).

Functional-core / imperative-shell split: _fetch_working_hours,
_fetch_time_off, _fetch_appointments, and _fetch_qualifying_specialists
(6.H) are the only four functions that touch the tenant-scoped ORM (and so
are the only four that require tenant context to already be bound, via
core.tenancy.tenant_context — they don't bind it themselves, same
convention as every other service-layer function in this project, e.g.
specialists/services.py's _future_appointments). Everything else here is a
pure function over plain values, in particular _subtract_intervals, which
knows nothing about TimeOff/WorkingHours/Appointment at all — the
generalised blocking-interval list a future closure source would plug into
without this function changing (6.D's Appointment support is exactly that
plugging-in, not a rewrite).

6.E adds one more layer of each kind on top of compute_open_windows,
without changing it: _step_windows (pure — plain numbers for
granularity_minutes/occupied_minutes, the Window/dt.datetime vocabulary
otherwise) and compute_candidate_start_times (the thin orchestrator, which
calls compute_open_windows itself rather than receiving windows as an
argument — docs/DECISIONS.md § Stage 6.E decisions).

6.F adds booking-window filtering on top of _step_windows's output, again
without changing it: _filter_candidates_by_booking_window (pure — plain
dt.datetime bounds, no knowledge of lead time, max advance, or timezones of
its own) and _max_advance_boundary (pure — the calendar-boundary arithmetic,
taking an already-resolved ZoneInfo rather than resolving one itself).
compute_candidate_start_times gains a required `now: dt.datetime` parameter
with no default, validated on its first line (docs/DECISIONS.md § Stage 6.F
decisions).

6.H adds the "any specialist" composition on top of
compute_candidate_start_times, without changing it:
_fetch_qualifying_specialists (the fourth ORM-touching function, above),
_merge_specialist_availability (pure — combines each specialist's own
candidate times into {start_time: [specialists free then]}, sorting keys
explicitly), and compute_multi_specialist_availability (the thin
orchestrator, which calls compute_candidate_start_times once per qualifying
specialist rather than receiving their results — the same shape already
established for compute_candidate_start_times itself calling
compute_open_windows). Re-validates `now` on its own first line rather than
relying on compute_candidate_start_times's inner guard, because zero
qualifying specialists would otherwise skip that inner call entirely
(docs/DECISIONS.md § Stage 6.H decisions).
"""

import datetime as dt
from collections.abc import Iterator, Sequence
from typing import NamedTuple
from zoneinfo import ZoneInfo

from django.db.models import QuerySet
from django.utils import timezone

from booking.models import ACTIVE_APPOINTMENT_STATUSES, Appointment
from catalog.models import Service
from core.exceptions import InvalidDateRangeError, InvalidSteppingParametersError
from specialists.models import Specialist, TimeOff, WorkingHours
from tenants.models import Salon


class Window(NamedTuple):
    start: dt.datetime
    end: dt.datetime


def _dates_in_range(date_from: dt.date, date_to: dt.date) -> Iterator[dt.date]:
    current = date_from
    while current <= date_to:
        yield current
        current += dt.timedelta(days=1)


def _localize_window(date: dt.date, start_time: dt.time, end_time: dt.time, tz: ZoneInfo) -> Window:
    start = dt.datetime.combine(date, start_time, tzinfo=tz).astimezone(dt.UTC)
    end = dt.datetime.combine(date, end_time, tzinfo=tz).astimezone(dt.UTC)
    return Window(start, end)


def _merge_intervals(intervals: Sequence[Window]) -> list[Window]:
    """
    Merges overlapping AND touching intervals (one ends exactly where the
    next begins) into one — docs/DECISIONS.md § Stage 6.C decisions,
    "touching intervals merge."
    """
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda window: window.start)
    merged = [ordered[0]]
    for window in ordered[1:]:
        last = merged[-1]
        if window.start <= last.end:
            if window.end > last.end:
                merged[-1] = Window(last.start, window.end)
        else:
            merged.append(window)
    return merged


def _time_off_to_blocking_intervals(time_off_rows: Sequence[TimeOff]) -> list[Window]:
    """
    The only function that knows TimeOff specifically. A future closure
    source gets its own adapter of this shape, feeding the same
    _subtract_intervals below (docs/DECISIONS.md § Stage 6 decisions).
    """
    return [Window(row.start_datetime, row.end_datetime) for row in time_off_rows]


def _appointments_to_blocking_intervals(appointment_rows: Sequence[Appointment]) -> list[Window]:
    """
    Mirrors _time_off_to_blocking_intervals exactly. The busy interval is
    (start_datetime, blocked_until) — the same buffered interval the
    Appointment exclusion constraint itself protects (docs/ARCHITECTURE.md
    § 2, § 7), never the bare (start_datetime, end_datetime) service
    interval (docs/DECISIONS.md § Stage 6.D decisions).
    """
    return [Window(row.start_datetime, row.blocked_until) for row in appointment_rows]


def _subtract_intervals(
    windows: Sequence[Window], blocking_intervals: Sequence[Window]
) -> list[Window]:
    """
    Generalised subtraction — no knowledge of TimeOff, WorkingHours, or
    Appointment. Half-open [start, end) boundaries throughout: a blocking
    interval that only touches a window, without overlapping it, does not
    clip it. Blocking intervals are merged first so the result never
    depends on the order they're passed in, including when they overlap
    each other (docs/DECISIONS.md § Stage 6.C decisions).
    """
    if not blocking_intervals:
        return list(windows)

    merged_blockers = _merge_intervals(blocking_intervals)
    result: list[Window] = []
    for window in windows:
        remaining = [window]
        for blocker in merged_blockers:
            next_remaining: list[Window] = []
            for piece in remaining:
                if blocker.end <= piece.start or blocker.start >= piece.end:
                    next_remaining.append(piece)
                    continue
                if blocker.start > piece.start:
                    next_remaining.append(Window(piece.start, blocker.start))
                if blocker.end < piece.end:
                    next_remaining.append(Window(blocker.end, piece.end))
            remaining = next_remaining
        result.extend(remaining)
    return result


def _fetch_working_hours(specialist: Specialist) -> QuerySet[WorkingHours]:
    return WorkingHours.objects.filter(specialist=specialist)


def _fetch_time_off(
    specialist: Specialist, range_start_utc: dt.datetime, range_end_utc: dt.datetime
) -> QuerySet[TimeOff]:
    return TimeOff.objects.filter(
        specialist=specialist,
        start_datetime__lt=range_end_utc,
        end_datetime__gt=range_start_utc,
    )


def _fetch_appointments(
    specialist: Specialist, range_start_utc: dt.datetime, range_end_utc: dt.datetime
) -> QuerySet[Appointment]:
    return Appointment.objects.filter(
        specialist=specialist,
        status__in=ACTIVE_APPOINTMENT_STATUSES,
        start_datetime__lt=range_end_utc,
        blocked_until__gt=range_start_utc,
    )


def compute_open_windows(
    specialist: Specialist,
    date_from: dt.date,
    date_to: dt.date,
    salon_timezone: str,
) -> list[Window]:
    """
    A specialist's open windows over [date_from, date_to] (salon-local
    calendar dates, inclusive), as concrete UTC intervals: WorkingHours
    rows localized and merged, minus TimeOff and existing appointments
    (docs/ARCHITECTURE.md § 6 step 1; docs/DECISIONS.md § Stage 6 / §
    Stage 6.C / § Stage 6.D decisions).

    salon_timezone is resolved into a zoneinfo.ZoneInfo here, once — this
    is therefore where an unresolvable timezone name surfaces as
    zoneinfo.ZoneInfoNotFoundError, deliberately, not from a helper several
    calls deep (docs/DECISIONS.md § Stage 6.C decisions).

    Requires tenant context to already be bound (core.tenancy.tenant_context)
    — this function does not bind it itself; see the module docstring.

    Excludes duration, buffer, granularity, lead time, and the advance
    window entirely — those are later substages.
    """
    if date_from > date_to:
        raise InvalidDateRangeError(
            f"date_from ({date_from}) must not be after date_to ({date_to})."
        )

    if not specialist.is_active:
        return []

    tz = ZoneInfo(salon_timezone)

    working_hours_rows = list(_fetch_working_hours(specialist))
    raw_windows = [
        _localize_window(date, row.start_time, row.end_time, tz)
        for date in _dates_in_range(date_from, date_to)
        for row in working_hours_rows
        if row.day_of_week == date.weekday()
    ]
    open_windows = _merge_intervals(raw_windows)

    range_start_utc = _localize_window(date_from, dt.time(0, 0), dt.time(0, 0), tz).start
    range_end_utc = _localize_window(
        date_to + dt.timedelta(days=1), dt.time(0, 0), dt.time(0, 0), tz
    ).start
    time_off_rows = list(_fetch_time_off(specialist, range_start_utc, range_end_utc))
    appointment_rows = list(_fetch_appointments(specialist, range_start_utc, range_end_utc))
    blocking_intervals = _time_off_to_blocking_intervals(
        time_off_rows
    ) + _appointments_to_blocking_intervals(appointment_rows)

    return _subtract_intervals(open_windows, blocking_intervals)


def _step_windows(
    windows: Sequence[Window],
    granularity_minutes: int,
    occupied_minutes: int,
) -> list[dt.datetime]:
    """
    Steps each window into candidate start times, independently, anchored
    to that window's own start — never to midnight or to another window's
    grid (docs/DECISIONS.md § Stage 6.E decisions; § Stage 6.C decisions'
    "touching intervals merge" is why this matters: two non-touching
    windows the same day must each get their own grid). Half-open [)
    bounds: a candidate is kept while `candidate + occupied_minutes <=
    window.end`, so a candidate whose occupied span ends exactly at
    window.end is offered. Strict grid only — no candidate is added beyond
    what stepping from window.start actually lands on, even when the
    leftover tail after the last on-grid candidate would itself fit
    occupied_minutes.

    granularity_minutes and occupied_minutes non-positive raises
    InvalidSteppingParametersError rather than looping forever or silently
    returning nothing — see that exception's own docstring.
    """
    if granularity_minutes <= 0 or occupied_minutes <= 0:
        raise InvalidSteppingParametersError(
            f"granularity_minutes ({granularity_minutes}) and occupied_minutes "
            f"({occupied_minutes}) must both be positive."
        )

    step = dt.timedelta(minutes=granularity_minutes)
    occupied = dt.timedelta(minutes=occupied_minutes)
    candidates: list[dt.datetime] = []
    for window in windows:
        candidate = window.start
        while candidate + occupied <= window.end:
            candidates.append(candidate)
            candidate += step
    return candidates


def _filter_candidates_by_booking_window(
    candidates: Sequence[dt.datetime],
    lower_bound: dt.datetime,
    upper_bound: dt.datetime,
) -> list[dt.datetime]:
    """
    Keeps a candidate only when `lower_bound <= candidate < upper_bound` —
    half-open [), consistent with the rest of the engine. No knowledge of
    lead time, max advance, or timezones of its own; both bounds arrive
    already computed (docs/DECISIONS.md § Stage 6.F decisions).
    """
    return [candidate for candidate in candidates if lower_bound <= candidate < upper_bound]


def _max_advance_boundary(now: dt.datetime, max_advance_days: int, tz: ZoneInfo) -> dt.datetime:
    """
    The exclusive upper bound of the booking window: midnight at the start
    of the day following (today's salon-local date + max_advance_days), in
    the salon's timezone, converted to UTC (docs/DECISIONS.md § Stage 6.F
    decisions). An inlined two-line body, not a call to
    `_localize_window(...).start` — same reasoning recorded there: identical
    arithmetic, but this is a single cutoff instant, not a working-hours
    Window that happens to have zero width.

    Does not defend against a salon timezone whose DST transition lands
    exactly on local midnight (docs/DECISIONS.md § Open questions,
    "Max-advance boundary computed as local midnight can land on a
    non-existent local time") — deliberately unresolved; see that entry.
    """
    boundary_date = now.astimezone(tz).date() + dt.timedelta(days=max_advance_days + 1)
    return dt.datetime.combine(boundary_date, dt.time(0, 0), tzinfo=tz).astimezone(dt.UTC)


def compute_candidate_start_times(
    specialist: Specialist,
    service: Service,
    salon: Salon,
    date_from: dt.date,
    date_to: dt.date,
    now: dt.datetime,
) -> list[dt.datetime]:
    """
    A specialist's candidate start times for a service, over
    [date_from, date_to], filtered to the salon's booking window
    (docs/ARCHITECTURE.md § 6 step 3; docs/DECISIONS.md § Stage 6.E
    decisions, § Stage 6.F decisions). Calls compute_open_windows itself
    rather than receiving windows as an argument, then resolves
    occupied_minutes from service.duration_minutes + service.buffer_minutes
    and granularity_minutes from salon.slot_granularity_minutes — two
    different models — before calling _step_windows. `salon` is taken as an
    explicit argument rather than derived from specialist.salon
    specifically so this function issues no queries of its own beyond the
    three compute_open_windows already does (docs/DECISIONS.md § Stage 6.D
    decisions' django_assert_num_queries(3); a lazy specialist.salon access
    would have added a fourth — salon.min_lead_time_hours,
    salon.max_advance_days, and salon.timezone are all plain columns on the
    already-loaded row, so booking-window filtering adds none either). Not
    checked against specialist.salon — see docs/DECISIONS.md § Open
    questions.

    `now` has no default and is validated first: a naive value raises
    ValueError immediately, before compute_open_windows or anything else
    runs (docs/DECISIONS.md § Stage 6.F decisions).

    Requires tenant context to already be bound (core.tenancy.tenant_context),
    same as compute_open_windows — this function does not bind it itself.

    Excludes conversion to salon-local time — that is a later substage.
    """
    if timezone.is_naive(now):
        raise ValueError("now must be a timezone-aware datetime.")

    windows = compute_open_windows(
        specialist=specialist,
        date_from=date_from,
        date_to=date_to,
        salon_timezone=salon.timezone,
    )
    occupied_minutes = service.duration_minutes + service.buffer_minutes
    granularity_minutes = salon.slot_granularity_minutes
    candidates = _step_windows(
        windows, granularity_minutes=granularity_minutes, occupied_minutes=occupied_minutes
    )

    lower_bound = now + dt.timedelta(hours=salon.min_lead_time_hours)
    tz = ZoneInfo(salon.timezone)
    upper_bound = _max_advance_boundary(now, salon.max_advance_days, tz)
    return _filter_candidates_by_booking_window(candidates, lower_bound, upper_bound)


def _fetch_qualifying_specialists(service: Service) -> QuerySet[Specialist]:
    """
    Specialists linked to `service` via SpecialistService and currently
    active. No `.distinct()`: SpecialistService's
    UniqueConstraint(specialist, service) already rules out more than one
    join row per specialist for a single service, so this filter can't
    produce duplicate Specialist rows to begin with (docs/DECISIONS.md §
    Stage 6.H decisions).
    """
    return Specialist.objects.filter(services=service, is_active=True)


def _merge_specialist_availability(
    per_specialist: Sequence[tuple[Specialist, Sequence[dt.datetime]]],
) -> dict[dt.datetime, list[Specialist]]:
    """
    Pure — combines each specialist's own candidate times into
    {start_time: [specialists free then]}. Keys are sorted explicitly via
    dict(sorted(...)): plain dicts preserve insertion order but do not sort
    themselves (docs/DECISIONS.md § Stage 6.H decisions).
    """
    mapping: dict[dt.datetime, list[Specialist]] = {}
    for specialist, times in per_specialist:
        for start in times:
            mapping.setdefault(start, []).append(specialist)
    return dict(sorted(mapping.items()))


def compute_multi_specialist_availability(
    service: Service,
    salon: Salon,
    date_from: dt.date,
    date_to: dt.date,
    now: dt.datetime,
) -> dict[dt.datetime, list[Specialist]]:
    """
    The "any specialist" composition (docs/ARCHITECTURE.md § 6; § Stage 6
    decisions): calls compute_candidate_start_times once per specialist
    qualified for `service` (linked via SpecialistService, is_active) and
    unions the results into {start_time: [specialists free then]}. Consumed
    by both the time-grid and the chosen-time lookup endpoints — recomputed
    on every call, never persisted between them (docs/DECISIONS.md § Stage
    6.G decisions, § Stage 6.H decisions).

    `now` is validated here, on the first line, rather than relying on the
    inner compute_candidate_start_times guard: with zero qualifying
    specialists the inner function is never called at all, so a naive `now`
    would otherwise silently produce {} instead of raising
    (docs/DECISIONS.md § Stage 6.H decisions).

    Requires tenant context to already be bound (core.tenancy.tenant_context),
    same as compute_open_windows and compute_candidate_start_times — this
    function does not bind it itself.
    """
    if timezone.is_naive(now):
        raise ValueError("now must be a timezone-aware datetime.")

    qualifying_specialists = list(_fetch_qualifying_specialists(service))
    per_specialist = [
        (
            specialist,
            compute_candidate_start_times(
                specialist=specialist,
                service=service,
                salon=salon,
                date_from=date_from,
                date_to=date_to,
                now=now,
            ),
        )
        for specialist in qualifying_specialists
    ]
    return _merge_specialist_availability(per_specialist)


def is_start_offered(
    *,
    salon: Salon,
    service: Service,
    specialist: Specialist | None,
    start: dt.datetime,
    now: dt.datetime,
) -> bool:
    """
    Whether `start` is offered for `service` with `specialist`, or with any
    qualifying specialist when `specialist` is None (docs/DECISIONS.md §
    Stage 16 planning, "Sub-step 6 details", 6a).

    The engine is asked for the salon-local date of `start` only, the same
    single-day call booking makes. Requires tenant context to already be
    bound, like the engine functions it calls.
    """
    if timezone.is_naive(start):
        raise ValueError("start must be a timezone-aware datetime.")

    local_date = start.astimezone(ZoneInfo(salon.timezone)).date()
    if specialist is None:
        availability = compute_multi_specialist_availability(
            service=service, salon=salon, date_from=local_date, date_to=local_date, now=now
        )
        return bool(availability.get(start))
    candidates = compute_candidate_start_times(
        specialist=specialist,
        service=service,
        salon=salon,
        date_from=local_date,
        date_to=local_date,
        now=now,
    )
    return start in candidates
