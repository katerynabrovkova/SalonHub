"""
Appointment times in the salon's time zone, cycle T1 (docs/DECISIONS.md §
"Appointment times in the salon's time zone", points 1 and 3): every
appointment time meant for display is ISO 8601 with the salon's own offset
(`salon.timezone`), like availability slots, never UTC `Z`. Only the
representation changes: parsing the string gives the stored instant.

Covered responses:
- GET appointments/mine/ (AppointmentAccountSerializer): start_datetime,
  end_datetime, refund_deadline.
- GET guest/appointments/<id>/ (AppointmentGuestSerializer): start_datetime,
  end_datetime.
- POST bookings/ and POST appointments/ (AppointmentCreatedSerializer under
  "appointment"): start_datetime, end_datetime.

Fixtures mirror test_booking_account_appointment_list.py (Account path),
test_guest_appointment_access.py (guest token and its frozen now),
test_booking_post_endpoint.py and test_booking_account_create.py (working
hours, frozen now, payload). Stored instants are fixed UTC literals; the
list endpoint and refund_deadline compare nothing with the real clock, so
they need no freeze. The guest token and the booking endpoints do compare
with now, so those tests freeze it (CLAUDE.md rule).

Europe/Kyiv is +03:00 in summer and +02:00 in winter; in 2026 it switches
to summer time on 29 March at 01:00 UTC (03:00 local becomes 04:00).
Asia/Tbilisi is +04:00 all year.
"""

import datetime as dt

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Account, AccountRole, Customer
from booking.guest_tokens import issue_guest_token
from booking.models import AppointmentStatus
from booking.services import REFUND_ELIGIBILITY_CUTOFF
from catalog.models import Service, ServiceCategory
from core.tenancy import tenant_context
from payments.models import Payment, PaymentStatus
from specialists.models import Specialist
from tests.conftest import make_appointment, make_working_hours

pytestmark = pytest.mark.django_db

UTC = dt.UTC
PASSWORD = "a-strong-passw0rd!"

# 12:15 in Kyiv in summer (+03:00), the case from the 21.09.2026 manual check.
SUMMER_START = dt.datetime(2026, 7, 15, 9, 15, tzinfo=UTC)
# 12:00 in Kyiv in winter (+02:00).
WINTER_START = dt.datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
# The 2026 Kyiv switch to summer time.
DST_SWITCH = dt.datetime(2026, 3, 29, 1, 0, tzinfo=UTC)

# Same booking-endpoint numbers as test_booking_post_endpoint.py and
# test_booking_account_create.py: Monday 09:00-18:00 Kyiv working hours, so
# 06:00 UTC (09:00+03:00) is the first on-grid candidate.
MONDAY = dt.date(2026, 8, 17)
FIRST_CANDIDATE = dt.datetime(2026, 8, 17, 6, 0, tzinfo=UTC)
SAFE_NOW = dt.datetime(2026, 8, 16, 0, 0, tzinfo=UTC)


@pytest.fixture
def client() -> APIClient:
    return APIClient()


@pytest.fixture
def customer_account(salon, customer):
    with tenant_context(salon.id):
        return Account.objects.create_account(
            salon=salon,
            email="alice-account@example.com",
            password=PASSWORD,
            role=AccountRole.CLIENT,
            customer=customer,
        )


def _mine_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/appointments/mine/"


def _guest_detail_url(salon, appointment) -> str:
    return f"/api/v1/salons/{salon.slug}/guest/appointments/{appointment.id}/"


def _guest_bookings_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/bookings/"


def _account_bookings_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/appointments/"


def _freeze_now(monkeypatch: pytest.MonkeyPatch, value: dt.datetime) -> None:
    monkeypatch.setattr(timezone, "now", lambda: value)


def _make_succeeded_payment(salon, appointment) -> Payment:
    with tenant_context(salon.id):
        return Payment.objects.create(
            salon=salon,
            appointment=appointment,
            amount="100.00",
            currency=salon.currency,
            status=PaymentStatus.SUCCEEDED,
            provider_reference_id=f"ref-{appointment.id}",
        )


def _assert_salon_time(value: str, *, expected: str, stored: dt.datetime) -> None:
    """The exact salon-offset string, and the same instant as the stored
    value once parsed (point 6: only the representation changes)."""
    assert value == expected
    assert dt.datetime.fromisoformat(value) == stored


