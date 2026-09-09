"""
Scheduling app's availability-engine test suite (docs/ARCHITECTURE.md § 6;
docs/DECISIONS.md § Stage 6 decisions and its Stage 6.C/6.D/6.E addenda).
Scoped to the availability engine as a whole, not to open-window computation
specifically — open windows (§ 6 steps 1-2) are the only computation this
file covers so far, but the filename and this docstring are deliberately not
scoped narrower than that, so later substages' tests (e.g. 6.E's slot
stepping) land in this same file rather than reopening where tests belong.

Written against the agreed design before any implementation exists — this
file is expected to fail on collection (ModuleNotFoundError) until
scheduling/services.py is written. Two layers, matching the functional-core
/ imperative-shell split from the design proposal:

- The _merge_intervals / _subtract_intervals / _localize_window tests below
  exercise the pure functions directly, with plain Window/datetime literals
  — no database, no tenant context. These are leading-underscore "internal"
  functions; testing them directly is deliberate for a module whose whole
  point is being a heavily-unit-tested pure computation core
  (docs/ARCHITECTURE.md § 6), not a convention break — same precedent as
  specialists/services.py's _future_appointments.
- The compute_open_windows tests exercise the orchestrator end-to-end
  against real WorkingHours/TimeOff/Appointment rows, via
  make_working_hours/make_time_off/make_appointment (tests/conftest.py).
  Several of these exist specifically to prove the pure functions above are
  actually wired into the orchestrator, not just correct in isolation — 6.D
  extends this same section (compute_open_windows's signature is unchanged;
  only its body gained a second blocking source, docs/DECISIONS.md § Stage
  6.D decisions), rather than adding a new file or a new entry point.
- 6.E adds one of each layer on top: the _step_windows tests exercise the
  new pure primitive directly, same treatment as the pure functions above;
  the compute_candidate_start_times tests exercise the new orchestrator
  end-to-end, layered on compute_open_windows exactly as docs/DECISIONS.md
  § Stage 6.E decisions specifies. This file is expected to fail on
  collection again until scheduling/services.py gains these two names and
  core/exceptions.py gains InvalidSteppingParametersError — the same
  ModuleNotFoundError/ImportError shape as before 6.C/6.D existed.
- 6.F adds two more pure functions alongside the ones above —
  _filter_candidates_by_booking_window (plain dt.datetime bounds, no
  knowledge of lead time, max advance, or timezones of its own) and
  _max_advance_boundary (the calendar-boundary arithmetic, taking an
  already-resolved ZoneInfo, docs/DECISIONS.md § Stage 6.F decisions) — and
  extends compute_candidate_start_times with a required `now: dt.datetime`
  parameter that has no default and raises ValueError immediately if it's
  naive. This file is expected to fail on collection again until
  scheduling/services.py gains the two new names and
  compute_candidate_start_times gains the `now` parameter — the same
  ModuleNotFoundError/ImportError shape as before 6.C/6.D/6.E existed.
"""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from accounts.models import Customer
from booking.models import ACTIVE_APPOINTMENT_STATUSES, AppointmentStatus
from catalog.models import Service, ServiceCategory
from core.exceptions import InvalidDateRangeError, InvalidSteppingParametersError
from core.tenancy import tenant_context
from scheduling.services import (
    Window,
    _filter_candidates_by_booking_window,
    _localize_window,
    _max_advance_boundary,
    _merge_intervals,
    _merge_specialist_availability,
    _step_windows,
    _subtract_intervals,
    compute_candidate_start_times,
    compute_multi_specialist_availability,
    compute_open_windows,
)
from specialists.models import Specialist, SpecialistService
from tests.conftest import make_appointment, make_time_off, make_working_hours

UTC = dt.UTC
DAY = dt.date(2026, 8, 17)


