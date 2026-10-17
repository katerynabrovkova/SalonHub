"""
Price and prepayment on dashboard cards, backend (docs/DECISIONS.md § "Price
and prepayment on dashboard cards", points 1 and 6): GET appointments/mine/
gains two read-only fields.

- `deposit_amount`: `Payment.amount` when a Payment exists; otherwise
  computed from the appointment's own price/percentage snapshots by the
  helper payments uses. `null` whenever the deposit is 0.
- `currency`: `Payment.currency` when a Payment exists, otherwise the
  salon's `Salon.currency`.

Fixtures mirror test_booking_account_appointment_list.py (Account path,
`make_appointment`) and test_booking_salon_time_zone.py (two salons, each
with its own client account). The price/percentage snapshots are set on the
stored row after `make_appointment`, since that is what the serializer
reads. Nothing here compares with the real clock.
"""

import datetime as dt
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from accounts.models import Account, AccountRole, Customer
from booking.models import Appointment, AppointmentStatus
from catalog.models import Service, ServiceCategory
from core.tenancy import tenant_context
from payments.models import Payment, PaymentStatus
from specialists.models import Specialist
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

START = dt.datetime(2026, 9, 1, 10, 0, tzinfo=dt.UTC)
PASSWORD = "a-strong-passw0rd!"


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


def _appointment(
    salon,
    customer,
    specialist,
    service,
    *,
    price: str,
    percentage: str,
    status: str = AppointmentStatus.PENDING_PAYMENT,
    start: dt.datetime = START,
) -> Appointment:
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=start,
        status=status,
    )
    with tenant_context(salon.id):
        Appointment.objects.filter(pk=appointment.pk).update(
            service_price_at_booking=Decimal(price),
            deposit_percentage_at_booking=Decimal(percentage),
        )
    return appointment


def _make_payment(
    salon, appointment, *, amount: str, currency: str, status: str = PaymentStatus.PENDING
) -> Payment:
    with tenant_context(salon.id):
        return Payment.objects.create(
            salon=salon,
            appointment=appointment,
            amount=Decimal(amount),
            currency=currency,
            status=status,
            provider_reference_id=f"ref-{appointment.id}",
        )


def _rows(client, salon, account) -> list[dict]:
    client.force_authenticate(user=account)
    response = client.get(_mine_url(salon))
    assert response.status_code == 200
    return response.json()["results"]


def _only_row(client, salon, account) -> dict:
    (row,) = _rows(client, salon, account)
    return row


# --- 1. No Payment: computed deposit, salon currency ---------------------------


def test_pending_appointment_without_payment_gets_computed_deposit_and_salon_currency(
    client, salon, customer, specialist, service, customer_account
):
    # Not the fixture's UAH, so a hardcoded "UAH" cannot pass.
    salon.currency = "EUR"
    salon.save(update_fields=["currency"])
    _appointment(salon, customer, specialist, service, price="120.00", percentage="20.00")

    row = _only_row(client, salon, customer_account)

    assert row["deposit_amount"] == "24.00"
    assert row["currency"] == "EUR"


# --- 2. With a Payment: deposit_amount is Payment.amount ----------------------


def test_deposit_amount_comes_from_the_payment_when_one_exists(
    client, salon, customer, specialist, service, customer_account
):
    """25.00, deliberately not the 24.00 the snapshots compute to, so only
    an implementation that reads Payment.amount passes."""
    appointment = _appointment(
        salon, customer, specialist, service, price="120.00", percentage="20.00"
    )
    _make_payment(salon, appointment, amount="25.00", currency=salon.currency)

    row = _only_row(client, salon, customer_account)

    assert row["deposit_amount"] == "25.00"


# --- 3. With a Payment: currency is Payment.currency --------------------------


def test_currency_comes_from_the_payment_after_the_salon_changes_its_currency(
    client, salon, customer, specialist, service, customer_account
):
    appointment = _appointment(
        salon, customer, specialist, service, price="120.00", percentage="20.00"
    )
    _make_payment(
        salon,
        appointment,
        amount="24.00",
        currency="UAH",
        status=PaymentStatus.SUCCEEDED,
    )
    salon.currency = "EUR"
    salon.save(update_fields=["currency"])

    row = _only_row(client, salon, customer_account)

    assert row["currency"] == "UAH"


# --- 4. deposit_amount is null whenever the deposit is 0 ----------------------


