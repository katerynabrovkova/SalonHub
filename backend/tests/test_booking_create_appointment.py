"""
Stage 7.C — `create_appointment` service function (docs/ARCHITECTURE.md § 2,
§ 7, § 14, the State machines section; docs/DECISIONS.md § Stage 7.C
decisions). Scoped to the core `create_appointment` function only — Customer
get-or-create, `GuestAccessToken` issuance (7.C-bis), and the `POST` endpoint
(7.D) are out of scope here (§ Stage 7.C decisions, "Scope").

Written against the agreed design before any implementation exists — this
file is expected to fail on collection (ModuleNotFoundError: no module named
`booking.services`) until that module is written, then again
(ImportError: cannot import name 'SlotNotOfferedError'/'SlotUnavailableError'
from 'core.exceptions') until those two new `DomainError` subclasses are
added — the same two-step red shape `test_scheduling_availability.py`
established for `scheduling/services.py`.

Two implementation-detail names this file pins, not stated verbatim in
docs/DECISIONS.md § Stage 7.C decisions, needed to make the double-booking
tests (6/7 below) actually exercise the layer they claim to (a short
docs/DECISIONS.md line recording this as deliberate is a separate,
follow-up step — not done in this commit):

- `create_appointment` is expected to call the scheduling engine via a
  bare, module-global `compute_candidate_start_times(...)` reference (i.e.
  `from scheduling.services import compute_candidate_start_times` at the top
  of `booking/services.py`), never a namespaced `scheduling_services.compute_...`
  call — so that `monkeypatch.setattr(booking_services,
  "compute_candidate_start_times", ...)` actually intercepts it. This
  mirrors the "from module import name" style `scheduling/services.py`
  itself already uses for its own cross-app imports.
- The private double-booking re-check helper (§ Stage 7.C decisions,
  "Double-booking prevention, two layers") is expected to be named
  `_has_overlapping_active_appointment`, called as a bare module-global
  name for the same monkeypatch-interception reason.

Why the double-booking tests need two mocks, not zero: the engine's own
`compute_open_windows` already subtracts every ACTIVE_APPOINTMENT_STATUSES
appointment's buffered interval from a specialist's open windows (§ Stage
6.D decisions) — the exact same notion of "occupied" the double-booking
layers use. A `make_appointment` row placed before calling
`create_appointment`, in the same test (so it's visible to every query in
the same open transaction), is therefore *already* excluded from
`compute_candidate_start_times`'s candidate list by the time the slot-
validity check (§ Stage 7.C decisions, "the key decision") runs, which
happens **before** `transaction.atomic()` — so an unmocked call would raise
`SlotNotOfferedError` there, and the double-booking layers inside the atomic
block would never even run. This isn't a bug in the design: the slot-
validity check and the double-booking re-check are deliberately redundant
for the ordinary (non-racing) case, and differ only in *when* they run — the
re-check exists specifically for the gap between the two. To test each
double-booking layer in isolation, both tests force the slot-validity check
to report the slot as offered regardless of real state
(`compute_candidate_start_times` monkeypatched to return `[start_datetime]`)
before making the real conflicting row's effect on each of the two deeper
layers observable:
  - Test 6 (application-level layer): only the engine check is defeated;
    the real `_has_overlapping_active_appointment` runs against the real
    conflicting row and must catch it.
  - Test 7 (DB layer): both the engine check and
    `_has_overlapping_active_appointment` are defeated, so only the
    Postgres exclusion constraint stands between the call and a genuine
    double-booked insert.

"Wrong specialist" (item 5 in the agreed test list) is written here as a
specialist with no matching `WorkingHours` for the requested day — not as a
specialist unqualified for the service via `SpecialistService`.
`compute_candidate_start_times` never checks `SpecialistService` itself
(only `compute_multi_specialist_availability`'s "any specialist"
composition does, § Stage 6.H decisions), and `create_appointment` per §
Stage 7.C decisions only calls `compute_candidate_start_times` — so an
unqualified-but-active specialist with matching working hours is not
rejected by this service function at all. That gap is real but out of
scope for this sub-step's test list; "wrong specialist" is pinned here as
"no working hours," the one mechanism that actually is enforced.

All fixed `now`/`start_datetime` values are tz-aware UTC literals, never
`timezone.now()` — same discipline as § Stage 6.F decisions.
"""

