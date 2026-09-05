"""
Stage 7.D — guest booking POST endpoint (docs/ARCHITECTURE.md § 2, § 3;
docs/DECISIONS.md § Stage 7.D decisions). `POST
/api/v1/salons/<slug>/bookings/`, AllowAny, wrapping the § Stage 7.C-bis
orchestrator `create_guest_appointment`.

Test 3 (slot no longer available) cannot be set up with a real conflicting
`make_appointment` row alone: `compute_open_windows` (scheduling/services.py)
subtracts every ACTIVE_APPOINTMENT_STATUSES appointment's buffered interval
from the specialist's open windows *before* candidates are stepped out, so a
real row on the exact requested interval removes that slot from the
candidate grid entirely and would raise SlotNotOfferedError instead of
SlotUnavailableError — the same trap test_booking_create_appointment.py's
own double-booking tests already document. This file reuses that file's
fix: monkeypatch `booking.services.compute_candidate_start_times` (a bare
module-global name, pinned for exactly this reason by § Stage 7.C decisions
— renaming it silently breaks this test's interception, not just that
file's) to force the slot-validity check to report the slot as offered,
then let the real conflicting row trip the real application-level
overlap re-check.

`start_datetime` values sent in request bodies are fixed tz-aware UTC
literals, never computed from `timezone.now()` — same discipline as § Stage
6.F/7.C/7.C-bis decisions. Unlike the unit tests in
test_booking_create_appointment.py / test_booking_create_guest_appointment.py
though, `now` itself is not a parameter this test controls directly — the
view computes `now = timezone.now()` internally (§ Stage 7.D decisions'
single-call-site rule, same as § Stage 6.F/6.I). Test 1, whose result
depends on where `now` falls relative to MONDAY (the lead-time/max-advance
window), freezes it via monkeypatch — same pattern
test_availability_endpoint.py's `_freeze_now` uses. Tests 2 and 3 don't
need the freeze: test 2's empty-candidates result holds regardless of `now`
(no WorkingHours row exists at all), and test 3 defeats the engine's
candidate computation entirely via monkeypatch, independent of the real
clock.
"""

import datetime as dt

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

import booking.services as booking_services
from booking.guest_tokens import derive_guest_token, validate_guest_token
from booking.models import Appointment, AppointmentStatus
from core.tenancy import tenant_context
from tests.conftest import make_appointment, make_working_hours

pytestmark = pytest.mark.django_db

UTC = dt.UTC

# Same numbers as test_booking_create_appointment.py / the 7.C-bis test file:
# a Monday with 09:00-18:00 local (Europe/Kyiv, the `salon` fixture's default
# tz) working hours == 06:00-15:00 UTC, so 06:00 UTC is always the first
# on-grid candidate for the `service` fixture's duration/buffer.
MONDAY = dt.date(2026, 8, 17)
FIRST_CANDIDATE = dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC)

# Well within the default min_lead_time_hours=3 / max_advance_days=60 window
# for FIRST_CANDIDATE.
SAFE_NOW = dt.datetime(2026, 8, 16, 0, 0, tzinfo=UTC)


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def _bookings_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/bookings/"


def _freeze_now(monkeypatch: pytest.MonkeyPatch, value: dt.datetime = SAFE_NOW) -> None:
    monkeypatch.setattr(timezone, "now", lambda: value)


def _working_hours(salon, specialist, *, day: dt.date = MONDAY) -> None:
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=day.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )


def _payload(specialist, service, *, start_datetime: dt.datetime = FIRST_CANDIDATE) -> dict:
    return {
        "specialist": specialist.id,
        "service": service.id,
        "start_datetime": start_datetime.isoformat(),
        "customer_name": "Alice",
        "customer_email": "alice@example.com",
        "customer_phone": "+10000000000",
    }


def _defeat_slot_validity_check(monkeypatch, start_datetime: dt.datetime) -> None:
    """Forces create_appointment's engine-level slot-validity check to
    report `start_datetime` as offered, regardless of real DB state — see
    the module docstring for why test 3 needs this."""

    def _fake_compute_candidate_start_times(*args, **kwargs):
        return [start_datetime]

    monkeypatch.setattr(
        booking_services, "compute_candidate_start_times", _fake_compute_candidate_start_times
    )


# --- 1. Happy path -----------------------------------------------------------


def test_valid_guest_booking_on_offered_slot_returns_201_without_a_token_in_the_body(
    client, monkeypatch, salon, specialist, service
):
    _working_hours(salon, specialist)
    _freeze_now(monkeypatch)

    response = client.post(_bookings_url(salon), _payload(specialist, service), format="json")

    assert response.status_code == 201
    with tenant_context(salon.id):
        appt = Appointment.objects.get()
    assert appt.status == AppointmentStatus.PENDING_PAYMENT
    assert appt.specialist_id == specialist.id
    assert appt.service_id == service.id
    assert response.data["appointment"]["id"] == appt.id

    # The raw guest token is no longer in the 201 body (docs/DECISIONS.md §
    # Step (d) decisions) — it is delivered later, in the BOOKING_CONFIRMED
    # email, re-derived from the appointment id rather than round-tripped
    # through this response.
    assert "token" not in response.data

    # A working token still exists for this appointment end-to-end — proven
    # via the commit-A re-derivation function instead of off the response.
    with tenant_context(salon.id):
        derived = derive_guest_token(appt.id)
        validated = validate_guest_token(derived)
    assert validated.appointment_id == appt.id


# --- 2. Slot not offered -----------------------------------------------------


def test_slot_not_offered_returns_400(client, salon, specialist, service):
    # Deliberately no WorkingHours row for `specialist` on MONDAY, so
    # FIRST_CANDIDATE is never an offered candidate at all.
    response = client.post(_bookings_url(salon), _payload(specialist, service), format="json")

    assert response.status_code == 400
    assert response.data["error"]["code"] == "SLOT_NOT_OFFERED"


# --- 3. Slot no longer available ---------------------------------------------


def test_slot_taken_by_another_booking_returns_409(
    client, monkeypatch, salon, specialist, service, customer
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

    response = client.post(_bookings_url(salon), _payload(specialist, service), format="json")

    assert response.status_code == 409
    assert response.data["error"]["code"] == "SLOT_NO_LONGER_AVAILABLE"