@pytest.mark.parametrize(
    ("price", "percentage"),
    [
        ("120.00", "0.00"),
        # 20% of 2.00 is 0.40, which rounds half-up to whole units as 0.
        ("2.00", "20.00"),
    ],
)
def test_deposit_amount_is_null_when_the_computed_deposit_is_zero(
    client, salon, customer, specialist, service, customer_account, price, percentage
):
    _appointment(salon, customer, specialist, service, price=price, percentage=percentage)

    row = _only_row(client, salon, customer_account)

    assert row["deposit_amount"] is None


def test_deposit_amount_is_null_when_the_payment_amount_is_zero(
    client, salon, customer, specialist, service, customer_account
):
    """A 0% booking still creates a 0.00 Payment today (the entry's "0%
    deposit booking flow: not decided here"); point 1's "null whenever the
    deposit is 0" covers it too."""
    appointment = _appointment(
        salon, customer, specialist, service, price="120.00", percentage="0.00"
    )
    _make_payment(salon, appointment, amount="0.00", currency=salon.currency)

    row = _only_row(client, salon, customer_account)

    assert row["deposit_amount"] is None


# --- 5. amount_due_at_visit is unchanged (guard) ------------------------------


def test_amount_due_at_visit_is_unchanged_by_the_new_fields(
    client, salon, customer, specialist, service, customer_account
):
    """Existing behaviour, kept as a guard: price minus the succeeded
    payment's amount, for a confirmed appointment."""
    appointment = _appointment(
        salon,
        customer,
        specialist,
        service,
        price="120.00",
        percentage="20.00",
        status=AppointmentStatus.CONFIRMED,
    )
    _make_payment(
        salon,
        appointment,
        amount="24.00",
        currency=salon.currency,
        status=PaymentStatus.SUCCEEDED,
    )

    row = _only_row(client, salon, customer_account)

    assert row["amount_due_at_visit"] == "96.00"


# --- 6. Tenant isolation: two salons, two currencies --------------------------


def test_two_salons_with_different_currencies_each_get_their_own_amount_and_currency(
    client, salon, other_salon, customer, specialist, service, customer_account
):
    """UAH and EUR side by side, each with its own client account. Salon A's
    appointment has no Payment (computed deposit, salon currency); salon B's
    has one (Payment amount and currency). Different percentages and
    amounts, so a value taken from the wrong salon or payment cannot
    match."""
    other_salon.currency = "EUR"
    other_salon.save(update_fields=["currency"])
    assert salon.currency == "UAH"  # guards the setup, not the code
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
    uah_appointment = _appointment(
        salon, customer, specialist, service, price="120.00", percentage="20.00"
    )
    eur_appointment = _appointment(
        other_salon,
        other_customer,
        other_specialist,
        other_service,
        price="200.00",
        percentage="30.00",
    )
    _make_payment(other_salon, eur_appointment, amount="60.00", currency="EUR")

    uah_row = _only_row(client, salon, customer_account)
    eur_row = _only_row(client, other_salon, other_account)

    assert uah_row["id"] == uah_appointment.id
    assert uah_row["deposit_amount"] == "24.00"
    assert uah_row["currency"] == "UAH"
    assert eur_row["id"] == eur_appointment.id
    assert eur_row["deposit_amount"] == "60.00"
    assert eur_row["currency"] == "EUR"


# --- 7. No N+1 ----------------------------------------------------------------


def test_new_fields_add_no_query_per_row(
    client, salon, customer, specialist, service, customer_account
):
    """One row without a Payment, then three rows (two without, one with),
    so an extra query on either path shows up as a different count. The
    key check keeps this test from passing only because the fields are not
    there yet."""
    _appointment(salon, customer, specialist, service, price="120.00", percentage="20.00")
    client.force_authenticate(user=customer_account)
    with CaptureQueriesContext(connection) as one_row_ctx:
        response = client.get(_mine_url(salon))
    assert response.status_code == 200
    assert response.json()["count"] == 1
    one_row_query_count = len(one_row_ctx.captured_queries)

    _appointment(
        salon,
        customer,
        specialist,
        service,
        price="120.00",
        percentage="20.00",
        start=START + dt.timedelta(days=1),
    )
    paid = _appointment(
        salon,
        customer,
        specialist,
        service,
        price="120.00",
        percentage="20.00",
        start=START + dt.timedelta(days=2),
    )
    _make_payment(salon, paid, amount="24.00", currency=salon.currency)

    with CaptureQueriesContext(connection) as three_rows_ctx:
        response = client.get(_mine_url(salon))
    assert response.status_code == 200
    rows = response.json()["results"]
    assert len(rows) == 3
    for row in rows:
        assert "deposit_amount" in row
        assert "currency" in row
    assert len(three_rows_ctx.captured_queries) == one_row_query_count