import datetime as dt
from decimal import Decimal

import pytest
from django.db import IntegrityError

import booking.services as booking_services
from booking.constants import SLOT_HOLD_DURATION
from booking.models import Appointment, AppointmentStatus
from booking.services import create_appointment
from core.exceptions import SlotNotOfferedError, SlotUnavailableError
from core.tenancy import tenant_context
from specialists.models import Specialist
from tests.conftest import make_appointment, make_working_hours

UTC = dt.UTC

# A Monday with 09:00-18:00 local (Europe/Kyiv, the `salon` fixture's
# default, EEST = UTC+3 in August) working hours == 06:00-15:00 UTC —
# reused verbatim from test_scheduling_availability.py's own numbers so the
# candidate grid (granularity 15 / occupied 75 for the `service` fixture's
# duration 60 + buffer 15) is already known-correct: 06:00 is always the
# first on-grid candidate.
MONDAY = dt.date(2026, 8, 17)
FIRST_CANDIDATE = dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC)

# Well before MONDAY and well within the default min_lead_time_hours=3 /
# max_advance_days=60 window for FIRST_CANDIDATE — a safe default `now` for
# every test that isn't specifically pinning lead-time/max-advance
# rejection.
SAFE_NOW = dt.datetime(2026, 8, 16, 0, 0, tzinfo=UTC)


def _working_hours(salon, specialist, *, day: dt.date = MONDAY) -> None:
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=day.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )


# --- 1. Happy path --------------------------------------------------------


def test_create_appointment_happy_path_creates_pending_payment_row(
    salon, specialist, service, customer
):
    _working_hours(salon, specialist)
    with tenant_context(salon.id):
        appt = create_appointment(
            salon=salon,
            specialist=specialist,
            service=service,
            customer=customer,
            start_datetime=FIRST_CANDIDATE,
            now=SAFE_NOW,
        )
    assert appt.status == AppointmentStatus.PENDING_PAYMENT
    assert appt.start_datetime == FIRST_CANDIDATE
    assert appt.end_datetime == FIRST_CANDIDATE + dt.timedelta(minutes=service.duration_minutes)
    assert appt.blocked_until == appt.end_datetime + dt.timedelta(minutes=service.buffer_minutes)
    assert appt.specialist_id == specialist.id
    assert appt.service_id == service.id
    assert appt.customer_id == customer.id


# --- 2. Snapshot fields frozen --------------------------------------------


def test_create_appointment_snapshot_fields_frozen_against_later_price_and_deposit_changes(
    salon, specialist, service, customer
):
    _working_hours(salon, specialist)
    # Decimal(...): the `service`/`salon` fixtures assign price/
    # deposit_percentage as plain Python literals ("500.00", the model
    # field's default int 20) — Django doesn't coerce a DecimalField to
    # Decimal on the in-memory instance until it's written to or read back
    # from the DB, so the bare fixture attributes are str/int here, not
    # Decimal. Wrapping them states the expected frozen value explicitly,
    # rather than comparing across two different Python types that Decimal
    # never considers equal regardless of numeric value.
    original_price = Decimal(service.price)
    original_deposit = Decimal(salon.deposit_percentage)

    with tenant_context(salon.id):
        appt = create_appointment(
            salon=salon,
            specialist=specialist,
            service=service,
            customer=customer,
            start_datetime=FIRST_CANDIDATE,
            now=SAFE_NOW,
        )

        service.price = Decimal("999.00")
        service.save(update_fields=["price"])
        salon.deposit_percentage = Decimal("50.00")
        salon.save(update_fields=["deposit_percentage"])

        appt.refresh_from_db()

    assert appt.service_price_at_booking == original_price
    assert appt.deposit_percentage_at_booking == original_deposit
    assert appt.service_price_at_booking != service.price
    assert appt.deposit_percentage_at_booking != salon.deposit_percentage


# --- 3. hold_expires_at ----------------------------------------------------