def _only_row(client, salon, account) -> dict:
    client.force_authenticate(user=account)
    response = client.get(_mine_url(salon))
    assert response.status_code == 200
    (row,) = response.json()["results"]
    return row


# --- 1. appointments/mine/ in Kyiv, summer -----------------------------------


def test_mine_returns_start_and_end_in_the_kyiv_summer_offset(
    client, salon, customer, specialist, service, customer_account
):
    appointment = make_appointment(
        salon=salon, customer=customer, specialist=specialist, service=service, start=SUMMER_START
    )

    row = _only_row(client, salon, customer_account)

    _assert_salon_time(
        row["start_datetime"],
        expected="2026-07-15T12:15:00+03:00",
        stored=appointment.start_datetime,
    )
    _assert_salon_time(
        row["end_datetime"],
        expected="2026-07-15T13:15:00+03:00",
        stored=appointment.end_datetime,
    )


# --- 2. A salon in another zone ----------------------------------------------


def test_mine_follows_the_salons_own_time_zone_not_a_fixed_one(
    client, salon, customer, specialist, service, customer_account
):
    salon.timezone = "Asia/Tbilisi"
    salon.save(update_fields=["timezone"])
    appointment = make_appointment(
        salon=salon, customer=customer, specialist=specialist, service=service, start=SUMMER_START
    )

    row = _only_row(client, salon, customer_account)

    _assert_salon_time(
        row["start_datetime"],
        expected="2026-07-15T13:15:00+04:00",
        stored=appointment.start_datetime,
    )
    _assert_salon_time(
        row["end_datetime"],
        expected="2026-07-15T14:15:00+04:00",
        stored=appointment.end_datetime,
    )


def test_two_salons_in_one_database_each_get_their_own_offset(
    client, salon, other_salon, customer, specialist, service, customer_account
):
    """Kyiv and Tbilisi side by side, each with its own client account and an
    appointment at the same UTC instant. Each salon's URL returns its own
    offset and wall time, which catches an implementation that takes the
    zone from the wrong salon (a fixed one, the first one, the request's
    instead of the row's); reconfiguring a single salon cannot."""
    other_salon.timezone = "Asia/Tbilisi"
    other_salon.save(update_fields=["timezone"])
    assert salon.timezone == "Europe/Kyiv"  # guards the setup, not the code
    with tenant_context(other_salon.id):
        other_customer = Customer.objects.create(
            salon=other_salon, name="Nino", email="nino@example.com", phone="+20000000000"
        )
        other_account = Account.objects.create_account(
            salon=other_salon,
            email="nino-account@example.com",
            password=PASSWORD,
            role=AccountRole.CLIENT,
            customer=other_customer,
        )
        other_category = ServiceCategory.objects.create(salon=other_salon, name={"en": "Hair"})
        other_service = Service.objects.create(
            salon=other_salon,
            category=other_category,
            name={"en": "Cut"},
            duration_minutes=60,
            price="200.00",
            buffer_minutes=0,
        )
        other_specialist = Specialist.objects.create(salon=other_salon, name={"en": "Sam"})
    kyiv_appointment = make_appointment(
        salon=salon, customer=customer, specialist=specialist, service=service, start=SUMMER_START
    )
    tbilisi_appointment = make_appointment(
        salon=other_salon,
        customer=other_customer,
        specialist=other_specialist,
        service=other_service,
        start=SUMMER_START,
    )

    kyiv_row = _only_row(client, salon, customer_account)
    tbilisi_row = _only_row(client, other_salon, other_account)

    assert kyiv_row["id"] == kyiv_appointment.id
    assert tbilisi_row["id"] == tbilisi_appointment.id
    _assert_salon_time(
        kyiv_row["start_datetime"],
        expected="2026-07-15T12:15:00+03:00",
        stored=SUMMER_START,
    )
    _assert_salon_time(
        kyiv_row["end_datetime"],
        expected="2026-07-15T13:15:00+03:00",
        stored=kyiv_appointment.end_datetime,
    )
    _assert_salon_time(
        tbilisi_row["start_datetime"],
        expected="2026-07-15T13:15:00+04:00",
        stored=SUMMER_START,
    )
    _assert_salon_time(
        tbilisi_row["end_datetime"],
        expected="2026-07-15T14:15:00+04:00",
        stored=tbilisi_appointment.end_datetime,
    )