def _t(hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(DAY.year, DAY.month, DAY.day, hour, minute, tzinfo=UTC)


def _localized_midnight(salon, date: dt.date) -> dt.datetime:
    """
    Local helper, used once (the full-day TimeOff fixture below) — not
    promoted to conftest.py since it's not part of the shared
    factory-helper set the way make_working_hours/make_time_off are.
    """
    return dt.datetime(date.year, date.month, date.day, tzinfo=ZoneInfo(salon.timezone)).astimezone(
        UTC
    )


def _make_specialist(*, salon, name: str, is_active: bool = True) -> Specialist:
    """
    Local helper, used only by the compute_multi_specialist_availability
    tests (6.H) — not promoted to conftest.py's `specialist` fixture, which
    is a single fixed specialist ("Jane"), not a factory for several.
    """
    with tenant_context(salon.id):
        return Specialist.objects.create(salon=salon, name={"en": name}, is_active=is_active)


def _link_specialist_service(*, salon, specialist: Specialist, service: Service) -> None:
    with tenant_context(salon.id):
        SpecialistService.objects.create(salon=salon, specialist=specialist, service=service)


WINDOW = Window(_t(9), _t(18))


# --- _merge_intervals --------------------------------------------------


def test_merge_intervals_empty_input_returns_empty():
    assert _merge_intervals([]) == []


def test_merge_intervals_single_interval_returns_unchanged():
    assert _merge_intervals([WINDOW]) == [WINDOW]


def test_merge_intervals_no_overlap_stays_separate():
    a, b = Window(_t(9), _t(12)), Window(_t(14), _t(18))
    assert _merge_intervals([a, b]) == [a, b]


def test_merge_intervals_overlapping_pair_merges():
    a, b = Window(_t(9), _t(14)), Window(_t(12), _t(18))
    assert _merge_intervals([a, b]) == [WINDOW]


def test_merge_intervals_three_overlapping_merge_into_one():
    rows = [Window(_t(9), _t(12)), Window(_t(11), _t(15)), Window(_t(14), _t(18))]
    assert _merge_intervals(rows) == [WINDOW]


def test_merge_intervals_touching_with_zero_gap_merges():
    """Case 21 — pins decision 5 (touching WorkingHours rows merge)."""
    a, b = Window(_t(9), _t(12, 50)), Window(_t(12, 50), _t(18))
    assert _merge_intervals([a, b]) == [WINDOW]


# --- _subtract_intervals -------------------------------------------------


def test_subtract_intervals_no_blockers_returns_windows_unchanged():
    assert _subtract_intervals([WINDOW], []) == [WINDOW]


def test_subtract_intervals_blocker_clips_start():
    blocker = Window(_t(7), _t(10))
    assert _subtract_intervals([WINDOW], [blocker]) == [Window(_t(10), _t(18))]


def test_subtract_intervals_blocker_clips_end():
    blocker = Window(_t(17), _t(20))
    assert _subtract_intervals([WINDOW], [blocker]) == [Window(_t(9), _t(17))]


def test_subtract_intervals_blocker_splits_window_in_two():
    blocker = Window(_t(12), _t(13))
    assert _subtract_intervals([WINDOW], [blocker]) == [
        Window(_t(9), _t(12)),
        Window(_t(13), _t(18)),
    ]


def test_subtract_intervals_blocker_covers_window_entirely_returns_empty():
    blocker = Window(_t(8), _t(19))
    assert _subtract_intervals([WINDOW], [blocker]) == []


def test_subtract_intervals_blocker_exactly_matches_window_returns_empty():
    blocker = Window(_t(9), _t(18))
    assert _subtract_intervals([WINDOW], [blocker]) == []


def test_subtract_intervals_blocker_touching_start_boundary_does_not_clip():
    """[) convention: blocker ending exactly at window start doesn't overlap."""
    blocker = Window(_t(7), _t(9))
    assert _subtract_intervals([WINDOW], [blocker]) == [WINDOW]


def test_subtract_intervals_blocker_touching_end_boundary_does_not_clip():
    """[) convention: blocker starting exactly at window end doesn't overlap."""
    blocker = Window(_t(18), _t(20))
    assert _subtract_intervals([WINDOW], [blocker]) == [WINDOW]


def test_subtract_intervals_blocker_outside_window_leaves_window_unchanged():
    blocker = Window(_t(19), _t(20))
    assert _subtract_intervals([WINDOW], [blocker]) == [WINDOW]


@pytest.mark.parametrize("blocker_order", [0, 1])
def test_subtract_intervals_overlapping_blockers_are_order_independent(blocker_order):
    """Case 15. Blockers 10-14 and 12-16 overlap each other; result must not
    depend on which order they're passed in."""
    blockers = [Window(_t(10), _t(14)), Window(_t(12), _t(16))]
    if blocker_order:
        blockers = list(reversed(blockers))
    assert _subtract_intervals([WINDOW], blockers) == [
        Window(_t(9), _t(10)),
        Window(_t(16), _t(18)),
    ]


def test_subtract_intervals_multiple_windows_each_handled_independently():
    other_day_window = Window(_t(9) + dt.timedelta(days=1), _t(18) + dt.timedelta(days=1))
    blocker = Window(_t(12), _t(13))  # only overlaps WINDOW, not other_day_window
    result = _subtract_intervals([WINDOW, other_day_window], [blocker])
    assert result == [Window(_t(9), _t(12)), Window(_t(13), _t(18)), other_day_window]


# --- _localize_window ----------------------------------------------------


def test_localize_window_ordinary_day_produces_correct_utc_offset():
    """January — no DST in effect, Europe/Kyiv is a flat UTC+2."""
    start, end = _localize_window(
        date=dt.date(2026, 1, 15),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
        tz=ZoneInfo("Europe/Kyiv"),
    )
    assert (start, end) == (
        dt.datetime(2026, 1, 15, 7, 0, tzinfo=UTC),
        dt.datetime(2026, 1, 15, 16, 0, tzinfo=UTC),
    )


def test_localize_window_non_whole_hour_offset():
    """Case 20 — confirms the arithmetic doesn't assume :00-minute offsets."""
    start, end = _localize_window(
        date=dt.date(2026, 6, 1),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
        tz=ZoneInfo("Asia/Kathmandu"),  # UTC+5:45
    )
    assert (start, end) == (
        dt.datetime(2026, 6, 1, 3, 15, tzinfo=UTC),
        dt.datetime(2026, 6, 1, 12, 15, tzinfo=UTC),
    )


def test_localize_window_spring_forward_calendar_day_is_23_hours_in_utc():
    """
    Case 12 (spring-forward half). 2026-03-29 is the last Sunday of March —
    the EU/Europe-Kyiv spring-forward date. Spans the whole calendar day by
    comparing local midnight to the next local midnight, so the assertion
    doesn't depend on knowing the transition's exact local clock hour.
    """
    tz = ZoneInfo("Europe/Kyiv")
    midnight, _ = _localize_window(dt.date(2026, 3, 29), dt.time(0, 0), dt.time(0, 0), tz)
    next_midnight, _ = _localize_window(dt.date(2026, 3, 30), dt.time(0, 0), dt.time(0, 0), tz)
    assert next_midnight - midnight == dt.timedelta(hours=23)


def test_localize_window_fall_back_calendar_day_is_25_hours_in_utc():
    """Case 12 (fall-back half). 2026-10-25 is the last Sunday of October."""
    tz = ZoneInfo("Europe/Kyiv")
    midnight, _ = _localize_window(dt.date(2026, 10, 25), dt.time(0, 0), dt.time(0, 0), tz)
    next_midnight, _ = _localize_window(dt.date(2026, 10, 26), dt.time(0, 0), dt.time(0, 0), tz)
    assert next_midnight - midnight == dt.timedelta(hours=25)


def test_localize_window_offset_recomputed_fresh_per_date_spring_forward():
    """
    Guards against the specific bug named in docs/DECISIONS.md's DST
    decision: resolving the UTC offset once and reusing it across dates,
    instead of resolving it fresh per date. Same local 09:00, one calendar
    day apart, straddling the transition: the UTC gap between them is 23h,
    not the naive 24h — the 1-hour deviation is the whole point of the
    assertion.
    """
    tz = ZoneInfo("Europe/Kyiv")
    before, _ = _localize_window(dt.date(2026, 3, 28), dt.time(9, 0), dt.time(18, 0), tz)
    after, _ = _localize_window(dt.date(2026, 3, 29), dt.time(9, 0), dt.time(18, 0), tz)
    assert after - before == dt.timedelta(hours=23)


def test_localize_window_offset_recomputed_fresh_per_date_fall_back():
    """
    Same guard, fall-back direction. Same local 09:00, one calendar day
    apart, straddling the transition: the UTC gap is 25h, not the naive
    24h — again, the 1-hour deviation is the whole point.
    """
    tz = ZoneInfo("Europe/Kyiv")
    before, _ = _localize_window(dt.date(2026, 10, 24), dt.time(9, 0), dt.time(18, 0), tz)
    after, _ = _localize_window(dt.date(2026, 10, 25), dt.time(9, 0), dt.time(18, 0), tz)
    assert after - before == dt.timedelta(hours=25)


# --- _step_windows (6.E) ---------------------------------------------------


def test_step_windows_empty_window_list_returns_empty():
    assert _step_windows([], granularity_minutes=20, occupied_minutes=60) == []


def test_step_windows_window_shorter_than_occupied_minutes_yields_nothing():
    window = Window(_t(9), _t(9, 30))  # 30 minutes wide
    assert _step_windows([window], granularity_minutes=20, occupied_minutes=60) == []


def test_step_windows_window_exactly_equal_to_occupied_minutes_yields_one_candidate_at_start():
    window = Window(_t(9), _t(10))  # 60 minutes wide, exactly occupied_minutes
    assert _step_windows([window], granularity_minutes=20, occupied_minutes=60) == [_t(9)]


def test_step_windows_candidate_ending_exactly_on_window_end_is_kept():
    """
    Decision 4 (half-open [) bounds): the boundary check is `candidate +
    occupied_minutes <= window.end`, not `<`. The second candidate (09:20)
    occupies exactly through 10:00, window.end itself, and must still be
    offered.
    """
    window = Window(_t(9), _t(10))
    result = _step_windows([window], granularity_minutes=20, occupied_minutes=40)
    assert result == [_t(9), _t(9, 20)]


def test_step_windows_tail_shorter_than_one_step_is_dropped_even_if_occupied_minutes_would_fit():
    """
    Decision 1 (strict grid only, § Stage 6.E decisions): window is 09:00-
    09:50. After the last on-grid candidate (09:30, occupying through
    09:40), 10 minutes remain before window end — exactly occupied_minutes
    (10), so a slot at 09:40 would physically fit, but 09:40 is off the
    30-minute grid anchored at 09:00 and must not be offered.
    """
    window = Window(_t(9), _t(9, 50))
    result = _step_windows([window], granularity_minutes=30, occupied_minutes=10)
    assert result == [_t(9), _t(9, 30)]


def test_step_windows_occupied_minutes_not_a_multiple_of_granularity_steps_correctly():
    """
    occupied_minutes=75 mirrors the real numbers a service with
    duration_minutes=60 and buffer_minutes=15 produces (the `service`
    fixture below) — deliberately not a multiple of granularity_minutes=20.
    """
    window = Window(_t(9), _t(12))  # 180 minutes wide
    result = _step_windows([window], granularity_minutes=20, occupied_minutes=75)
    assert result == [_t(9) + dt.timedelta(minutes=20 * k) for k in range(6)]  # 09:00..10:40


def test_step_windows_granularity_larger_than_whole_window_yields_only_window_start():
    window = Window(_t(9), _t(9, 30))  # 30 minutes wide
    result = _step_windows([window], granularity_minutes=60, occupied_minutes=10)
    assert result == [_t(9)]


def test_step_windows_multiple_windows_on_different_days_stepped_independently():
    window_day1 = Window(_t(9), _t(10))
    window_day2 = Window(_t(9) + dt.timedelta(days=1), _t(10) + dt.timedelta(days=1))
    result = _step_windows([window_day1, window_day2], granularity_minutes=30, occupied_minutes=20)
    assert result == [
        _t(9),
        _t(9, 30),
        _t(9) + dt.timedelta(days=1),
        _t(9, 30) + dt.timedelta(days=1),
    ]


def test_step_windows_one_too_short_window_contributes_nothing_but_does_not_break_the_rest():
    too_short = Window(_t(9), _t(9, 10))  # 10 minutes wide, can't fit occupied_minutes=20
    fits = Window(_t(11), _t(12))  # 60 minutes wide
    result = _step_windows([too_short, fits], granularity_minutes=20, occupied_minutes=20)
    assert result == [_t(11), _t(11, 20), _t(11, 40)]


def test_step_windows_second_window_anchors_to_its_own_start_not_the_first_windows_grid():
    """
    Sharpens 'windows on different days, stepped independently' into a
    same-day case that isolates the specific bug that case can't catch: two
    non-touching windows the same day, 09:00-12:00 and 12:50-18:00 (a
    50-minute gap — decision 5 of § Stage 6.C decisions is exactly why
    these stay two windows instead of merging). At granularity 20, the
    second window's candidates must start at 12:50, 13:10, ... — its own
    grid, anchored to its own start. If the grid were anchored to the day
    (midnight) or to the first window's start (09:00) instead, the next
    09:00-anchored 20-minute line at or after 12:50 is 13:00, not 12:50,
    and this assertion fails outright.
    """
    first = Window(_t(9), _t(12))
    second = Window(_t(12, 50), _t(18))
    result = _step_windows([first, second], granularity_minutes=20, occupied_minutes=20)
    assert result[:9] == [_t(9) + dt.timedelta(minutes=20 * k) for k in range(9)]
    assert result[9] == _t(12, 50)
    assert result[9:] == [_t(12, 50) + dt.timedelta(minutes=20 * k) for k in range(15)]


def test_step_windows_spring_forward_window_produces_23_hourly_candidates_not_24():
    """
    Pins UTC-only stepping arithmetic (docs/DECISIONS.md § Stage 6.E
    decisions, "grid remainder"/DST discussion): by the time a window
    reaches _step_windows it's already a concrete UTC interval, built via
    _localize_window exactly as the spring-forward tests above prove (23
    real UTC hours on 2026-03-29, Europe/Kyiv). Stepping that window in
    constant 60-minute UTC steps must therefore produce 23 candidates, one
    per real UTC hour — never the naive 24 a local-wall-clock-minutes
    implementation would produce.
    """
    tz = ZoneInfo("Europe/Kyiv")
    midnight, _ = _localize_window(dt.date(2026, 3, 29), dt.time(0, 0), dt.time(0, 0), tz)
    next_midnight, _ = _localize_window(dt.date(2026, 3, 30), dt.time(0, 0), dt.time(0, 0), tz)
    window = Window(midnight, next_midnight)
    result = _step_windows([window], granularity_minutes=60, occupied_minutes=60)
    assert result == [midnight + dt.timedelta(hours=k) for k in range(23)]


def test_step_windows_fall_back_window_produces_25_hourly_candidates_not_24():
    """Fall-back counterpart — 25 real UTC hours on 2026-10-25, Europe/Kyiv."""
    tz = ZoneInfo("Europe/Kyiv")
    midnight, _ = _localize_window(dt.date(2026, 10, 25), dt.time(0, 0), dt.time(0, 0), tz)
    next_midnight, _ = _localize_window(dt.date(2026, 10, 26), dt.time(0, 0), dt.time(0, 0), tz)
    window = Window(midnight, next_midnight)
    result = _step_windows([window], granularity_minutes=60, occupied_minutes=60)
    assert result == [midnight + dt.timedelta(hours=k) for k in range(25)]


@pytest.mark.parametrize("granularity_minutes", [0, -5])
def test_step_windows_non_positive_granularity_raises(granularity_minutes):
    with pytest.raises(InvalidSteppingParametersError):
        _step_windows([WINDOW], granularity_minutes=granularity_minutes, occupied_minutes=30)


@pytest.mark.parametrize("occupied_minutes", [0, -5])
def test_step_windows_non_positive_occupied_minutes_raises(occupied_minutes):
    with pytest.raises(InvalidSteppingParametersError):
        _step_windows([WINDOW], granularity_minutes=20, occupied_minutes=occupied_minutes)


def test_step_windows_both_non_positive_at_once_still_raises_a_single_error():
    """Neither check may assume the other field is well-formed first."""
    with pytest.raises(InvalidSteppingParametersError):
        _step_windows([WINDOW], granularity_minutes=0, occupied_minutes=-5)


# --- _filter_candidates_by_booking_window (6.F) ----------------------------


def test_filter_candidates_by_booking_window_empty_input_returns_empty():
    assert _filter_candidates_by_booking_window([], _t(9), _t(18)) == []


def test_filter_candidates_by_booking_window_candidate_before_lower_bound_excluded():
    assert _filter_candidates_by_booking_window([_t(8, 59)], _t(9), _t(18)) == []


def test_filter_candidates_by_booking_window_candidate_at_lower_bound_included():
    """Decision 2 (§ Stage 6.F decisions): `candidate >= lower_bound` — inclusive."""
    assert _filter_candidates_by_booking_window([_t(9)], _t(9), _t(18)) == [_t(9)]


def test_filter_candidates_by_booking_window_candidate_at_upper_bound_excluded():
    """
    Decision 3 (§ Stage 6.F decisions): `candidate < upper_bound` — exclusive,
    half-open [), consistent with the rest of the engine.
    """
    assert _filter_candidates_by_booking_window([_t(18)], _t(9), _t(18)) == []


def test_filter_candidates_by_booking_window_candidate_just_before_upper_bound_included():
    assert _filter_candidates_by_booking_window([_t(17, 59)], _t(9), _t(18)) == [_t(17, 59)]


def test_filter_candidates_by_booking_window_candidate_after_upper_bound_excluded():
    assert _filter_candidates_by_booking_window([_t(19)], _t(9), _t(18)) == []


def test_filter_candidates_by_booking_window_mixed_list_preserves_surviving_order():
    candidates = [_t(8), _t(9), _t(10), _t(17, 59), _t(18), _t(19)]
    result = _filter_candidates_by_booking_window(candidates, _t(9), _t(18))
    assert result == [_t(9), _t(10), _t(17, 59)]


# --- _max_advance_boundary (6.F) --------------------------------------------


def test_max_advance_boundary_ordinary_flat_offset_day():
    """January — no DST in effect, Europe/Kyiv is a flat UTC+2 (mirrors
    test_localize_window_ordinary_day_produces_correct_utc_offset)."""
    now = dt.datetime(2026, 1, 15, 10, 0, tzinfo=UTC)  # local Kyiv 12:00, Jan 15
    boundary = _max_advance_boundary(now, max_advance_days=5, tz=ZoneInfo("Europe/Kyiv"))
    assert boundary == dt.datetime(2026, 1, 20, 22, 0, tzinfo=UTC)  # local midnight Jan 21, -2h


def test_max_advance_boundary_zero_days_is_local_midnight_tomorrow():
    now = dt.datetime(2026, 8, 17, 10, 0, tzinfo=UTC)  # local Kyiv 13:00 (EEST +3), Aug 17
    boundary = _max_advance_boundary(now, max_advance_days=0, tz=ZoneInfo("Europe/Kyiv"))
    assert boundary == dt.datetime(2026, 8, 17, 21, 0, tzinfo=UTC)  # local midnight Aug 18, -3h


def test_max_advance_boundary_uses_salon_local_date_not_utc_date():
    """
    now is late enough in UTC that Kyiv's local date (UTC+3 in August) has
    already rolled over to the next day — proves the function converts to
    local time before taking .date(), not now.date() directly. A `now.date()`
    implementation would compute boundary_date one day earlier than this,
    landing on 2026-08-17 21:00 UTC instead — the same result as the previous
    test, so this would silently pass with 2026-08-17's setup but not this
    one's.
    """
    now = dt.datetime(2026, 8, 17, 23, 0, tzinfo=UTC)  # local Kyiv Aug 18, 02:00
    boundary = _max_advance_boundary(now, max_advance_days=0, tz=ZoneInfo("Europe/Kyiv"))
    assert boundary == dt.datetime(2026, 8, 18, 21, 0, tzinfo=UTC)  # local midnight Aug 19, -3h


def test_max_advance_boundary_pins_current_behavior_on_nonexistent_local_midnight():
    """
    Pins CURRENT behaviour only — does not assert what it ought to do. §
    Open questions ("Max-advance boundary computed as local midnight can land
    on a non-existent local time") is recorded as deliberately unresolved;
    this test exists so a future fix to that open question is a visible,
    deliberate change to this assertion, not a silent one.

    2026-09-06 is the day America/Santiago's local midnight does not exist
    (spring-forward gap; verified against real tzdata via zoneinfo directly).
    The inlined dt.datetime.combine(...).astimezone(UTC) body (§ Stage 6.F
    decisions) defaults to fold=0, which resolves the gap using the
    PRE-transition offset (-04:00) rather than raising — the same UTC instant
    an ordinary, non-transition day would produce.
    """
    tz = ZoneInfo("America/Santiago")
    now = dt.datetime(2026, 9, 5, 12, 0, tzinfo=UTC)  # local Santiago Sep 5, pre-transition
    boundary = _max_advance_boundary(now, max_advance_days=0, tz=tz)
    assert boundary == dt.datetime(2026, 9, 6, 4, 0, tzinfo=UTC)


# --- _merge_specialist_availability (6.H) ------------------------------------


def test_merge_specialist_availability_empty_input_returns_empty():
    assert _merge_specialist_availability([]) == {}


def test_merge_specialist_availability_sorts_keys_and_unions_overlap():
    """
    Mixed input: A and B each contribute a distinct time, plus one time
    (_t(10)) both are free at. Per-specialist lists are passed in
    deliberately unsorted (_t(11) before _t(9) for A) to prove the ascending
    order comes from the function sorting, not from input order or
    insertion-order luck — plain dicts preserve insertion order but do not
    sort themselves (docs/DECISIONS.md § Stage 6.H decisions).
    """
    a = Specialist(name={"en": "A"})
    b = Specialist(name={"en": "B"})
    pairs = [
        (a, [_t(11), _t(9), _t(10)]),
        (b, [_t(10)]),
    ]
    result = _merge_specialist_availability(pairs)
    assert list(result.keys()) == [_t(9), _t(10), _t(11)]
    assert result == {
        _t(9): [a],
        _t(10): [a, b],
        _t(11): [a],
    }


# --- compute_open_windows (integration, DB-backed) ------------------------


def test_compute_open_windows_single_shift(salon, specialist):
    """Case 1."""
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )
    with tenant_context(salon.id):
        result = compute_open_windows(
            specialist=specialist,
            date_from=monday,
            date_to=monday,
            salon_timezone=salon.timezone,
        )
    assert len(result) == 1