def test_create_appointment_hold_expires_at_is_now_plus_slot_hold_duration(
    salon, specialist, service, customer
):
    _working_hours(salon, specialist)
    with tenant_context(salon.id):
        appt = create_appointment(
            salon=salon,
            specialist=specialist,
            service=service,
            customer=customer,
            start_datetime=FIRST_CANDIDATE,
            now=SAFE_NOW,
        )
    assert appt.hold_expires_at == SAFE_NOW + SLOT_HOLD_DURATION


# --- 4. salon set explicitly -----------------------------------------------


def test_create_appointment_writes_salon_explicitly(salon, specialist, service, customer):
    _working_hours(salon, specialist)
    with tenant_context(salon.id):
        appt = create_appointment(
            salon=salon,
            specialist=specialist,
            service=service,
            customer=customer,
            start_datetime=FIRST_CANDIDATE,
            now=SAFE_NOW,
        )
    # unscoped_objects bypasses the tenant-scoped manager's own salon_id
    # filter, so this actually proves the column was written, rather than
    # merely proving the row was findable under the expected tenant.
    row = Appointment.unscoped_objects.get(pk=appt.pk)
    assert row.salon_id == salon.id


# --- 5. Slot not offered ----------------------------------------------------


def test_create_appointment_slot_not_offered_before_lead_time(salon, specialist, service, customer):
    """salon.min_lead_time_hours=3 (default); now pinned to window start
    puts lower_bound at 09:00 UTC, after FIRST_CANDIDATE (06:00 UTC)."""
    _working_hours(salon, specialist)
    now = dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC)  # window start
    with tenant_context(salon.id), pytest.raises(SlotNotOfferedError) as exc_info:
        create_appointment(
            salon=salon,
            specialist=specialist,
            service=service,
            customer=customer,
            start_datetime=FIRST_CANDIDATE,
            now=now,
        )
    assert exc_info.value.status_code == 400
    with tenant_context(salon.id):
        assert Appointment.objects.count() == 0


def test_create_appointment_slot_not_offered_beyond_max_advance(
    salon, specialist, service, customer
):
    """max_advance_days=1 with `now` pinned to Monday puts the boundary
    before Wednesday's window — mirrors
    test_compute_candidate_start_times_filters_out_candidates_beyond_max_advance."""
    salon.max_advance_days = 1
    salon.save(update_fields=["max_advance_days"])
    wednesday = MONDAY + dt.timedelta(days=2)
    _working_hours(salon, specialist, day=MONDAY)
    _working_hours(salon, specialist, day=wednesday)
    wednesday_candidate = dt.datetime(2026, 8, 19, 6, 0, tzinfo=UTC)
    now = dt.datetime(2026, 8, 17, 0, 0, tzinfo=UTC)
    with tenant_context(salon.id), pytest.raises(SlotNotOfferedError) as exc_info:
        create_appointment(
            salon=salon,
            specialist=specialist,
            service=service,
            customer=customer,
            start_datetime=wednesday_candidate,
            now=now,
        )
    assert exc_info.value.status_code == 400
    with tenant_context(salon.id):
        assert Appointment.objects.count() == 0


def test_create_appointment_slot_not_offered_specialist_has_no_working_hours(
    salon, service, customer
):
    """A specialist with no WorkingHours rows at all never offers
    FIRST_CANDIDATE, regardless of another specialist's schedule."""
    with tenant_context(salon.id):
        specialist_with_no_hours = Specialist.objects.create(salon=salon, name={"en": "No Hours"})
    with tenant_context(salon.id), pytest.raises(SlotNotOfferedError) as exc_info:
        create_appointment(
            salon=salon,
            specialist=specialist_with_no_hours,
            service=service,
            customer=customer,
            start_datetime=FIRST_CANDIDATE,
            now=SAFE_NOW,
        )
    assert exc_info.value.status_code == 400
    with tenant_context(salon.id):
        assert Appointment.objects.count() == 0


