"""
Stage 14 — "any specialist" assignment (docs/DECISIONS.md § Stage 14 scope
revision "Assignment rule", and its 15.09.2026 tie-break clarification).
Covers three units:

- `booking.services.select_specialist_for_any`: pure ordering logic (same-day
  busyness ascending, seeded-rng tie-break within equal-count groups).
- `booking.services.create_appointment_for_any_specialist`: the retry
  orchestrator that walks that ordering, attempting `create_appointment`
  (unchanged) once per candidate.
- `booking.services.create_guest_appointment`'s "any" dispatch branch, and
  proof the real-specialist-id branch is untouched by it.
- `booking.serializers.GuestBookingRequestSerializer`'s `specialist` field
  accepting `"any"` alongside a real pk (docs/DECISIONS.md § Stage 14
  implementation decisions).

Test 6 (retry) and test 7 (exhausted) isolate the retry loop from the
ordering logic by monkeypatching `select_specialist_for_any`'s return value
directly — the same "mock the layer you're not testing" technique
test_booking_create_appointment.py's own double-booking tests use (see that
file's module docstring), since ordering itself is already covered by tests
1-4 below and a real busyness-based conflict would fight the ordering
instead of proving the retry step.

All fixed `now`/`start_datetime` values are tz-aware UTC literals, never
`timezone.now()` — same discipline as § Stage 6.F/7.C decisions.
"""

import datetime as dt
import random

import pytest

import booking.services as booking_services
from booking.models import Appointment, AppointmentStatus
from booking.serializers import GuestBookingRequestSerializer
from booking.services import (
    create_appointment_for_any_specialist,
    create_guest_appointment,
    select_specialist_for_any,
)
from core.exceptions import SlotNotOfferedError, SlotUnavailableError
from core.tenancy import tenant_context
from specialists.models import Specialist, SpecialistService
from tests.conftest import make_appointment, make_working_hours

UTC = dt.UTC

# Same numbers as test_booking_create_appointment.py: a Monday with
# 09:00-18:00 local (Europe/Kyiv, the `salon` fixture's default tz) working
# hours == 06:00-15:00 UTC, so 06:00 UTC is always the first on-grid
# candidate for the `service` fixture's duration/buffer.
MONDAY = dt.date(2026, 8, 17)
FIRST_CANDIDATE = dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC)

# Well within the default min_lead_time_hours=3 / max_advance_days=60 window
# for FIRST_CANDIDATE.
SAFE_NOW = dt.datetime(2026, 8, 16, 0, 0, tzinfo=UTC)


def _working_hours(salon, specialist, *, day: dt.date = MONDAY) -> None:
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=day.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )


def _make_specialist(salon, name: str) -> Specialist:
    with tenant_context(salon.id):
        return Specialist.objects.create(salon=salon, name={"en": name})


def _link_specialist_service(*, salon, specialist: Specialist, service) -> None:
    with tenant_context(salon.id):
        SpecialistService.objects.create(salon=salon, specialist=specialist, service=service)


# === select_specialist_for_any ==============================================

# --- 1. Ascending busyness ordering ------------------------------------------


def test_select_specialist_for_any_orders_by_ascending_same_day_appointment_count(
    salon, service, customer
):
    a = _make_specialist(salon, "A")
    b = _make_specialist(salon, "B")
    c = _make_specialist(salon, "C")
    with tenant_context(salon.id):
        make_appointment(
            salon=salon,
            customer=customer,
            specialist=b,
            service=service,
            start=FIRST_CANDIDATE,
            status=AppointmentStatus.CONFIRMED,
        )
        make_appointment(
            salon=salon,
            customer=customer,
            specialist=c,
            service=service,
            start=FIRST_CANDIDATE,
            status=AppointmentStatus.CONFIRMED,
        )
        make_appointment(
            salon=salon,
            customer=customer,
            specialist=c,
            service=service,
            start=FIRST_CANDIDATE + dt.timedelta(hours=2),
            status=AppointmentStatus.CONFIRMED,
        )
        ordered = select_specialist_for_any([b, c, a], salon, MONDAY, rng=random.Random(0))
    assert ordered == [a, b, c]


# --- 2. Tie-break determinism via seeded rng ---------------------------------


def test_select_specialist_for_any_ties_broken_deterministically_by_seeded_rng(salon):
    x = _make_specialist(salon, "X")
    y = _make_specialist(salon, "Y")
    with tenant_context(salon.id):
        result = select_specialist_for_any([x, y], salon, MONDAY, rng=random.Random(42))
        result_again = select_specialist_for_any([x, y], salon, MONDAY, rng=random.Random(42))

    expected = [x, y]
    random.Random(42).shuffle(expected)

    assert result == expected
    assert result_again == expected


# --- 3. Empty candidates ------------------------------------------------------