def test_compute_open_windows_split_shift_with_lunch_gap(salon, specialist):
    """Case 2."""
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(13, 0),
    )
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(14, 0),
        end_time=dt.time(18, 0),
    )
    with tenant_context(salon.id):
        result = compute_open_windows(
            specialist=specialist,
            date_from=monday,
            date_to=monday,
            salon_timezone=salon.timezone,
        )
    assert len(result) == 2  # lunch gap preserved, not merged


def test_compute_open_windows_merges_overlapping_working_hours_rows(salon, specialist):
    """
    Proves _merge_intervals is actually wired into compute_open_windows, not
    just correct in isolation (test_merge_intervals_overlapping_pair_merges
    above never calls the orchestrator at all). Europe/Kyiv is EEST (+3) in
    August, so 09:00-18:00 local is 06:00-15:00 UTC.
    """
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(14, 0),
    )
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(12, 0),
        end_time=dt.time(18, 0),
    )
    with tenant_context(salon.id):
        result = compute_open_windows(
            specialist=specialist,
            date_from=monday,
            date_to=monday,
            salon_timezone=salon.timezone,
        )
    assert result == [
        Window(
            dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC),
            dt.datetime(2026, 8, 17, 15, 0, tzinfo=UTC),
        )
    ]


def test_compute_open_windows_time_off_partially_overlapping_shift_splits_window(salon, specialist):
    """
    Proves the TimeOff-to-blocking-interval adapter and _subtract_intervals
    are actually wired into compute_open_windows — case 11 below only
    covers a full-day TimeOff, which wouldn't catch a bug in how a
    partial-day, already-UTC TimeOff interval meets a freshly localized
    window. 12:00-13:00 salon-local (Europe/Kyiv, EEST +3 in August) is
    09:00-10:00 UTC.
    """
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )
    make_time_off(
        salon=salon,
        specialist=specialist,
        start_datetime=dt.datetime(2026, 8, 17, 9, 0, tzinfo=UTC),
        end_datetime=dt.datetime(2026, 8, 17, 10, 0, tzinfo=UTC),
    )
    with tenant_context(salon.id):
        result = compute_open_windows(
            specialist=specialist,
            date_from=monday,
            date_to=monday,
            salon_timezone=salon.timezone,
        )
    assert result == [
        Window(
            dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC),
            dt.datetime(2026, 8, 17, 9, 0, tzinfo=UTC),
        ),
        Window(
            dt.datetime(2026, 8, 17, 10, 0, tzinfo=UTC),
            dt.datetime(2026, 8, 17, 15, 0, tzinfo=UTC),
        ),
    ]


