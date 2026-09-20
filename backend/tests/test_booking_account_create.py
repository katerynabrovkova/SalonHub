"""
Stage 15 planning, item 5, Cycle B — POST appointments/ (docs/DECISIONS.md §
Stage 15 planning, item 5, "Cycle B — account booking endpoint contract,
decided 20.09.2026", plus the Refined/Extended/Corrected 19.09.2026-20.09.2026
entries above it): the authenticated Account books an appointment directly,
without the guest contact-form/token flow.

    POST /api/v1/salons/<slug>/appointments/

Written before the endpoint exists at all -- the URL is built as a literal
path (mirroring test_booking_account_appointment_list.py's `_mine_url` and
test_booking_account_appointment_cancel.py's `_cancel_url`), never
`reverse()` of a URL name that doesn't exist yet, so every test below fails
with a wrong status code (404/405), not NoReverseMatch or an import error.

Fixtures mirror:
- test_booking_account_appointment_list.py's Account path
  (`Account.objects.create_account(customer=...)` + `client.force_authenticate`)
  and its cross-tenant real-cookie-login test
  (`test_account_authenticated_for_its_own_salon_gets_rejected_when_querying_another_salons_url`).
- test_booking_post_endpoint.py's slot machinery: `_working_hours`,
  `_freeze_now`, and `_defeat_slot_validity_check` (the same monkeypatch of
  the bare module-global `booking_services.compute_candidate_start_times`
  § Stage 7.C decisions pins for exactly this interception) for the 409
  slot-conflict test.
- test_auth_me.py's pattern for producing a *verified* Account
  (`account.email_verified_at = timezone.now(); account.save(...)`) --
  `Account.objects.create_account` itself has no such kwarg.
"""

import datetime as dt

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

import booking.services as booking_services
from accounts.models import Account, AccountRole, Customer
from booking.models import Appointment, AppointmentStatus
from booking.services import create_account_appointment
from core.exceptions import EmailNotVerifiedError
from core.tenancy import tenant_context
from notifications.models import Notification, NotificationTrigger
from tests.conftest import make_appointment, make_working_hours

pytestmark = pytest.mark.django_db

UTC = dt.UTC

PASSWORD = "a-strong-passw0rd!"

# Same numbers as test_booking_post_endpoint.py / test_booking_create_guest_appointment.py:
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


def _account_bookings_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/appointments/"


def _login_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/auth/login/"


def _working_hours(salon, specialist, *, day: dt.date = MONDAY) -> None:
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=day.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )


def _freeze_now(monkeypatch: pytest.MonkeyPatch, value: dt.datetime = SAFE_NOW) -> None:
    monkeypatch.setattr(timezone, "now", lambda: value)


def _defeat_slot_validity_check(monkeypatch, start_datetime: dt.datetime) -> None:
    """Same interception test_booking_post_endpoint.py uses: forces
    create_appointment's engine-level slot-validity check to report
    `start_datetime` as offered, regardless of real DB state, so a real
    conflicting row trips the application-level overlap re-check
    (SlotUnavailableError -> 409) instead of SlotNotOfferedError (400)."""

    def _fake_compute_candidate_start_times(*args, **kwargs):
        return [start_datetime]

    monkeypatch.setattr(
        booking_services, "compute_candidate_start_times", _fake_compute_candidate_start_times
    )


def _payload(
    specialist,
    service,
    *,
    start_datetime: dt.datetime = FIRST_CANDIDATE,
    customer_name: str | None = "Alice",
    customer_phone: str | None = "+10000000000",
    customer_email: str | None = None,
) -> dict:
    data = {
        "specialist": specialist.id,
        "service": service.id,
        "start_datetime": start_datetime.isoformat(),
    }
    if customer_name is not None:
        data["customer_name"] = customer_name
    if customer_phone is not None:
        data["customer_phone"] = customer_phone
    if customer_email is not None:
        data["customer_email"] = customer_email
    return data


def _verified_account(salon, *, email: str, customer: Customer | None = None) -> Account:
    with tenant_context(salon.id):
        account = Account.objects.create_account(
            salon=salon,
            email=email,
            password=PASSWORD,
            role=AccountRole.CLIENT,
            customer=customer,
        )
        account.email_verified_at = timezone.now()
        account.save(update_fields=["email_verified_at"])
        return account


def _unverified_account(salon, *, email: str) -> Account:
    with tenant_context(salon.id):
        return Account.objects.create_account(
            salon=salon,
            email=email,
            password=PASSWORD,
            role=AccountRole.CLIENT,
        )


# --- 1. Unauthenticated -----------------------------------------------------


def test_unauthenticated_request_returns_401(client, salon, specialist, service):
    response = client.post(
        _account_bookings_url(salon), _payload(specialist, service), format="json"
    )

    assert response.status_code == 401


# --- 2/3. Unverified Account is rejected before anything else --------------