# --- 3. DST ------------------------------------------------------------------


def test_mine_uses_the_kyiv_winter_offset_in_winter(
    client, salon, customer, specialist, service, customer_account
):
    appointment = make_appointment(
        salon=salon, customer=customer, specialist=specialist, service=service, start=WINTER_START
    )

    row = _only_row(client, salon, customer_account)

    _assert_salon_time(
        row["start_datetime"],
        expected="2026-01-15T12:00:00+02:00",
        stored=appointment.start_datetime,
    )
    _assert_salon_time(
        row["end_datetime"],
        expected="2026-01-15T13:00:00+02:00",
        stored=appointment.end_datetime,
    )


def test_mine_gives_each_time_its_own_offset_across_the_dst_switch(
    client, salon, customer, specialist, service, customer_account
):
    """Starts 30 minutes before the 2026 switch and ends 30 minutes after it
    (the `service` fixture lasts 60 minutes): the start carries +02:00 and
    the end +03:00. A single offset for the whole row would get one wrong."""
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=DST_SWITCH - dt.timedelta(minutes=30),
    )

    row = _only_row(client, salon, customer_account)

    _assert_salon_time(
        row["start_datetime"],
        expected="2026-03-29T02:30:00+02:00",
        stored=appointment.start_datetime,
    )
    _assert_salon_time(
        row["end_datetime"],
        expected="2026-03-29T04:30:00+03:00",
        stored=appointment.end_datetime,
    )


# --- 4. refund_deadline ------------------------------------------------------


def test_mine_returns_refund_deadline_in_the_salon_offset(
    client, salon, customer, specialist, service, customer_account
):
    appointment = make_appointment(
        salon=salon, customer=customer, specialist=specialist, service=service, start=SUMMER_START
    )
    _make_succeeded_payment(salon, appointment)

    row = _only_row(client, salon, customer_account)

    deadline = SUMMER_START - REFUND_ELIGIBILITY_CUTOFF
    assert row["refund_deadline"].endswith("+03:00")
    assert dt.datetime.fromisoformat(row["refund_deadline"]) == deadline
    # Salon wall time: the deadline is the start's own wall time
    # REFUND_ELIGIBILITY_CUTOFF earlier, both in summer.
    local_start = dt.datetime.fromisoformat(row["start_datetime"])
    local_deadline = dt.datetime.fromisoformat(row["refund_deadline"])
    assert local_deadline.utcoffset() == local_start.utcoffset() == dt.timedelta(hours=3)


def test_refund_deadline_and_start_carry_their_own_offsets_across_the_dst_switch(
    client, salon, customer, specialist, service, customer_account
):
    """The start is after the 2026 switch (+03:00) and the deadline, a
    REFUND_ELIGIBILITY_CUTOFF earlier, before it (+02:00)."""
    start = DST_SWITCH + dt.timedelta(hours=1)
    deadline = start - REFUND_ELIGIBILITY_CUTOFF
    assert deadline < DST_SWITCH < start  # guards the setup, not the code
    appointment = make_appointment(
        salon=salon, customer=customer, specialist=specialist, service=service, start=start
    )
    _make_succeeded_payment(salon, appointment)

    row = _only_row(client, salon, customer_account)

    assert row["start_datetime"].endswith("+03:00")
    assert dt.datetime.fromisoformat(row["start_datetime"]) == start
    assert row["refund_deadline"].endswith("+02:00")
    assert dt.datetime.fromisoformat(row["refund_deadline"]) == deadline


def test_refund_deadline_stays_null_when_nothing_is_refundable(
    client, salon, customer, specialist, service, customer_account
):
    make_appointment(
        salon=salon, customer=customer, specialist=specialist, service=service, start=SUMMER_START
    )

    row = _only_row(client, salon, customer_account)

    assert row["refund_deadline"] is None


# --- 5a. Guest appointment endpoint ------------------------------------------