def test_compute_open_windows_day_with_no_working_hours_returns_empty(salon, specialist):
    """Case 9."""
    monday = dt.date(2026, 8, 17)
    with tenant_context(salon.id):
        result = compute_open_windows(
            specialist=specialist,
            date_from=monday,
            date_to=monday,
            salon_timezone=salon.timezone,
        )
    assert result == []


def test_compute_open_windows_range_spanning_several_days(salon, specialist):
    """Case 10. Monday has hours, Tuesday doesn't, Wednesday has hours."""
    monday = dt.date(2026, 8, 17)
    for offset in (0, 2):  # Monday, Wednesday
        day = monday + dt.timedelta(days=offset)
        make_working_hours(
            salon=salon,
            specialist=specialist,
            day_of_week=day.weekday(),
            start_time=dt.time(9, 0),
            end_time=dt.time(18, 0),
        )
    with tenant_context(salon.id):
        result = compute_open_windows(
            specialist=specialist,
            date_from=monday,
            date_to=monday + dt.timedelta(days=2),
            salon_timezone=salon.timezone,
        )
    assert len(result) == 2  # Monday + Wednesday, nothing for Tuesday


def test_compute_open_windows_time_off_on_different_weekday_does_not_affect_other_days(
    salon, specialist
):
    """Case 11."""
    monday = dt.date(2026, 8, 17)
    tuesday = monday + dt.timedelta(days=1)
    for day in (monday, tuesday):
        make_working_hours(
            salon=salon,
            specialist=specialist,
            day_of_week=day.weekday(),
            start_time=dt.time(9, 0),
            end_time=dt.time(18, 0),
        )
    # TimeOff covers all of Monday only.
    make_time_off(
        salon=salon,
        specialist=specialist,
        start_datetime=_localized_midnight(salon, monday),
        end_datetime=_localized_midnight(salon, tuesday),
    )
    with tenant_context(salon.id):
        result = compute_open_windows(
            specialist=specialist,
            date_from=monday,
            date_to=tuesday,
            salon_timezone=salon.timezone,
        )
    assert len(result) == 1  # Tuesday's window survives untouched


def test_compute_open_windows_single_day_range(salon, specialist):
    """Case 17."""
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )
    with tenant_context(salon.id):
        result = compute_open_windows(
            specialist=specialist,
            date_from=monday,
            date_to=monday,
            salon_timezone=salon.timezone,
        )
    assert len(result) == 1


def test_compute_open_windows_date_from_after_date_to_raises(salon, specialist):
    """Case 18."""
    with tenant_context(salon.id), pytest.raises(InvalidDateRangeError):
        compute_open_windows(
            specialist=specialist,
            date_from=dt.date(2026, 8, 20),
            date_to=dt.date(2026, 8, 15),
            salon_timezone=salon.timezone,
        )


def test_compute_open_windows_working_hours_weekday_absent_from_range_produces_no_window(
    salon, specialist
):
    """Case 19. WorkingHours exists for Sunday; range is Mon-Wed only."""
    monday = dt.date(2026, 8, 17)
    sunday = monday + dt.timedelta(days=6)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=sunday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )
    with tenant_context(salon.id):
        result = compute_open_windows(
            specialist=specialist,
            date_from=monday,
            date_to=monday + dt.timedelta(days=2),
            salon_timezone=salon.timezone,
        )
    assert result == []


def test_compute_open_windows_inactive_specialist_returns_empty_even_with_working_hours(
    salon, specialist
):
    """
    Case 6 (compute_open_windows half only — the caller-side filter half of
    the is_active guard belongs to the composition function's own future
    tests, not here).
    """
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )
    with tenant_context(salon.id):
        specialist.is_active = False
        specialist.save(update_fields=["is_active"])
        result = compute_open_windows(
            specialist=specialist,
            date_from=monday,
            date_to=monday,
            salon_timezone=salon.timezone,
        )
    assert result == []


def test_compute_open_windows_spring_forward_and_fall_back(salon, specialist):
    """
    Case 12, at the orchestrator level, both transition directions. Pure
    _localize_window tests above can't catch a bug where compute_open_windows
    itself resolves the offset once and reuses it across the loop over
    dates — only a real multi-day call through the orchestrator can. Saturday
    (2026-03-28/2026-10-24) and Sunday (2026-03-29/2026-10-25) share the same
    day_of_week values in both March and October, so one pair of WorkingHours
    rows covers both ranges.
    """
    for day_of_week in (5, 6):  # Saturday, Sunday
        make_working_hours(
            salon=salon,
            specialist=specialist,
            day_of_week=day_of_week,
            start_time=dt.time(9, 0),
            end_time=dt.time(18, 0),
        )

    with tenant_context(salon.id):
        spring = compute_open_windows(
            specialist=specialist,
            date_from=dt.date(2026, 3, 28),
            date_to=dt.date(2026, 3, 29),
            salon_timezone=salon.timezone,
        )
        fall = compute_open_windows(
            specialist=specialist,
            date_from=dt.date(2026, 10, 24),
            date_to=dt.date(2026, 10, 25),
            salon_timezone=salon.timezone,
        )

    spring_starts = sorted(w.start for w in spring)
    assert spring_starts[1] - spring_starts[0] == dt.timedelta(hours=23)

    fall_starts = sorted(w.start for w in fall)
    assert fall_starts[1] - fall_starts[0] == dt.timedelta(hours=25)


# --- compute_open_windows + Appointments (6.D) ------------------------


def test_compute_open_windows_no_appointments_matches_pre_appointment_behavior(salon, specialist):
    """Case 1 — appointments integrated, but zero exist; result is identical
    to 6.C's own single-shift behavior."""
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )
    with tenant_context(salon.id):
        result = compute_open_windows(
            specialist=specialist,
            date_from=monday,
            date_to=monday,
            salon_timezone=salon.timezone,
        )
    assert result == [
        Window(
            dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC), dt.datetime(2026, 8, 17, 15, 0, tzinfo=UTC)
        )
    ]


def test_compute_open_windows_appointment_splits_window(salon, customer, specialist, service):
    """Case 2."""
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=dt.datetime(2026, 8, 17, 9, 0, tzinfo=UTC),  # local 12:00
    )
    with tenant_context(salon.id):
        result = compute_open_windows(
            specialist=specialist,
            date_from=monday,
            date_to=monday,
            salon_timezone=salon.timezone,
        )
    assert result == [
        Window(
            dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC), dt.datetime(2026, 8, 17, 9, 0, tzinfo=UTC)
        ),
        Window(
            dt.datetime(2026, 8, 17, 10, 15, tzinfo=UTC),
            dt.datetime(2026, 8, 17, 15, 0, tzinfo=UTC),
        ),
    ]


def test_compute_open_windows_appointment_blocked_until_extends_past_window_end(
    salon, customer, specialist, service
):
    """Case 3."""
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=dt.datetime(2026, 8, 17, 14, 30, tzinfo=UTC),  # local 17:30; blocked_until 15:45 UTC
    )
    with tenant_context(salon.id):
        result = compute_open_windows(
            specialist=specialist,
            date_from=monday,
            date_to=monday,
            salon_timezone=salon.timezone,
        )
    assert result == [
        Window(
            dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC), dt.datetime(2026, 8, 17, 14, 30, tzinfo=UTC)
        )
    ]