def test_unverified_account_returns_403_email_not_verified(client, salon, specialist, service):
    account = _unverified_account(salon, email="unverified@example.com")
    client.force_authenticate(user=account)

    response = client.post(
        _account_bookings_url(salon), _payload(specialist, service), format="json"
    )

    assert response.status_code == 403
    assert response.data["error"]["code"] == "email_not_verified"


def test_unverified_account_invalid_body_still_gets_the_same_403_regardless_of_existing_customer(
    client, salon, specialist, service
):
    """The 403 must fire before body validation (docs/DECISIONS.md's "order
    of checks" bullet), and must be identical whether or not a guest
    Customer with that email already exists -- otherwise the response shape
    itself would leak whether the email is taken."""
    with tenant_context(salon.id):
        Customer.objects.create(
            salon=salon,
            name="Existing Guest",
            email="taken@example.com",
            phone="+19999999999",
        )
    account_with_existing_customer = _unverified_account(salon, email="taken@example.com")
    account_without_existing_customer = _unverified_account(salon, email="not-taken@example.com")

    invalid_payload = _payload(specialist, service, customer_name=None, customer_phone=None)

    client.force_authenticate(user=account_with_existing_customer)
    response_taken = client.post(_account_bookings_url(salon), invalid_payload, format="json")

    client.force_authenticate(user=account_without_existing_customer)
    response_not_taken = client.post(_account_bookings_url(salon), invalid_payload, format="json")

    assert response_taken.status_code == 403
    assert response_not_taken.status_code == 403
    assert response_taken.data == response_not_taken.data
    assert response_taken.data["error"]["code"] == "email_not_verified"


# --- 4. Verified Account, already-linked Customer ---------------------------


def test_verified_account_with_linked_customer_books_without_a_guest_token(
    client, monkeypatch, salon, specialist, service, customer
):
    _working_hours(salon, specialist)
    _freeze_now(monkeypatch)
    account = _verified_account(salon, email="linked@example.com", customer=customer)
    client.force_authenticate(user=account)

    response = client.post(
        _account_bookings_url(salon),
        _payload(specialist, service, customer_name="Someone Else", customer_phone="+19999999999"),
        format="json",
    )

    assert response.status_code == 201
    assert "appointment" in response.data
    assert "guest_token" not in response.data
    with tenant_context(salon.id):
        appt = Appointment.objects.get(pk=response.data["appointment"]["id"])
        customer.refresh_from_db()
    assert appt.customer_id == customer.id
    # The payload's customer_name/customer_phone must never overwrite an
    # already-linked Customer's own profile (docs/DECISIONS.md's Cycle B
    # request contract).
    assert customer.name != "Someone Else"
    assert customer.phone != "+19999999999"


# --- 5. Verified Account, no linked Customer, brand-new email ---------------


def test_verified_account_with_no_linked_customer_creates_one_from_account_email(
    client, monkeypatch, salon, specialist, service
):
    _working_hours(salon, specialist)
    _freeze_now(monkeypatch)
    account = _verified_account(salon, email="brand-new@example.com")
    client.force_authenticate(user=account)

    response = client.post(
        _account_bookings_url(salon),
        _payload(specialist, service, customer_name="Bob", customer_phone="+10000000002"),
        format="json",
    )

    assert response.status_code == 201
    with tenant_context(salon.id):
        new_customer = Customer.objects.get(salon=salon, email="brand-new@example.com")
        account.refresh_from_db()
    assert account.customer_id == new_customer.id
    assert new_customer.name == "Bob"
    assert new_customer.phone == "+10000000002"


# --- 6. Verified Account, no linked Customer, existing guest Customer ------


def test_verified_account_with_no_linked_customer_reuses_an_existing_guest_customer(
    client, monkeypatch, salon, specialist, service
):
    _working_hours(salon, specialist)
    _freeze_now(monkeypatch)
    with tenant_context(salon.id):
        existing_guest_customer = Customer.objects.create(
            salon=salon, name="Guest Alice", email="reused@example.com", phone="+10000000003"
        )
    account = _verified_account(salon, email="reused@example.com")
    client.force_authenticate(user=account)

    response = client.post(
        _account_bookings_url(salon),
        _payload(specialist, service, customer_name="Ignored Name", customer_phone="+10000000009"),
        format="json",
    )

    assert response.status_code == 201
    with tenant_context(salon.id):
        account.refresh_from_db()
        matching_customers = list(Customer.objects.filter(salon=salon, email="reused@example.com"))
    assert account.customer_id == existing_guest_customer.id
    assert len(matching_customers) == 1


# --- 7. Verified Account, no linked Customer, missing name/phone -----------


def test_verified_account_with_no_linked_customer_missing_fields_returns_400(
    client, salon, specialist, service
):
    account = _verified_account(salon, email="missing-fields@example.com")
    client.force_authenticate(user=account)

    response = client.post(
        _account_bookings_url(salon),
        _payload(specialist, service, customer_name=None, customer_phone=None),
        format="json",
    )

    assert response.status_code == 400
    with tenant_context(salon.id):
        assert not Customer.objects.filter(salon=salon, email="missing-fields@example.com").exists()
        assert not Appointment.objects.exists()