def test_create_appointment_slot_not_offered_inactive_specialist(
    salon, specialist, service, customer
):
    _working_hours(salon, specialist)
    with tenant_context(salon.id):
        specialist.is_active = False
        specialist.save(update_fields=["is_active"])
    with tenant_context(salon.id), pytest.raises(SlotNotOfferedError) as exc_info:
        create_appointment(
            salon=salon,
            specialist=specialist,
            service=service,
            customer=customer,
            start_datetime=FIRST_CANDIDATE,
            now=SAFE_NOW,
        )
    assert exc_info.value.status_code == 400
    with tenant_context(salon.id):
        assert Appointment.objects.count() == 0


# --- 6 & 7. Double-booking --------------------------------------------------


def _defeat_slot_validity_check(monkeypatch, start_datetime: dt.datetime) -> None:
    """Forces create_appointment's engine-level slot-validity check to
    report `start_datetime` as offered, regardless of real DB state — see
    the module docstring for why the double-booking tests need this."""

    def _fake_compute_candidate_start_times(*args, **kwargs):
        return [start_datetime]

    monkeypatch.setattr(
        booking_services, "compute_candidate_start_times", _fake_compute_candidate_start_times
    )


def test_create_appointment_double_booking_application_layer_blocks_before_insert(
    monkeypatch, salon, specialist, service, customer
):
    _working_hours(salon, specialist)
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=FIRST_CANDIDATE,
        status=AppointmentStatus.CONFIRMED,
    )
    _defeat_slot_validity_check(monkeypatch, FIRST_CANDIDATE)

    with tenant_context(salon.id), pytest.raises(SlotUnavailableError) as exc_info:
        create_appointment(
            salon=salon,
            specialist=specialist,
            service=service,
            customer=customer,
            start_datetime=FIRST_CANDIDATE,
            now=SAFE_NOW,
        )
    assert exc_info.value.status_code == 409
    with tenant_context(salon.id):
        assert Appointment.objects.count() == 1


def test_create_appointment_double_booking_db_layer_blocks_when_application_recheck_defeated(
    monkeypatch, salon, specialist, service, customer
):
    _working_hours(salon, specialist)
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=FIRST_CANDIDATE,
        status=AppointmentStatus.CONFIRMED,
    )
    _defeat_slot_validity_check(monkeypatch, FIRST_CANDIDATE)
    monkeypatch.setattr(
        booking_services, "_has_overlapping_active_appointment", lambda *args, **kwargs: False
    )

    with tenant_context(salon.id), pytest.raises(SlotUnavailableError) as exc_info:
        create_appointment(
            salon=salon,
            specialist=specialist,
            service=service,
            customer=customer,
            start_datetime=FIRST_CANDIDATE,
            now=SAFE_NOW,
        )
    assert exc_info.value.status_code == 409
    with tenant_context(salon.id):
        assert Appointment.objects.count() == 1


# --- 8. Non-overlapping back-to-back ----------------------------------------


def test_create_appointment_back_to_back_non_overlapping_succeeds(
    salon, specialist, service, customer
):
    _working_hours(salon, specialist)
    first = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=FIRST_CANDIDATE,
        status=AppointmentStatus.CONFIRMED,
    )
    with tenant_context(salon.id):
        second = create_appointment(
            salon=salon,
            specialist=specialist,
            service=service,
            customer=customer,
            start_datetime=first.blocked_until,
            now=SAFE_NOW,
        )
    assert second.start_datetime == first.blocked_until
    assert second.status == AppointmentStatus.PENDING_PAYMENT
    with tenant_context(salon.id):
        assert Appointment.objects.count() == 2


# --- 9. Cancelled appointment doesn't block ---------------------------------


def test_create_appointment_cancelled_appointment_does_not_block_same_interval(
    salon, specialist, service, customer
):
    _working_hours(salon, specialist)
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=FIRST_CANDIDATE,
        status=AppointmentStatus.CANCELLED,
    )
    with tenant_context(salon.id):
        appt = create_appointment(
            salon=salon,
            specialist=specialist,
            service=service,
            customer=customer,
            start_datetime=FIRST_CANDIDATE,
            now=SAFE_NOW,
        )
    assert appt.status == AppointmentStatus.PENDING_PAYMENT
    with tenant_context(salon.id):
        assert Appointment.objects.count() == 2


# --- 10. Different specialist, same interval --------------------------------