def test_compute_open_windows_back_to_back_appointments_merge_into_one_gap(
    salon, customer, specialist, service
):
    """Case 4."""
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )
    first = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=dt.datetime(2026, 8, 17, 9, 0, tzinfo=UTC),
    )
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=first.blocked_until,
    )
    with tenant_context(salon.id):
        result = compute_open_windows(
            specialist=specialist,
            date_from=monday,
            date_to=monday,
            salon_timezone=salon.timezone,
        )
    assert result == [
        Window(
            dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC), dt.datetime(2026, 8, 17, 9, 0, tzinfo=UTC)
        ),
        Window(
            dt.datetime(2026, 8, 17, 11, 30, tzinfo=UTC),
            dt.datetime(2026, 8, 17, 15, 0, tzinfo=UTC),
        ),
    ]


def test_compute_open_windows_window_after_appointment_starts_exactly_at_blocked_until(
    salon, customer, specialist, service
):
    """Case 5 — half-open boundary convention, through a real
    appointment-derived blocker rather than hand-built Window literals."""
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=dt.datetime(2026, 8, 17, 9, 0, tzinfo=UTC),
    )
    with tenant_context(salon.id):
        result = compute_open_windows(
            specialist=specialist,
            date_from=monday,
            date_to=monday,
            salon_timezone=salon.timezone,
        )
    assert result[1].start == appointment.blocked_until


@pytest.mark.parametrize("status", ACTIVE_APPOINTMENT_STATUSES)
def test_compute_open_windows_active_status_appointment_blocks(
    salon, customer, specialist, service, status
):
    """
    Mirror image of the terminal-status test below: an implementation using
    status=AppointmentStatus.CONFIRMED instead of
    status__in=ACTIVE_APPOINTMENT_STATUSES would pass every other test in
    this file, since make_appointment's own default is CONFIRMED —
    PENDING_PAYMENT would never be exercised as a blocking status otherwise.
    """
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=dt.datetime(2026, 8, 17, 9, 0, tzinfo=UTC),
        status=status,
    )
    with tenant_context(salon.id):
        result = compute_open_windows(
            specialist=specialist,
            date_from=monday,
            date_to=monday,
            salon_timezone=salon.timezone,
        )
    assert result == [
        Window(
            dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC), dt.datetime(2026, 8, 17, 9, 0, tzinfo=UTC)
        ),
        Window(
            dt.datetime(2026, 8, 17, 10, 15, tzinfo=UTC),
            dt.datetime(2026, 8, 17, 15, 0, tzinfo=UTC),
        ),
    ]


@pytest.mark.parametrize(
    "status",
    [
        AppointmentStatus.CANCELLED,
        AppointmentStatus.EXPIRED,
        AppointmentStatus.COMPLETED,
        AppointmentStatus.NO_SHOW,
    ],
)
def test_compute_open_windows_terminal_status_appointment_does_not_block(
    salon, customer, specialist, service, status
):
    """Case 6."""
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=dt.datetime(2026, 8, 17, 9, 0, tzinfo=UTC),
        status=status,
    )
    with tenant_context(salon.id):
        result = compute_open_windows(
            specialist=specialist,
            date_from=monday,
            date_to=monday,
            salon_timezone=salon.timezone,
        )
    assert result == [
        Window(
            dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC), dt.datetime(2026, 8, 17, 15, 0, tzinfo=UTC)
        )
    ]


def test_compute_open_windows_appointment_on_a_different_day_does_not_affect_other_days(
    salon, customer, specialist, service
):
    """Case 7."""
    monday = dt.date(2026, 8, 17)
    tuesday = monday + dt.timedelta(days=1)
    for day in (monday, tuesday):
        make_working_hours(
            salon=salon,
            specialist=specialist,
            day_of_week=day.weekday(),
            start_time=dt.time(9, 0),
            end_time=dt.time(18, 0),
        )
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=dt.datetime(2026, 8, 17, 9, 0, tzinfo=UTC),  # Monday only
    )
    with tenant_context(salon.id):
        result = compute_open_windows(
            specialist=specialist,
            date_from=monday,
            date_to=tuesday,
            salon_timezone=salon.timezone,
        )
    assert result == [
        Window(
            dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC), dt.datetime(2026, 8, 17, 9, 0, tzinfo=UTC)
        ),
        Window(
            dt.datetime(2026, 8, 17, 10, 15, tzinfo=UTC),
            dt.datetime(2026, 8, 17, 15, 0, tzinfo=UTC),
        ),
        Window(
            dt.datetime(2026, 8, 18, 6, 0, tzinfo=UTC), dt.datetime(2026, 8, 18, 15, 0, tzinfo=UTC)
        ),
    ]


def test_compute_open_windows_appointment_for_a_different_specialist_does_not_block(
    salon, customer, specialist, service
):
    """Case 8."""
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )
    with tenant_context(salon.id):
        other_specialist = Specialist.objects.create(salon=salon, name={"en": "Other Specialist"})
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=other_specialist,
        service=service,
        start=dt.datetime(2026, 8, 17, 9, 0, tzinfo=UTC),
    )
    with tenant_context(salon.id):
        result = compute_open_windows(
            specialist=specialist,
            date_from=monday,
            date_to=monday,
            salon_timezone=salon.timezone,
        )
    assert result == [
        Window(
            dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC), dt.datetime(2026, 8, 17, 15, 0, tzinfo=UTC)
        )
    ]


def test_compute_open_windows_appointment_starting_before_range_still_clips(
    salon, customer, specialist, service
):
    """
    Case 9. An early-morning shift (00:00-03:00 local) puts the range
    boundary (local midnight) inside the window, so an appointment that
    started the salon-local day before but extends into the shift must
    still be fetched and still clip. A start_datetime__gte=range_start_utc
    filter, instead of the correct overlap condition, would exclude it and
    this test would see the whole window unclipped.
    """
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(0, 0),
        end_time=dt.time(3, 0),
    )
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=dt.datetime(2026, 8, 16, 20, 30, tzinfo=UTC),  # local Aug 16 23:30
    )
    with tenant_context(salon.id):
        result = compute_open_windows(
            specialist=specialist,
            date_from=monday,
            date_to=monday,
            salon_timezone=salon.timezone,
        )
    assert result == [
        Window(
            dt.datetime(2026, 8, 16, 21, 45, tzinfo=UTC), dt.datetime(2026, 8, 17, 0, 0, tzinfo=UTC)
        )
    ]


def test_compute_open_windows_appointment_ending_after_range_still_clips(
    salon, customer, specialist, service
):
    """
    Case 10. A late-night shift (22:00-23:30 local) so an appointment whose
    blocked_until extends past the range's own end still clips. The correct
    query has no upper bound on blocked_until at all — a version that added
    one (e.g. blocked_until__lte=range_end_utc) would incorrectly exclude
    this appointment.
    """
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(22, 0),
        end_time=dt.time(23, 30),
    )
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=dt.datetime(2026, 8, 17, 20, 0, tzinfo=UTC),  # local 23:00
    )
    with tenant_context(salon.id):
        result = compute_open_windows(
            specialist=specialist,
            date_from=monday,
            date_to=monday,
            salon_timezone=salon.timezone,
        )
    assert result == [
        Window(
            dt.datetime(2026, 8, 17, 19, 0, tzinfo=UTC), dt.datetime(2026, 8, 17, 20, 0, tzinfo=UTC)
        )
    ]


def test_compute_open_windows_time_off_and_appointment_overlapping_each_other_compose(
    salon, customer, specialist, service
):
    """Case 11 — a TimeOff and an Appointment overlapping EACH OTHER (not
    just the window), proving the two adapters' output is concatenated and
    merged as one list before subtraction, not applied as two independent
    passes."""
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )
    make_time_off(
        salon=salon,
        specialist=specialist,
        start_datetime=dt.datetime(2026, 8, 17, 9, 0, tzinfo=UTC),
        end_datetime=dt.datetime(2026, 8, 17, 10, 0, tzinfo=UTC),
    )
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=dt.datetime(2026, 8, 17, 9, 45, tzinfo=UTC),
    )
    with tenant_context(salon.id):
        result = compute_open_windows(
            specialist=specialist,
            date_from=monday,
            date_to=monday,
            salon_timezone=salon.timezone,
        )
    assert result == [
        Window(
            dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC), dt.datetime(2026, 8, 17, 9, 0, tzinfo=UTC)
        ),
        Window(
            dt.datetime(2026, 8, 17, 11, 0, tzinfo=UTC), dt.datetime(2026, 8, 17, 15, 0, tzinfo=UTC)
        ),
    ]


def test_compute_open_windows_appointment_exactly_matching_window_returns_no_windows(
    salon, customer, specialist, service
):
    """Case 12. WorkingHours sized to exactly match the service's own
    duration+buffer block (75 minutes)."""
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(12, 0),
        end_time=dt.time(13, 15),
    )
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=dt.datetime(2026, 8, 17, 9, 0, tzinfo=UTC),  # local 12:00
    )
    with tenant_context(salon.id):
        result = compute_open_windows(
            specialist=specialist,
            date_from=monday,
            date_to=monday,
            salon_timezone=salon.timezone,
        )
    assert result == []