def test_guest_appointment_detail_returns_start_and_end_in_the_salon_offset(
    monkeypatch, client, salon, customer, specialist, service
):
    appointment = make_appointment(
        salon=salon, customer=customer, specialist=specialist, service=service, start=SUMMER_START
    )
    with tenant_context(salon.id):
        raw_token, _row = issue_guest_token(appointment)
    # validate_guest_token compares the token's expiry with the real now.
    _freeze_now(monkeypatch, SUMMER_START)

    response = client.get(_guest_detail_url(salon, appointment), HTTP_X_GUEST_TOKEN=raw_token)

    assert response.status_code == 200
    body = response.json()
    _assert_salon_time(
        body["start_datetime"],
        expected="2026-07-15T12:15:00+03:00",
        stored=appointment.start_datetime,
    )
    _assert_salon_time(
        body["end_datetime"],
        expected="2026-07-15T13:15:00+03:00",
        stored=appointment.end_datetime,
    )


# --- 5b. Created responses ---------------------------------------------------


def _working_hours(salon, specialist) -> None:
    make_working_hours(
        salon=salon,
        specialist=specialist,
        day_of_week=MONDAY.weekday(),
        start_time=dt.time(9, 0),
        end_time=dt.time(18, 0),
    )


def _assert_created_times(body: dict) -> None:
    appointment = body["appointment"]
    _assert_salon_time(
        appointment["start_datetime"],
        expected="2026-08-17T09:00:00+03:00",
        stored=FIRST_CANDIDATE,
    )
    _assert_salon_time(
        appointment["end_datetime"],
        expected="2026-08-17T10:00:00+03:00",
        stored=FIRST_CANDIDATE + dt.timedelta(minutes=60),
    )


def test_guest_booking_created_response_is_in_the_salon_offset(
    monkeypatch, client, salon, specialist, service
):
    _working_hours(salon, specialist)
    _freeze_now(monkeypatch, SAFE_NOW)

    response = client.post(
        _guest_bookings_url(salon),
        {
            "specialist": specialist.id,
            "service": service.id,
            "start_datetime": FIRST_CANDIDATE.isoformat(),
            "customer_name": "Alice",
            "customer_email": "alice@example.com",
            "customer_phone": "+10000000000",
        },
        format="json",
    )

    assert response.status_code == 201
    _assert_created_times(response.json())


def test_account_booking_created_response_is_in_the_salon_offset(
    monkeypatch, client, salon, customer, specialist, service, customer_account
):
    _working_hours(salon, specialist)
    _freeze_now(monkeypatch, SAFE_NOW)
    customer_account.email_verified_at = SAFE_NOW
    customer_account.save(update_fields=["email_verified_at"])
    client.force_authenticate(user=customer_account)

    response = client.post(
        _account_bookings_url(salon),
        {
            "specialist": specialist.id,
            "service": service.id,
            "start_datetime": FIRST_CANDIDATE.isoformat(),
        },
        format="json",
    )

    assert response.status_code == 201
    _assert_created_times(response.json())


# --- 7. Query count ----------------------------------------------------------


def test_salon_offset_times_add_no_query_per_appointment(
    client, salon, customer, specialist, service, customer_account
):
    """Rendering times in salon.timezone must not read the salon once per
    row: the list costs the same number of queries for 1 and for 6
    appointments, each with a SUCCEEDED payment so refund_deadline is
    rendered too."""
    first = make_appointment(
        salon=salon, customer=customer, specialist=specialist, service=service, start=SUMMER_START
    )
    _make_succeeded_payment(salon, first)
    client.force_authenticate(user=customer_account)

    with CaptureQueriesContext(connection) as one_row:
        response = client.get(_mine_url(salon))
    assert response.status_code == 200
    assert response.json()["count"] == 1

    for days in range(1, 6):
        appointment = make_appointment(
            salon=salon,
            customer=customer,
            specialist=specialist,
            service=service,
            start=SUMMER_START + dt.timedelta(days=days),
            status=AppointmentStatus.CONFIRMED,
        )
        _make_succeeded_payment(salon, appointment)

    with CaptureQueriesContext(connection) as six_rows:
        response = client.get(_mine_url(salon))
    assert response.status_code == 200
    assert response.json()["count"] == 6

    assert len(six_rows.captured_queries) == len(one_row.captured_queries)