def test_create_appointment_different_specialist_same_interval_both_succeed(
    salon, specialist, service, customer
):
    _working_hours(salon, specialist)
    with tenant_context(salon.id):
        other_specialist = Specialist.objects.create(salon=salon, name={"en": "Other Specialist"})
    _working_hours(salon, other_specialist)

    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=FIRST_CANDIDATE,
        status=AppointmentStatus.CONFIRMED,
    )
    with tenant_context(salon.id):
        appt = create_appointment(
            salon=salon,
            specialist=other_specialist,
            service=service,
            customer=customer,
            start_datetime=FIRST_CANDIDATE,
            now=SAFE_NOW,
        )
    assert appt.status == AppointmentStatus.PENDING_PAYMENT
    assert appt.specialist_id == other_specialist.id
    with tenant_context(salon.id):
        assert Appointment.objects.count() == 2


# --- 11. Transaction atomicity ----------------------------------------------


def test_create_appointment_transaction_not_poisoned_after_db_layer_conflict(
    monkeypatch, salon, specialist, service, customer
):
    """
    Reuses the DB-layer double-booking scenario (test 7), then proves the
    caught-and-translated IntegrityError didn't leave the connection's
    transaction unusable for later queries/writes in the same request —
    i.e. create_appointment's atomic block for the insert attempt is its
    own savepoint, not sharing one with the surrounding transaction.
    """
    _working_hours(salon, specialist)
    make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=FIRST_CANDIDATE,
        status=AppointmentStatus.CONFIRMED,
    )
    _defeat_slot_validity_check(monkeypatch, FIRST_CANDIDATE)
    monkeypatch.setattr(
        booking_services, "_has_overlapping_active_appointment", lambda *args, **kwargs: False
    )

    with tenant_context(salon.id), pytest.raises(SlotUnavailableError):
        create_appointment(
            salon=salon,
            specialist=specialist,
            service=service,
            customer=customer,
            start_datetime=FIRST_CANDIDATE,
            now=SAFE_NOW,
        )

    # No monkeypatching here — a genuinely independent, non-conflicting
    # booking must still work in the same test (same connection/transaction)
    # right after the failed attempt above.
    with tenant_context(salon.id):
        other_specialist = Specialist.objects.create(salon=salon, name={"en": "Other Specialist"})
    _working_hours(salon, other_specialist)
    with tenant_context(salon.id):
        recovery_appt = create_appointment(
            salon=salon,
            specialist=other_specialist,
            service=service,
            customer=customer,
            start_datetime=FIRST_CANDIDATE,
            now=SAFE_NOW,
        )
    assert recovery_appt.status == AppointmentStatus.PENDING_PAYMENT
    with tenant_context(salon.id):
        # The pre-existing first appointment + the recovery booking only —
        # no partial row survived from the failed DB-layer attempt.
        assert Appointment.objects.count() == 2


# --- 12. Cross-tenant composite-FK guard ------------------------------------


def test_create_appointment_cross_tenant_salon_mismatch_raises_integrity_error(
    salon, other_salon, specialist, service, customer
):
    """
    specialist/service/customer all belong to `salon`; `salon=other_salon`
    is passed to create_appointment by (simulated) caller bug. Per §
    Stage 7.C decisions this is deliberately unhandled: the composite tenant
    FK (core/db.py's composite_tenant_fk) is the only thing that rejects it,
    surfacing as a raw IntegrityError, not a DomainError. tenant_context is
    bound to `salon.id` — the tenant that actually owns these rows, exactly
    as a real per-request context would be — so only the `salon` argument
    itself is wrong, pinning this as a caller bug, not a tenant-context bug.
    """
    _working_hours(salon, specialist)
    with tenant_context(salon.id), pytest.raises(IntegrityError):
        create_appointment(
            salon=other_salon,
            specialist=specialist,
            service=service,
            customer=customer,
            start_datetime=FIRST_CANDIDATE,
            now=SAFE_NOW,
        )
    assert (
        Appointment.unscoped_objects.filter(
            specialist=specialist, start_datetime=FIRST_CANDIDATE
        ).count()
        == 0
    )