def test_compute_open_windows_ignores_appointment_from_a_different_salon(
    salon, other_salon, specialist
):
    """
    Case 13. A cross-salon appointment does not leak into this salon's
    computed windows.

    This does NOT isolate tenant scoping specifically: _fetch_appointments
    filters by a specific specialist OBJECT, and Specialist uses one global
    auto-incrementing id across every salon, so no two Specialist rows
    anywhere can ever share a pk — `specialist=specialist` alone already
    makes cross-salon leakage structurally impossible here, regardless of
    Appointment.objects vs Appointment.unscoped_objects. The composite
    tenant FK on Appointment (core/db.py's composite_tenant_fk) additionally
    guarantees Appointment.salon_id always equals
    Appointment.specialist.salon_id, so the two managers can never disagree
    on a query already filtered to one specialist. other_salon's specialist
    is still named "Jane" — same as this file's own specialist fixture — so
    a reader can see no accidental name coincidence is doing any work
    either. This is a regression/documentation safety net for the
    guarantee, not a test that would fail under a manager swap.
    """
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )
    with tenant_context(other_salon.id):
        other_category = ServiceCategory.objects.create(salon=other_salon, name={"en": "Nails"})
        other_service = Service.objects.create(
            salon=other_salon,
            category=other_category,
            name={"en": "Manicure"},
            duration_minutes=60,
            price="500.00",
            buffer_minutes=15,
        )
        other_specialist = Specialist.objects.create(salon=other_salon, name={"en": "Jane"})
        other_customer = Customer.objects.create(
            salon=other_salon, name="Bob", email="bob@example.com", phone="+10000000001"
        )
    make_appointment(
        salon=other_salon,
        customer=other_customer,
        specialist=other_specialist,
        service=other_service,
        start=dt.datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
    )
    with tenant_context(salon.id):
        result = compute_open_windows(
            specialist=specialist,
            date_from=monday,
            date_to=monday,
            salon_timezone=salon.timezone,
        )
    assert result == [
        Window(
            dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC), dt.datetime(2026, 8, 17, 15, 0, tzinfo=UTC)
        )
    ]


def test_compute_open_windows_issues_exactly_three_queries(
    django_assert_num_queries, salon, customer, specialist, service
):
    """Case 14. docs/DECISIONS.md § Stage 6.D decisions: pinned because this
    project has already lost query efficiency to an unpinned regression once
    (a missing select_related in Stage 4)."""
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )
    make_time_off(
        salon=salon,
        specialist=specialist,
        start_datetime=dt.datetime(2026, 8, 17, 9, 0, tzinfo=UTC),
        end_datetime=dt.datetime(2026, 8, 17, 10, 0, tzinfo=UTC),
    )
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=dt.datetime(2026, 8, 17, 11, 0, tzinfo=UTC),
    )
    with tenant_context(salon.id):
        with django_assert_num_queries(3):
            compute_open_windows(
                specialist=specialist,
                date_from=monday,
                date_to=monday,
                salon_timezone=salon.timezone,
            )


# --- compute_candidate_start_times (6.E) -----------------------------------


def test_compute_candidate_start_times_resolves_occupied_from_service_and_granularity_from_salon(
    salon, specialist, service
):
    """
    salon.slot_granularity_minutes defaults to 15, the same number as
    service.buffer_minutes (conftest.py's `service` fixture: duration=60,
    buffer=15) — a granularity misread from service.buffer_minutes instead
    of salon.slot_granularity_minutes would produce an identical candidate
    list at the default and pass regardless. Overriding
    salon.slot_granularity_minutes to 20 here makes all three inputs
    (granularity=20, duration=60, buffer=15, occupied_minutes=75) mutually
    distinct, so this test discriminates the specific misreads it exists to
    catch: with the 06:00-08:00 UTC window below,
      - correct (granularity from salon=20, occupied from service=75):
        06:00, 06:20, 06:40 — 3 candidates.
      - granularity misread from service.buffer_minutes (15) instead of
        salon.slot_granularity_minutes (20): would give 4 candidates.
      - granularity misread from service.duration_minutes (60): would give
        1 candidate.
      - occupied_minutes misread as salon.slot_granularity_minutes alone
        (20) instead of service.duration_minutes + buffer_minutes (75):
        would give 6 candidates.
    """
    salon.slot_granularity_minutes = 20
    salon.save(update_fields=["slot_granularity_minutes"])
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(11, 0),  # 120 minutes local; Aug in Europe/Kyiv is UTC+3
    )
    now = dt.datetime(2026, 8, 17, 0, 0, tzinfo=UTC)  # well before every candidate below
    with tenant_context(salon.id):
        result = compute_candidate_start_times(
            specialist=specialist,
            service=service,
            salon=salon,
            date_from=monday,
            date_to=monday,
            now=now,
        )
    start = dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC)
    assert result == [start + dt.timedelta(minutes=20 * k) for k in range(3)]  # 06:00, 06:20, 06:40


def test_compute_candidate_start_times_actually_calls_compute_open_windows(
    salon, specialist, service
):
    """
    Proves real delegation, not a reimplementation that only reads
    WorkingHours: a TimeOff carved out of the middle of the shift must
    change the candidate list from what raw WorkingHours alone would
    produce. Without the TimeOff, 06:00-10:00 UTC (240 minutes) would give
    12 candidates at granularity 15; with it splitting the window into a
    60-minute piece (too short for occupied_minutes=75) and a 150-minute
    piece, only 6 survive.
    """
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(13, 0),  # 240 minutes local -> 06:00-10:00 UTC
    )
    make_time_off(
        salon=salon,
        specialist=specialist,
        start_datetime=dt.datetime(2026, 8, 17, 7, 0, tzinfo=UTC),
        end_datetime=dt.datetime(2026, 8, 17, 7, 30, tzinfo=UTC),
    )
    now = dt.datetime(2026, 8, 17, 0, 0, tzinfo=UTC)  # well before every candidate below
    with tenant_context(salon.id):
        result = compute_candidate_start_times(
            specialist=specialist,
            service=service,
            salon=salon,
            date_from=monday,
            date_to=monday,
            now=now,
        )
    start = dt.datetime(2026, 8, 17, 7, 30, tzinfo=UTC)
    assert result == [start + dt.timedelta(minutes=15 * k) for k in range(6)]  # 07:30..08:45


def test_compute_candidate_start_times_issues_exactly_three_queries(
    django_assert_num_queries, salon, customer, specialist, service
):
    """
    Mirrors test_compute_open_windows_issues_exactly_three_queries (§ Stage
    6.D decisions). Proves the explicit `salon` argument (§ Stage 6.E
    decisions) didn't add a fourth query the way a lazy specialist.salon
    access would have — compute_candidate_start_times itself issues no
    queries of its own beyond the three compute_open_windows already does.
    """
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )
    make_time_off(
        salon=salon,
        specialist=specialist,
        start_datetime=dt.datetime(2026, 8, 17, 9, 0, tzinfo=UTC),
        end_datetime=dt.datetime(2026, 8, 17, 10, 0, tzinfo=UTC),
    )
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=dt.datetime(2026, 8, 17, 11, 0, tzinfo=UTC),
    )
    now = dt.datetime(2026, 8, 17, 0, 0, tzinfo=UTC)
    with tenant_context(salon.id):
        with django_assert_num_queries(3):
            compute_candidate_start_times(
                specialist=specialist,
                service=service,
                salon=salon,
                date_from=monday,
                date_to=monday,
                now=now,
            )


def test_compute_candidate_start_times_specialist_with_no_working_hours_returns_empty(
    salon, specialist, service
):
    monday = dt.date(2026, 8, 17)
    now = dt.datetime(2026, 8, 17, 0, 0, tzinfo=UTC)
    with tenant_context(salon.id):
        result = compute_candidate_start_times(
            specialist=specialist,
            service=service,
            salon=salon,
            date_from=monday,
            date_to=monday,
            now=now,
        )
    assert result == []


def test_compute_candidate_start_times_non_positive_salon_granularity_raises(
    salon, specialist, service
):
    salon.slot_granularity_minutes = 0
    salon.save(update_fields=["slot_granularity_minutes"])
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )
    now = dt.datetime(2026, 8, 17, 0, 0, tzinfo=UTC)
    with tenant_context(salon.id), pytest.raises(InvalidSteppingParametersError):
        compute_candidate_start_times(
            specialist=specialist,
            service=service,
            salon=salon,
            date_from=monday,
            date_to=monday,
            now=now,
        )


def test_compute_candidate_start_times_date_from_after_date_to_raises_invalid_date_range(
    salon, specialist, service
):
    """Proves compute_open_windows's own validation isn't bypassed or
    swallowed by the new orchestrator layer."""
    with tenant_context(salon.id), pytest.raises(InvalidDateRangeError):
        compute_candidate_start_times(
            specialist=specialist,
            service=service,
            salon=salon,
            date_from=dt.date(2026, 8, 20),
            date_to=dt.date(2026, 8, 15),
            now=dt.datetime(2026, 8, 17, 0, 0, tzinfo=UTC),
        )