def test_select_specialist_for_any_empty_candidates_returns_empty_list(salon):
    with tenant_context(salon.id):
        assert select_specialist_for_any([], salon, MONDAY) == []


# --- 4. Single aggregate query, not one per candidate -------------------------


def test_select_specialist_for_any_uses_a_single_aggregate_query(
    salon, service, customer, django_assert_num_queries
):
    a = _make_specialist(salon, "A")
    b = _make_specialist(salon, "B")
    c = _make_specialist(salon, "C")
    with tenant_context(salon.id):
        make_appointment(
            salon=salon,
            customer=customer,
            specialist=b,
            service=service,
            start=FIRST_CANDIDATE,
            status=AppointmentStatus.CONFIRMED,
        )
        with django_assert_num_queries(1):
            select_specialist_for_any([a, b, c], salon, MONDAY, rng=random.Random(0))


# === create_appointment_for_any_specialist ===================================

# --- 5. Assigns the least-busy real candidate ---------------------------------


def test_create_appointment_for_any_specialist_assigns_least_busy_candidate(
    salon, service, customer
):
    busy = _make_specialist(salon, "Busy")
    free = _make_specialist(salon, "Free")
    _working_hours(salon, busy)
    _working_hours(salon, free)
    _link_specialist_service(salon=salon, specialist=busy, service=service)
    _link_specialist_service(salon=salon, specialist=free, service=service)
    with tenant_context(salon.id):
        make_appointment(
            salon=salon,
            customer=customer,
            specialist=busy,
            service=service,
            start=FIRST_CANDIDATE + dt.timedelta(hours=3),
            status=AppointmentStatus.CONFIRMED,
        )
        appt = create_appointment_for_any_specialist(
            salon=salon,
            service=service,
            customer=customer,
            start_datetime=FIRST_CANDIDATE,
            now=SAFE_NOW,
            rng=random.Random(0),
        )
    assert appt.specialist_id == free.id


# --- 6. Retries the next candidate after the first's real conflict -----------


def test_create_appointment_for_any_specialist_retries_next_candidate_after_first_conflict(
    monkeypatch, salon, service, customer
):
    first_choice = _make_specialist(salon, "FirstChoice")
    second_choice = _make_specialist(salon, "SecondChoice")
    with tenant_context(salon.id):
        # Occupies first_choice's exact slot — the real
        # _has_overlapping_active_appointment recheck inside create_appointment
        # (not monkeypatched here) must catch this.
        make_appointment(
            salon=salon,
            customer=customer,
            specialist=first_choice,
            service=service,
            start=FIRST_CANDIDATE,
            status=AppointmentStatus.CONFIRMED,
        )

    monkeypatch.setattr(
        booking_services,
        "compute_multi_specialist_availability",
        lambda **kwargs: {FIRST_CANDIDATE: [first_choice, second_choice]},
    )
    monkeypatch.setattr(
        booking_services,
        "select_specialist_for_any",
        lambda candidates, salon, date, rng=None: [first_choice, second_choice],
    )
    # Defeats each candidate's own engine-level slot-validity check — neither
    # specialist has real WorkingHours here, and first_choice's real
    # appointment would otherwise also remove it from its own candidate grid
    # before the double-booking recheck ever runs (same trap documented in
    # test_booking_create_appointment.py's module docstring).
    monkeypatch.setattr(
        booking_services, "compute_candidate_start_times", lambda **kwargs: [FIRST_CANDIDATE]
    )

    with tenant_context(salon.id):
        appt = create_appointment_for_any_specialist(
            salon=salon,
            service=service,
            customer=customer,
            start_datetime=FIRST_CANDIDATE,
            now=SAFE_NOW,
        )
    assert appt.specialist_id == second_choice.id


# --- 7. SlotUnavailableError only once every candidate is exhausted ----------


def test_create_appointment_for_any_specialist_raises_slot_unavailable_after_all_exhausted(
    monkeypatch, salon, service, customer
):
    a = _make_specialist(salon, "A")
    b = _make_specialist(salon, "B")
    with tenant_context(salon.id):
        make_appointment(
            salon=salon,
            customer=customer,
            specialist=a,
            service=service,
            start=FIRST_CANDIDATE,
            status=AppointmentStatus.CONFIRMED,
        )
        make_appointment(
            salon=salon,
            customer=customer,
            specialist=b,
            service=service,
            start=FIRST_CANDIDATE,
            status=AppointmentStatus.CONFIRMED,
        )

    monkeypatch.setattr(
        booking_services,
        "compute_multi_specialist_availability",
        lambda **kwargs: {FIRST_CANDIDATE: [a, b]},
    )
    monkeypatch.setattr(
        booking_services,
        "select_specialist_for_any",
        lambda candidates, salon, date, rng=None: [a, b],
    )
    monkeypatch.setattr(
        booking_services, "compute_candidate_start_times", lambda **kwargs: [FIRST_CANDIDATE]
    )

    with tenant_context(salon.id), pytest.raises(SlotUnavailableError):
        create_appointment_for_any_specialist(
            salon=salon,
            service=service,
            customer=customer,
            start_datetime=FIRST_CANDIDATE,
            now=SAFE_NOW,
        )
    with tenant_context(salon.id):
        assert Appointment.objects.count() == 2  # the two seeded rows, nothing new


