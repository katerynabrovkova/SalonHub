"""
Stage 7.C-bis — the guest booking orchestrator (docs/ARCHITECTURE.md § 2,
§ 3; docs/DECISIONS.md § Stage 7.C-bis decisions). Covers
`booking.services.create_guest_appointment` and, directly, its Customer
get-or-create dependency `accounts.services.get_or_create_guest_customer`.
Guest path only — a registered-user path is a separate later step.

Written against the agreed design before any implementation exists — this
file is expected to fail on collection (ImportError: cannot import name
'get_or_create_guest_customer' from 'accounts.services', then again for
'create_guest_appointment' from 'booking.services') until both are written
— same two-step-red shape § Stage 7.C's own test file established.

Test-design choices worth recording here, not just in the test bodies:

- Items 1 and 2 (brand-new email, returning guest with *identical*
  details) go through the real entry point, `create_guest_appointment` —
  per § Stage 7.C-bis decisions, "most behavior is exercised through the
  orchestrator." Item 3 (the overwrite-on-mismatch semantics, § Stage
  7.C-bis decisions' option A) gets a direct test on
  `get_or_create_guest_customer` instead, since proving a same-pk
  field-overwrite is far cleaner isolated than buried inside the full
  chain's other assertions. Item 4 then re-proves the *same* overwrite
  semantics end-to-end through the orchestrator — not redundant with item
  3: item 3 only proves `get_or_create_guest_customer` itself does the
  right thing; item 4 proves `create_guest_appointment` actually calls it
  (rather than, say, only calling it on the brand-new-email path).
- The two failure-atomicity tests (8, 9) occupy the requested slot with a
  real pre-existing `make_appointment` row and let the natural
  `SlotNotOfferedError` fire — unlike § Stage 7.C's own double-booking
  tests, no `compute_candidate_start_times`/`_has_overlapping_active_appointment`
  monkeypatching is needed here, because these two tests aren't pinning
  *which* double-booking layer catches the conflict (that's already fully
  covered by `test_booking_create_appointment.py`) — they only need
  `create_appointment` to raise *some* `DomainError`, so the orchestrator's
  own outer `transaction.atomic()` can be proven to roll back everything
  behind it. Caught as the `core.exceptions.DomainError` base, not a
  specific subclass, for exactly that reason.

All fixed `now`/`start_datetime` values are tz-aware UTC literals, never
`timezone.now()` — same discipline as § Stage 6.F/7.C decisions.
"""

import datetime as dt
import hashlib

import pytest

from accounts.models import Customer
from accounts.services import get_or_create_guest_customer
from booking.guest_tokens import GUEST_TOKEN_VALIDITY, validate_guest_token
from booking.models import AppointmentStatus, GuestAccessToken
from booking.services import create_guest_appointment
from core.exceptions import DomainError
from core.tenancy import tenant_context
from tests.conftest import make_appointment, make_working_hours

UTC = dt.UTC

# Same numbers as test_booking_create_appointment.py: a Monday with
# 09:00-18:00 local (Europe/Kyiv, the `salon` fixture's default) working
# hours == 06:00-15:00 UTC, so 06:00 UTC is always the first on-grid
# candidate for the `service` fixture's duration/buffer.
MONDAY = dt.date(2026, 8, 17)
FIRST_CANDIDATE = dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC)

# Well within the default min_lead_time_hours=3 / max_advance_days=60
# window for FIRST_CANDIDATE.
SAFE_NOW = dt.datetime(2026, 8, 16, 0, 0, tzinfo=UTC)


def _working_hours(salon, specialist, *, day: dt.date = MONDAY) -> None:
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=day.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )


# --- 1. Brand-new email ------------------------------------------------


def test_create_guest_appointment_brand_new_email_creates_customer(salon, specialist, service):
    _working_hours(salon, specialist)
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
        customer = Customer.objects.get(pk=appt.customer_id)
    assert customer.name == "Alice"
    assert customer.email == "alice@example.com"
    assert customer.phone == "+10000000000"


# --- 2. Returning guest, identical details ------------------------------


def test_create_guest_appointment_returning_guest_identical_details_reuses_row(
    salon, specialist, service
):
    _working_hours(salon, specialist)
    with tenant_context(salon.id):
        existing = Customer.objects.create(
            salon=salon, name="Alice", email="alice@example.com", phone="+10000000000"
        )
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
        assert appt.customer_id == existing.pk
        assert Customer.objects.filter(email="alice@example.com").count() == 1


# --- 3. Overwrite semantics, direct test --------------------------------


def test_get_or_create_guest_customer_overwrites_name_and_phone_on_returning_guest(salon):
    with tenant_context(salon.id):
        first = get_or_create_guest_customer(
            salon=salon, name="Alice", email="alice@example.com", phone="+10000000000"
        )
        second = get_or_create_guest_customer(
            salon=salon, name="Alice Smith", email="alice@example.com", phone="+19999999999"
        )
        assert Customer.objects.filter(email="alice@example.com").count() == 1
        second.refresh_from_db()
    assert second.pk == first.pk
    assert second.name == "Alice Smith"
    assert second.phone == "+19999999999"


# --- 4. Overwrite semantics, end-to-end through the orchestrator --------