def test_compute_candidate_start_times_inactive_specialist_returns_empty(
    salon, specialist, service
):
    """
    Asserts the is_active=True baseline's full candidate list first, against
    the exact same fixtures the is_active=False assertion below uses, so a
    future change that silently starts returning [] regardless of is_active
    (e.g. a duration/granularity/booking-window regression) can't make this
    test pass vacuously — it must actually distinguish the two states.
    """
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )
    now = dt.datetime(2026, 8, 17, 0, 0, tzinfo=UTC)

    with tenant_context(salon.id):
        active_result = compute_candidate_start_times(
            specialist=specialist,
            service=service,
            salon=salon,
            date_from=monday,
            date_to=monday,
            now=now,
        )
    start = dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC)
    assert active_result == [start + dt.timedelta(minutes=15 * k) for k in range(32)]

    with tenant_context(salon.id):
        specialist.is_active = False
        specialist.save(update_fields=["is_active"])
        inactive_result = compute_candidate_start_times(
            specialist=specialist,
            service=service,
            salon=salon,
            date_from=monday,
            date_to=monday,
            now=now,
        )
    assert inactive_result == []


def test_compute_candidate_start_times_occupied_minutes_larger_than_every_window_returns_empty(
    salon, specialist, service
):
    """
    Integration-level counterpart of the pure _step_windows case, confirming
    docs/ARCHITECTURE.md § 6's existing edge case ("a service duration
    longer than any single open window never produces a slot — correct
    behavior, not a bug") holds through the whole orchestrator, not only the
    primitive: no exception, just an empty list.
    """
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(9, 30),  # 30 minutes local, shorter than occupied_minutes=75
    )
    now = dt.datetime(2026, 8, 17, 0, 0, tzinfo=UTC)
    with tenant_context(salon.id):
        result = compute_candidate_start_times(
            specialist=specialist,
            service=service,
            salon=salon,
            date_from=monday,
            date_to=monday,
            now=now,
        )
    assert result == []


def test_compute_candidate_start_times_full_ordering_across_multiple_days(
    salon, specialist, service
):
    """
    Explicit ordering contract, not an incidental one. Nothing pins today
    that the returned list is sorted ascending — it happens to be, because
    _merge_intervals sorts three layers down inside compute_open_windows,
    but that's an implementation detail of window-building, not a
    documented contract of compute_candidate_start_times. Monday and
    Wednesday each get an identical shift (Tuesday has none), and this
    asserts the full, exact, day-spanning candidate list in ascending
    chronological order — a future change to how blocking intervals or
    windows get composed that scrambled cross-window order would fail this
    test even if every individual window's own candidates stayed correct.
    """
    monday = dt.date(2026, 8, 17)
    wednesday = monday + dt.timedelta(days=2)
    for day in (monday, wednesday):
        make_working_hours(
            salon=salon,
            specialist=specialist,
            day_of_week=day.weekday(),
            start_time=dt.time(9, 0),
            end_time=dt.time(12, 0),  # 180 minutes local -> 06:00-09:00 UTC
        )
    now = dt.datetime(2026, 8, 17, 0, 0, tzinfo=UTC)
    with tenant_context(salon.id):
        result = compute_candidate_start_times(
            specialist=specialist,
            service=service,
            salon=salon,
            date_from=monday,
            date_to=wednesday,
            now=now,
        )
    monday_start = dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC)
    wednesday_start = dt.datetime(2026, 8, 19, 6, 0, tzinfo=UTC)
    expected = [monday_start + dt.timedelta(minutes=15 * k) for k in range(8)] + [
        wednesday_start + dt.timedelta(minutes=15 * k) for k in range(8)
    ]
    assert result == expected


# --- compute_candidate_start_times + booking-window filtering (6.F) --------


def test_compute_candidate_start_times_filters_out_candidates_before_lead_time(
    salon, specialist, service
):
    """
    Proves salon.min_lead_time_hours is actually read and applied, not
    ignored — window is 06:00-10:00 UTC (240 min local 09:00-13:00), which at
    granularity 15 / occupied 75 gives 12 raw candidates (06:00..08:45,
    mirrors the 6.E delegation test's own count). now is pinned to window
    start with a 2-hour lead time, so only candidates from 08:00 on survive.
    """
    salon.min_lead_time_hours = 2
    salon.save(update_fields=["min_lead_time_hours"])
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(13, 0),
    )
    now = dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC)  # window start
    with tenant_context(salon.id):
        result = compute_candidate_start_times(
            specialist=specialist,
            service=service,
            salon=salon,
            date_from=monday,
            date_to=monday,
            now=now,
        )
    start = dt.datetime(2026, 8, 17, 8, 0, tzinfo=UTC)
    assert result == [start + dt.timedelta(minutes=15 * k) for k in range(4)]  # 08:00..08:45


def test_compute_candidate_start_times_filters_out_candidates_beyond_max_advance(
    salon, specialist, service
):
    """
    Proves salon.max_advance_days (together with salon.timezone) is actually
    read and applied. Monday and Wednesday each get an identical shift
    (06:00-09:00 UTC, 8 candidates apiece — same setup as the full-ordering
    test above). max_advance_days=1 with `now` early Monday puts the boundary
    at local midnight Kyiv Aug 19 (UTC Aug 18 21:00) — after every Monday
    candidate but before every Wednesday one, so only Monday survives.
    """
    salon.max_advance_days = 1
    salon.save(update_fields=["max_advance_days"])
    monday = dt.date(2026, 8, 17)
    wednesday = monday + dt.timedelta(days=2)
    for day in (monday, wednesday):
        make_working_hours(
            salon=salon,
            specialist=specialist,
            day_of_week=day.weekday(),
            start_time=dt.time(9, 0),
            end_time=dt.time(12, 0),  # 06:00-09:00 UTC
        )
    now = dt.datetime(2026, 8, 17, 0, 0, tzinfo=UTC)
    with tenant_context(salon.id):
        result = compute_candidate_start_times(
            specialist=specialist,
            service=service,
            salon=salon,
            date_from=monday,
            date_to=wednesday,
            now=now,
        )
    start = dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC)
    assert result == [start + dt.timedelta(minutes=15 * k) for k in range(8)]  # Monday only


def test_compute_candidate_start_times_zero_lead_time_keeps_candidate_at_now(
    salon, specialist, service
):
    """min_lead_time_hours=0 means lower_bound == now exactly; the candidate
    sitting exactly at now must still be kept (`candidate >= now + 0h`)."""
    salon.min_lead_time_hours = 0
    salon.save(update_fields=["min_lead_time_hours"])
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(13, 0),
    )
    now = dt.datetime(2026, 8, 17, 6, 15, tzinfo=UTC)  # the second of the 12 raw candidates
    with tenant_context(salon.id):
        result = compute_candidate_start_times(
            specialist=specialist,
            service=service,
            salon=salon,
            date_from=monday,
            date_to=monday,
            now=now,
        )
    start = dt.datetime(2026, 8, 17, 6, 15, tzinfo=UTC)
    assert result == [start + dt.timedelta(minutes=15 * k) for k in range(11)]  # 06:15..08:45


def test_compute_candidate_start_times_zero_max_advance_still_allows_today(
    salon, specialist, service
):
    """
    max_advance_days=0 must not collapse to an empty bookable range — it
    means "through the end of today" (boundary is local midnight tomorrow),
    so every one of today's 12 raw candidates survives untouched.
    """
    salon.max_advance_days = 0
    salon.save(update_fields=["max_advance_days"])
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(13, 0),
    )
    now = dt.datetime(2026, 8, 17, 0, 0, tzinfo=UTC)
    with tenant_context(salon.id):
        result = compute_candidate_start_times(
            specialist=specialist,
            service=service,
            salon=salon,
            date_from=monday,
            date_to=monday,
            now=now,
        )
    start = dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC)
    assert result == [start + dt.timedelta(minutes=15 * k) for k in range(12)]


def test_compute_candidate_start_times_lead_time_swallows_entire_range_returns_empty(
    salon, specialist, service
):
    """
    Mirrors the existing "occupied_minutes larger than every window" and
    docs/ARCHITECTURE.md § 6 edge-case convention: nothing available is not
    an error condition. min_lead_time_hours=240 (10 days) pushes lower_bound
    ten days past every candidate in range.
    """
    salon.min_lead_time_hours = 240
    salon.save(update_fields=["min_lead_time_hours"])
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(13, 0),
    )
    now = dt.datetime(2026, 8, 17, 0, 0, tzinfo=UTC)
    with tenant_context(salon.id):
        result = compute_candidate_start_times(
            specialist=specialist,
            service=service,
            salon=salon,
            date_from=monday,
            date_to=monday,
            now=now,
        )
    assert result == []


def test_compute_candidate_start_times_now_after_requested_range_returns_empty(
    salon, specialist, service
):
    """
    A stale/already-elapsed range: `now` falls a full day after the
    requested range, with salon's DEFAULT lead time and max advance
    untouched — distinct from the lead-time-swallows test above, which
    reaches the same empty result via an overridden, unusually long lead
    time rather than a stale range. Both routes through the same comparison
    are worth covering explicitly.
    """
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(13, 0),
    )
    now = dt.datetime(2026, 8, 18, 0, 0, tzinfo=UTC)  # a day after the requested range
    with tenant_context(salon.id):
        result = compute_candidate_start_times(
            specialist=specialist,
            service=service,
            salon=salon,
            date_from=monday,
            date_to=monday,
            now=now,
        )
    assert result == []