# --- 8. A customer_email in the payload is ignored --------------------------


def test_payload_customer_email_is_ignored_customer_email_is_always_the_accounts(
    client, monkeypatch, salon, specialist, service
):
    _working_hours(salon, specialist)
    _freeze_now(monkeypatch)
    account = _verified_account(salon, email="real-account-email@example.com")
    client.force_authenticate(user=account)

    response = client.post(
        _account_bookings_url(salon),
        _payload(
            specialist,
            service,
            customer_name="Carol",
            customer_phone="+10000000004",
            customer_email="spoofed@example.com",
        ),
        format="json",
    )

    assert response.status_code == 201
    with tenant_context(salon.id):
        appt = Appointment.objects.get(pk=response.data["appointment"]["id"])
    assert appt.customer.email == "real-account-email@example.com"


# --- 9. Slot already taken --------------------------------------------------


def test_booking_an_already_taken_slot_returns_409(
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
    account = _verified_account(salon, email="conflict@example.com", customer=customer)
    client.force_authenticate(user=account)

    response = client.post(
        _account_bookings_url(salon), _payload(specialist, service), format="json"
    )

    assert response.status_code == 409
    assert response.data["error"]["code"] == "SLOT_NO_LONGER_AVAILABLE"


# --- 10. Cross-tenant: an Account from salon A hits salon B's URL ----------


def test_account_from_salon_a_is_rejected_at_salon_bs_url(
    client, salon, other_salon, specialist, service, customer
):
    account = _verified_account(salon, email="cross-tenant@example.com", customer=customer)
    client.post(
        _login_url(salon),
        {"email": account.email, "password": PASSWORD},
        format="json",
    )

    response = client.post(
        _account_bookings_url(other_salon), _payload(specialist, service), format="json"
    )

    assert response.status_code == 401
    with tenant_context(other_salon.id):
        assert not Appointment.objects.exists()
        assert not Customer.objects.filter(email="cross-tenant@example.com").exists()


# --- 11. BOOKING_CREATED notification carries booking_link_mode="account" --


def test_successful_booking_records_a_booking_created_notification_in_account_mode(
    client, monkeypatch, salon, specialist, service, customer
):
    _working_hours(salon, specialist)
    _freeze_now(monkeypatch)
    account = _verified_account(salon, email="notif@example.com", customer=customer)
    client.force_authenticate(user=account)

    response = client.post(
        _account_bookings_url(salon), _payload(specialist, service), format="json"
    )

    assert response.status_code == 201
    with tenant_context(salon.id):
        notification = Notification.objects.get(
            trigger_type=NotificationTrigger.BOOKING_CREATED,
            appointment_id=response.data["appointment"]["id"],
        )
    assert notification.booking_link_mode == "account"


# --- 12. Concurrency: the Account row is locked with SELECT ... FOR UPDATE -


def test_first_booking_locks_the_account_row_with_select_for_update(
    client, monkeypatch, salon, specialist, service
):
    _working_hours(salon, specialist)
    _freeze_now(monkeypatch)
    account = _verified_account(salon, email="locked@example.com")
    client.force_authenticate(user=account)

    with CaptureQueriesContext(connection) as ctx:
        response = client.post(
            _account_bookings_url(salon),
            _payload(specialist, service, customer_name="Dana", customer_phone="+10000000005"),
            format="json",
        )

    assert response.status_code == 201
    locking_queries = [
        q
        for q in ctx.captured_queries
        if "for update" in q["sql"].lower() and "accounts_account" in q["sql"].lower()
    ]
    assert locking_queries, (
        "expected a SELECT ... FOR UPDATE on accounts_account among the "
        f"captured queries, got: {[q['sql'] for q in ctx.captured_queries]}"
    )


# --- 13. Defense in depth: the service itself rejects an unverified Account -
#
# The view's own check (AccountBookingCreateView.post()) is the primary
# gate, but create_account_appointment must not trust that every future
# caller remembers it -- a second, independent check on the locked row
# itself, so a caller that reaches this service directly (bypassing the
# view) can never create a Customer/Appointment for an unverified Account.


def test_create_account_appointment_rejects_an_unverified_account_directly(
    salon, specialist, service
):
    _working_hours(salon, specialist)
    account = _unverified_account(salon, email="direct-call-unverified@example.com")

    with tenant_context(salon.id), pytest.raises(EmailNotVerifiedError):
        create_account_appointment(
            salon=salon,
            account=account,
            specialist=specialist,
            service=service,
            start_datetime=FIRST_CANDIDATE,
            now=SAFE_NOW,
            customer_name="Eve",
            customer_phone="+10000000006",
        )

    with tenant_context(salon.id):
        assert not Customer.objects.filter(email="direct-call-unverified@example.com").exists()
        assert not Appointment.objects.exists()
