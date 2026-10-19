"""
Price and prepayment on dashboard cards, backend (docs/DECISIONS.md § "Price
and prepayment on dashboard cards", points 1 and 6): GET appointments/mine/
gains three read-only fields.

- `deposit_amount`: `Payment.amount` when a Payment exists; otherwise
  computed from the appointment's own price/percentage snapshots by the
  helper payments uses. `null` whenever the deposit is 0.
- `currency`: `Payment.currency` when a Payment exists, otherwise the
  salon's `Salon.currency`.
- `deposit_outcome`: what happened to a paid deposit ("refunded",
  "refund_processing", "withheld", or null), read from the Payment's status,
  `Payment.refund_due` and the appointment's status.

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
    salon,
    appointment,
    *,
    amount: str,
    currency: str,
    status: str = PaymentStatus.PENDING,
    refund_due: bool = False,
) -> Payment:
    with tenant_context(salon.id):
        return Payment.objects.create(
            salon=salon,
            appointment=appointment,
            amount=Decimal(amount),
            currency=currency,
            status=status,
            provider_reference_id=f"ref-{appointment.id}",
            refund_due=refund_due,
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


# --- 7. deposit_outcome ---------------------------------------------------------
#
# Point 1: "refunded" (Payment REFUNDED); "refund_processing" (REFUND_PENDING,
# or SUCCEEDED with refund_due); "withheld" (SUCCEEDED, not refund_due,
# appointment CANCELLED); null otherwise. Every read is row["deposit_outcome"],
# never .get(), so a missing key fails even where the expected value is null.


def _outcome_row(
    client,
    salon,
    customer,
    specialist,
    service,
    account,
    *,
    appointment_status: str,
    payment_status: str | None,
    refund_due: bool = False,
) -> dict:
    """One appointment, with a Payment unless payment_status is None."""
    appointment = _appointment(
        salon,
        customer,
        specialist,
        service,
        price="120.00",
        percentage="20.00",
        status=appointment_status,
    )
    if payment_status is not None:
        _make_payment(
            salon,
            appointment,
            amount="24.00",
            currency=salon.currency,
            status=payment_status,
            refund_due=refund_due,
        )
    return _only_row(client, salon, account)


@pytest.mark.parametrize(
    ("appointment_status", "refund_due"),
    [
        (AppointmentStatus.CANCELLED, True),
        # refund_due False: a row from before the field existed. REFUNDED
        # alone decides the value.
        (AppointmentStatus.EXPIRED, False),
    ],
    ids=["cancelled", "expired"],
)
def test_deposit_outcome_is_refunded_for_a_refunded_payment(
    client,
    salon,
    customer,
    specialist,
    service,
    customer_account,
    appointment_status,
    refund_due,
):
    row = _outcome_row(
        client,
        salon,
        customer,
        specialist,
        service,
        customer_account,
        appointment_status=appointment_status,
        payment_status=PaymentStatus.REFUNDED,
        refund_due=refund_due,
    )

    assert row["deposit_outcome"] == "refunded"


def test_deposit_outcome_is_refund_processing_for_a_refund_pending_payment(
    client, salon, customer, specialist, service, customer_account
):
    """refund_due False, as on a row from before the field existed:
    REFUND_PENDING alone decides the value."""
    row = _outcome_row(
        client,
        salon,
        customer,
        specialist,
        service,
        customer_account,
        appointment_status=AppointmentStatus.CANCELLED,
        payment_status=PaymentStatus.REFUND_PENDING,
    )

    assert row["deposit_outcome"] == "refund_processing"


@pytest.mark.parametrize(
    "appointment_status",
    [AppointmentStatus.CANCELLED, AppointmentStatus.EXPIRED],
    ids=["cancelled", "expired"],
)
def test_deposit_outcome_is_refund_processing_for_a_succeeded_payment_with_refund_due(
    client, salon, customer, specialist, service, customer_account, appointment_status
):
    """The refund is owed but not started yet (the retry sweep's rows).
    payment_status alone says "succeeded"; only refund_due tells this apart
    from "withheld"."""
    row = _outcome_row(
        client,
        salon,
        customer,
        specialist,
        service,
        customer_account,
        appointment_status=appointment_status,
        payment_status=PaymentStatus.SUCCEEDED,
        refund_due=True,
    )

    assert row["deposit_outcome"] == "refund_processing"


def test_deposit_outcome_is_withheld_for_a_cancelled_appointment_with_a_kept_deposit(
    client, salon, customer, specialist, service, customer_account
):
    row = _outcome_row(
        client,
        salon,
        customer,
        specialist,
        service,
        customer_account,
        appointment_status=AppointmentStatus.CANCELLED,
        payment_status=PaymentStatus.SUCCEEDED,
        refund_due=False,
    )

    assert row["deposit_outcome"] == "withheld"


@pytest.mark.parametrize(
    "appointment_status",
    [AppointmentStatus.CONFIRMED, AppointmentStatus.COMPLETED, AppointmentStatus.EXPIRED],
    ids=["confirmed", "completed", "expired"],
)
def test_deposit_outcome_is_null_for_a_succeeded_payment_on_a_non_cancelled_appointment(
    client, salon, customer, specialist, service, customer_account, appointment_status
):
    """The value "withheld" needs the appointment to be CANCELLED. EXPIRED is included
    because an implementation that treats expired like cancelled would turn
    it into "withheld", which point 1 does not allow."""
    row = _outcome_row(
        client,
        salon,
        customer,
        specialist,
        service,
        customer_account,
        appointment_status=appointment_status,
        payment_status=PaymentStatus.SUCCEEDED,
        refund_due=False,
    )

    assert row["deposit_outcome"] is None


@pytest.mark.parametrize(
    "payment_status",
    [None, PaymentStatus.PENDING, PaymentStatus.FAILED],
    ids=["no_payment", "pending", "failed"],
)
def test_deposit_outcome_is_null_when_no_deposit_was_collected(
    client, salon, customer, specialist, service, customer_account, payment_status
):
    """On a CANCELLED appointment, so an implementation that decides
    "withheld" from the appointment status alone fails here."""
    row = _outcome_row(
        client,
        salon,
        customer,
        specialist,
        service,
        customer_account,
        appointment_status=AppointmentStatus.CANCELLED,
        payment_status=payment_status,
    )

    assert row["deposit_outcome"] is None


def test_deposit_outcome_is_per_appointment_for_two_cancelled_appointments_of_one_client(
    client, salon, customer, specialist, service, customer_account
):
    """Same salon, two objects (CLAUDE.md's two-level isolation rule): two
    cancelled appointments of the same client. The first (lower pk) kept its
    deposit, the second was refunded. Each row must carry its own value, not
    the other appointment's."""
    first = _appointment(
        salon,
        customer,
        specialist,
        service,
        price="120.00",
        percentage="20.00",
        status=AppointmentStatus.CANCELLED,
    )
    second = _appointment(
        salon,
        customer,
        specialist,
        service,
        price="120.00",
        percentage="20.00",
        status=AppointmentStatus.CANCELLED,
        start=START + dt.timedelta(days=1),
    )
    _make_payment(
        salon,
        first,
        amount="24.00",
        currency=salon.currency,
        status=PaymentStatus.SUCCEEDED,
        refund_due=False,
    )
    _make_payment(
        salon,
        second,
        amount="24.00",
        currency=salon.currency,
        status=PaymentStatus.REFUNDED,
        refund_due=True,
    )
    assert first.pk < second.pk  # guards the setup, not the code

    rows = {row["id"]: row for row in _rows(client, salon, customer_account)}

    assert rows[first.id]["deposit_outcome"] == "withheld"
    assert rows[second.id]["deposit_outcome"] == "refunded"


# --- 8. No N+1 ----------------------------------------------------------------


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
        assert "deposit_outcome" in row
    assert len(three_rows_ctx.captured_queries) == one_row_query_count