def test_compute_candidate_start_times_now_well_before_range_all_candidates_survive(
    salon, specialist, service
):
    """
    The ordinary case — querying a future range today. `now` is more than
    two weeks before the requested range, well inside the default 60-day
    max-advance window and far past the default 3-hour lead time, so
    filtering has no effect: proves `now` isn't accidentally coupled to
    date_from/date_to, only to the two booking-window boundaries.
    """
    monday = dt.date(2026, 8, 17)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(13, 0),
    )
    now = dt.datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    with tenant_context(salon.id):
        result = compute_candidate_start_times(
            specialist=specialist,
            service=service,
            salon=salon,
            date_from=monday,
            date_to=monday,
            now=now,
        )
    start = dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC)
    assert result == [start + dt.timedelta(minutes=15 * k) for k in range(12)]


def test_compute_candidate_start_times_naive_now_raises_value_error(salon, specialist, service):
    """
    § Stage 6.F decisions: naive `now` is rejected on the first line, before
    compute_open_windows or anything else runs — no WorkingHours needed for
    this test to be meaningful.
    """
    monday = dt.date(2026, 8, 17)
    with tenant_context(salon.id), pytest.raises(ValueError):
        compute_candidate_start_times(
            specialist=specialist,
            service=service,
            salon=salon,
            date_from=monday,
            date_to=monday,
            now=dt.datetime(2026, 8, 17, 6, 0),  # naive — no tzinfo
        )


# --- compute_multi_specialist_availability (6.H) -----------------------------


def test_compute_multi_specialist_availability_overlapping_time_maps_to_both(salon, service):
    """
    Two qualifying specialists share identical WorkingHours, so every
    candidate time is one both are free at. Proves the union actually
    unions — a single time key with both specialists in its value, not two
    entries for the same instant and not first-writer-wins silently
    dropping the second specialist (§ Stage 6.H decisions).
    """
    monday = dt.date(2026, 8, 17)
    ann = _make_specialist(salon=salon, name="Ann")
    bob = _make_specialist(salon=salon, name="Bob")
    for person in (ann, bob):
        _link_specialist_service(salon=salon, specialist=person, service=service)
        make_working_hours(
            salon=salon,
            specialist=person,
            day_of_week=monday.weekday(),
            start_time=dt.time(9, 0),
            end_time=dt.time(11, 0),
        )
    now = dt.datetime(2026, 8, 17, 0, 0, tzinfo=UTC)
    with tenant_context(salon.id):
        result = compute_multi_specialist_availability(
            service=service,
            salon=salon,
            date_from=monday,
            date_to=monday,
            now=now,
        )
    assert result == {
        _t(6): [ann, bob],
        _t(6, 15): [ann, bob],
        _t(6, 30): [ann, bob],
        _t(6, 45): [ann, bob],
    }


def test_compute_multi_specialist_availability_disjoint_times_no_shared_key(salon, service):
    """Ann and Bob work non-overlapping windows — no time key ever maps to
    both, each key maps to exactly the one specialist who is actually free
    then."""
    monday = dt.date(2026, 8, 17)
    ann = _make_specialist(salon=salon, name="Ann")
    bob = _make_specialist(salon=salon, name="Bob")
    _link_specialist_service(salon=salon, specialist=ann, service=service)
    _link_specialist_service(salon=salon, specialist=bob, service=service)
    make_working_hours(
        salon=salon,
        specialist=ann,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(11, 0),
    )
    make_working_hours(
        salon=salon,
        specialist=bob,
        day_of_week=monday.weekday(),
        start_time=dt.time(14, 0),
        end_time=dt.time(16, 0),
    )
    now = dt.datetime(2026, 8, 17, 0, 0, tzinfo=UTC)
    with tenant_context(salon.id):
        result = compute_multi_specialist_availability(
            service=service,
            salon=salon,
            date_from=monday,
            date_to=monday,
            now=now,
        )
    assert result == {
        _t(6): [ann],
        _t(6, 15): [ann],
        _t(6, 30): [ann],
        _t(6, 45): [ann],
        _t(11): [bob],
        _t(11, 15): [bob],
        _t(11, 30): [bob],
        _t(11, 45): [bob],
    }


def test_compute_multi_specialist_availability_excludes_inactive_specialist(
    django_assert_num_queries, salon, service
):
    """
    Ann and Bob are otherwise identical (same WorkingHours); Bob is
    is_active=False. Bob must be absent from the mapping AND
    compute_candidate_start_times must never be called for him — pinned by
    the query count, so the exclusion can only pass if it happens at
    _fetch_qualifying_specialists (§ Stage 6.H decisions), not as a
    downstream filter that would still spend Bob's three queries. If Bob's
    queries ran, this would be 1 + 3*2 = 7, not 1 + 3*1 = 4 — the same class
    of accidental-pass risk the earlier is_active audit found for the
    single-specialist path.
    """
    monday = dt.date(2026, 8, 17)
    ann = _make_specialist(salon=salon, name="Ann")
    bob = _make_specialist(salon=salon, name="Bob", is_active=False)
    for person in (ann, bob):
        _link_specialist_service(salon=salon, specialist=person, service=service)
        make_working_hours(
            salon=salon,
            specialist=person,
            day_of_week=monday.weekday(),
            start_time=dt.time(9, 0),
            end_time=dt.time(11, 0),
        )
    now = dt.datetime(2026, 8, 17, 0, 0, tzinfo=UTC)
    with tenant_context(salon.id):
        with django_assert_num_queries(1 + 3 * 1):
            result = compute_multi_specialist_availability(
                service=service,
                salon=salon,
                date_from=monday,
                date_to=monday,
                now=now,
            )
    assert result == {
        _t(6): [ann],
        _t(6, 15): [ann],
        _t(6, 30): [ann],
        _t(6, 45): [ann],
    }


def test_compute_multi_specialist_availability_zero_qualifying_specialists_returns_empty(
    salon, service
):
    """No specialist is linked to the service at all — a valid answer, {},
    not an error, not an exception (§ Stage 6.H decisions)."""
    monday = dt.date(2026, 8, 17)
    now = dt.datetime(2026, 8, 17, 0, 0, tzinfo=UTC)
    with tenant_context(salon.id):
        result = compute_multi_specialist_availability(
            service=service,
            salon=salon,
            date_from=monday,
            date_to=monday,
            now=now,
        )
    assert result == {}


def test_compute_multi_specialist_availability_naive_now_raises_with_zero_qualifying_specialists(
    salon, service
):
    """
    § Stage 6.H decisions: with zero qualifying specialists the inner
    compute_candidate_start_times is never called, so this function must
    re-validate `now` itself on its first line — otherwise a naive `now`
    would silently return {}, indistinguishable from "genuinely nothing
    available." No specialist is linked to the service, so if the guard
    lived only in the inner function this would wrongly return {} instead
    of raising.
    """
    monday = dt.date(2026, 8, 17)
    with tenant_context(salon.id), pytest.raises(ValueError):
        compute_multi_specialist_availability(
            service=service,
            salon=salon,
            date_from=monday,
            date_to=monday,
            now=dt.datetime(2026, 8, 17, 0, 0),  # naive — no tzinfo
        )


def test_compute_multi_specialist_availability_single_specialist_mirrors_primitive(
    salon, specialist, service
):
    """
    One qualifying specialist: the mapping's keys must be exactly that
    specialist's own compute_candidate_start_times list, each mapped to
    [that specialist] — the composition changes nothing about a single
    specialist's own result.
    """
    monday = dt.date(2026, 8, 17)
    _link_specialist_service(salon=salon, specialist=specialist, service=service)
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=monday.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )
    now = dt.datetime(2026, 8, 17, 0, 0, tzinfo=UTC)
    with tenant_context(salon.id):
        expected_times = compute_candidate_start_times(
            specialist=specialist,
            service=service,
            salon=salon,
            date_from=monday,
            date_to=monday,
            now=now,
        )
        result = compute_multi_specialist_availability(
            service=service,
            salon=salon,
            date_from=monday,
            date_to=monday,
            now=now,
        )
    assert expected_times != []  # baseline: this fixture must actually produce candidates
    assert result == {t: [specialist] for t in expected_times}


@pytest.mark.parametrize("n_qualifying", [0, 1, 3])
def test_compute_multi_specialist_availability_query_count_is_formula_of_n(
    django_assert_num_queries, salon, service, n_qualifying
):
    """
    § Stage 6.H decisions / § Open questions: 1 + 3*N, N counting only
    qualifying (linked + is_active) specialists. No WorkingHours needed —
    compute_open_windows issues its three queries regardless of whether any
    WorkingHours rows exist for a given specialist.
    """
    monday = dt.date(2026, 8, 17)
    for i in range(n_qualifying):
        person = _make_specialist(salon=salon, name=f"Specialist{i}")
        _link_specialist_service(salon=salon, specialist=person, service=service)
    now = dt.datetime(2026, 8, 17, 0, 0, tzinfo=UTC)
    with tenant_context(salon.id):
        with django_assert_num_queries(1 + 3 * n_qualifying):
            compute_multi_specialist_availability(
                service=service,
                salon=salon,
                date_from=monday,
                date_to=monday,
                now=now,
            )