# --- 8. SlotNotOfferedError when no candidate is free -------------------------


def test_create_appointment_for_any_specialist_raises_slot_not_offered_when_no_candidate_free(
    salon, service, customer
):
    # No specialists linked to `service` at all — zero qualifying specialists,
    # real/unmocked compute_multi_specialist_availability.
    with tenant_context(salon.id), pytest.raises(SlotNotOfferedError):
        create_appointment_for_any_specialist(
            salon=salon,
            service=service,
            customer=customer,
            start_datetime=FIRST_CANDIDATE,
            now=SAFE_NOW,
        )


# === create_guest_appointment dispatch =======================================

# --- 9. "any" happy path, end-to-end ------------------------------------------


def test_create_guest_appointment_any_specialist_creates_appointment_and_token(salon, service):
    x = _make_specialist(salon, "X")
    y = _make_specialist(salon, "Y")
    _working_hours(salon, x)
    _working_hours(salon, y)
    _link_specialist_service(salon=salon, specialist=x, service=service)
    _link_specialist_service(salon=salon, specialist=y, service=service)

    with tenant_context(salon.id):
        appt, raw_token = create_guest_appointment(
            salon=salon,
            specialist="any",
            service=service,
            start_datetime=FIRST_CANDIDATE,
            now=SAFE_NOW,
            customer_name="Alice",
            customer_email="alice@example.com",
            customer_phone="+10000000000",
            rng=random.Random(0),
        )
    assert appt.status == AppointmentStatus.PENDING_PAYMENT
    assert appt.specialist_id in {x.id, y.id}
    assert raw_token


# --- 10. Real specialist id path never touches the "any" logic ---------------


def test_create_guest_appointment_specific_specialist_path_does_not_use_any_logic(
    monkeypatch, salon, specialist, service
):
    _working_hours(salon, specialist)

    def _fail_if_called(**kwargs):
        raise AssertionError("create_appointment_for_any_specialist must not run for a real id")

    monkeypatch.setattr(booking_services, "create_appointment_for_any_specialist", _fail_if_called)

    with tenant_context(salon.id):
        appt, _raw_token = create_guest_appointment(
            salon=salon,
            specialist=specialist,
            service=service,
            start_datetime=FIRST_CANDIDATE,
            now=SAFE_NOW,
            customer_name="Alice",
            customer_email="alice@example.com",
            customer_phone="+10000000000",
        )
    assert appt.specialist_id == specialist.id


# === GuestBookingRequestSerializer / SpecialistOrAnyField ====================

# --- 11. Accepts the "any" literal --------------------------------------------


def test_guest_booking_request_serializer_accepts_any_literal_for_specialist(salon, service):
    with tenant_context(salon.id):
        serializer = GuestBookingRequestSerializer(
            data={
                "specialist": "any",
                "service": service.id,
                "start_datetime": FIRST_CANDIDATE.isoformat(),
                "customer_name": "Alice",
                "customer_email": "alice@example.com",
                "customer_phone": "+10000000000",
            }
        )
        assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["specialist"] == "any"


# --- 12. Rejects an arbitrary non-pk string -----------------------------------


def test_guest_booking_request_serializer_rejects_arbitrary_non_pk_string_for_specialist(
    salon, service
):
    with tenant_context(salon.id):
        serializer = GuestBookingRequestSerializer(
            data={
                "specialist": "banana",
                "service": service.id,
                "start_datetime": FIRST_CANDIDATE.isoformat(),
                "customer_name": "Alice",
                "customer_email": "alice@example.com",
                "customer_phone": "+10000000000",
            }
        )
        assert not serializer.is_valid()
    assert "specialist" in serializer.errors


# --- 13. Still accepts a real specialist pk -----------------------------------


def test_guest_booking_request_serializer_still_accepts_real_specialist_pk(
    salon, specialist, service
):
    with tenant_context(salon.id):
        serializer = GuestBookingRequestSerializer(
            data={
                "specialist": specialist.id,
                "service": service.id,
                "start_datetime": FIRST_CANDIDATE.isoformat(),
                "customer_name": "Alice",
                "customer_email": "alice@example.com",
                "customer_phone": "+10000000000",
            }
        )
        assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["specialist"] == specialist