def test_create_guest_appointment_returning_guest_different_details_updates_customer(
    salon, specialist, service
):
    _working_hours(salon, specialist)
    with tenant_context(salon.id):
        existing = Customer.objects.create(
            salon=salon, name="Alice", email="alice@example.com", phone="+10000000000"
        )
        appt, _raw_token = create_guest_appointment(
            salon=salon,
            specialist=specialist,
            service=service,
            start_datetime=FIRST_CANDIDATE,
            now=SAFE_NOW,
            customer_name="Alice Smith",
            customer_email="alice@example.com",
            customer_phone="+19999999999",
        )
        assert appt.customer_id == existing.pk
        assert Customer.objects.filter(email="alice@example.com").count() == 1
        existing.refresh_from_db()
    assert existing.name == "Alice Smith"
    assert existing.phone == "+19999999999"


# --- 5. GuestAccessToken row ---------------------------------------------


def test_create_guest_appointment_creates_guest_access_token_row(salon, specialist, service):
    _working_hours(salon, specialist)
    with tenant_context(salon.id):
        appt, raw_token = create_guest_appointment(
            salon=salon,
            specialist=specialist,
            service=service,
            start_datetime=FIRST_CANDIDATE,
            now=SAFE_NOW,
            customer_name="Alice",
            customer_email="alice@example.com",
            customer_phone="+10000000000",
        )
        token_row = GuestAccessToken.objects.get(appointment_id=appt.id)
    assert token_row.appointment_id == appt.id
    assert token_row.token_hash == hashlib.sha256(raw_token.encode()).hexdigest()
    assert token_row.expires_at == appt.end_datetime + GUEST_TOKEN_VALIDITY
    assert token_row.salon_id == salon.id


# --- 6. Raw token round-trips through validate_guest_token ---------------


def test_create_guest_appointment_raw_token_round_trips_through_validate_guest_token(
    salon, specialist, service
):
    _working_hours(salon, specialist)
    with tenant_context(salon.id):
        appt, raw_token = create_guest_appointment(
            salon=salon,
            specialist=specialist,
            service=service,
            start_datetime=FIRST_CANDIDATE,
            now=SAFE_NOW,
            customer_name="Alice",
            customer_email="alice@example.com",
            customer_phone="+10000000000",
        )
        validated = validate_guest_token(raw_token)
    assert validated.appointment_id == appt.id


# --- 7. salon written explicitly -----------------------------------------


def test_create_guest_appointment_writes_salon_explicitly_on_customer_and_token(
    salon, specialist, service
):
    _working_hours(salon, specialist)
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
    # unscoped_objects bypasses the tenant-scoped manager's own salon_id
    # filter, so this actually proves the column was written, rather than
    # merely proving the row was findable under the expected tenant — same
    # reasoning as test_booking_create_appointment.py's own salon test.
    customer_row = Customer.unscoped_objects.get(pk=appt.customer_id)
    token_row = GuestAccessToken.unscoped_objects.get(appointment_id=appt.id)
    assert customer_row.salon_id == salon.id
    assert token_row.salon_id == salon.id


# --- 8. Failure atomicity, returning guest --------------------------------


def test_create_guest_appointment_returning_guest_failure_leaves_customer_unchanged(
    salon, specialist, service
):
    _working_hours(salon, specialist)
    with tenant_context(salon.id):
        existing = Customer.objects.create(
            salon=salon, name="Alice", email="alice@example.com", phone="+10000000000"
        )
        make_appointment(
            salon=salon,
            customer=existing,
            specialist=specialist,
            service=service,
            start=FIRST_CANDIDATE,
            status=AppointmentStatus.CONFIRMED,
        )
        with pytest.raises(DomainError):
            create_guest_appointment(
                salon=salon,
                specialist=specialist,
                service=service,
                start_datetime=FIRST_CANDIDATE,
                now=SAFE_NOW,
                customer_name="Alice Smith",
                customer_email="alice@example.com",
                customer_phone="+19999999999",
            )
        existing.refresh_from_db()
        assert existing.name == "Alice"
        assert existing.phone == "+10000000000"
        assert GuestAccessToken.objects.filter(appointment__customer=existing).count() == 0


# --- 9. Failure atomicity, brand-new guest --------------------------------


def test_create_guest_appointment_brand_new_guest_failure_does_not_orphan_customer(
    salon, specialist, service
):
    _working_hours(salon, specialist)
    with tenant_context(salon.id):
        # Occupies the slot under a DIFFERENT customer, so create_appointment
        # fails for the guest under test without touching that customer.
        blocker = Customer.objects.create(
            salon=salon, name="Blocker", email="blocker@example.com", phone="+10000000001"
        )
        make_appointment(
            salon=salon,
            customer=blocker,
            specialist=specialist,
            service=service,
            start=FIRST_CANDIDATE,
            status=AppointmentStatus.CONFIRMED,
        )
        with pytest.raises(DomainError):
            create_guest_appointment(
                salon=salon,
                specialist=specialist,
                service=service,
                start_datetime=FIRST_CANDIDATE,
                now=SAFE_NOW,
                customer_name="Brand New",
                customer_email="brandnew@example.com",
                customer_phone="+10000000002",
            )
        assert not Customer.objects.filter(email="brandnew@example.com").exists()
