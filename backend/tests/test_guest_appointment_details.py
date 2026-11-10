"""
Guest manage page data, backend (docs/DECISIONS.md § "Booking > Guest token",
"Guest detail and cancel responses"; register "In progress: guest manage
page", sub-step (a)).

GET guest/appointments/<id>/ and POST guest/appointments/<id>/cancel/ carry
the same read-only fields as GET appointments/mine/: `specialist` and
`service` as `{id, name}` resolved per `?lang=`, `service_price_at_booking`,
`deposit_amount`, `amount_due_at_visit`, `currency`, `deposit_outcome`,
`refund_deadline`; never the customer's name, email or phone.

Fixtures mirror test_guest_appointment_access.py (token issued with
`issue_guest_token`) and test_booking_account_appointment_price.py (price and
percentage snapshots set on the stored row, Payment rows built directly).
Every test freezes `timezone.now`: token expiry and the refund cutoff are both
compared with it, and START is a fixed date.
"""

import datetime as dt
import json
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Account, AccountRole, Customer
from booking.guest_tokens import issue_guest_token
from booking.models import Appointment, AppointmentStatus
from catalog.models import Service, ServiceCategory
from core.tenancy import tenant_context
from payments.models import Payment, PaymentStatus
from specialists.models import Specialist
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

START = dt.datetime(2026, 9, 1, 10, 0, tzinfo=dt.UTC)
# Two days before START: well inside the token's validity and before the
# refund deadline (START - 24h).
DEFAULT_NOW = START - dt.timedelta(days=2)

OLD_KEYS = {
    "id",
    "status",
    "start_datetime",
    "end_datetime",
    "specialist",
    "service",
    "cancelled_at",
    "cancelled_by",
    "cancellation_reason",
}
NEW_KEYS = {
    "service_price_at_booking",
    "deposit_amount",
    "amount_due_at_visit",
    "currency",
    "deposit_outcome",
    "refund_deadline",
}
EXPECTED_KEYS = OLD_KEYS | NEW_KEYS
MONEY_KEYS = sorted(NEW_KEYS)


def _freeze_now(monkeypatch: pytest.MonkeyPatch, now: dt.datetime = DEFAULT_NOW) -> None:
    monkeypatch.setattr(timezone, "now", lambda: now)


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def _detail_url(salon, appointment) -> str:
    return f"/api/v1/salons/{salon.slug}/guest/appointments/{appointment.id}/"


def _cancel_url(salon, appointment) -> str:
    return f"/api/v1/salons/{salon.slug}/guest/appointments/{appointment.id}/cancel/"


def _appointment(
    salon,
    customer,
    specialist,
    service,
    *,
    price: str = "120.00",
    percentage: str = "20.00",
    status: str = AppointmentStatus.CONFIRMED,
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
    amount: str = "24.00",
    status: str = PaymentStatus.SUCCEEDED,
) -> Payment:
    with tenant_context(salon.id):
        return Payment.objects.create(
            salon=salon,
            appointment=appointment,
            amount=Decimal(amount),
            currency=salon.currency,
            status=status,
            provider_reference_id=f"ref-{appointment.id}",
        )


def _token(salon, appointment) -> str:
    with tenant_context(salon.id):
        raw_token, _row = issue_guest_token(appointment)
    return raw_token


def _detail(client, salon, appointment, token, *, query: str = "") -> dict:
    response = client.get(_detail_url(salon, appointment) + query, HTTP_X_GUEST_TOKEN=token)
    assert response.status_code == 200
    return response.json()


def _cancel(client, salon, appointment, token) -> dict:
    response = client.post(_cancel_url(salon, appointment), HTTP_X_GUEST_TOKEN=token)
    assert response.status_code == 200
    return response.json()


def _other_salon_setup(other_salon):
    """A complete second salon: its own customer, service and specialist."""
    with tenant_context(other_salon.id):
        other_customer = Customer.objects.create(
            salon=other_salon, name="Nino", email="nino@example.com", phone="+20000000000"
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
    return other_customer, other_specialist, other_service


# --- 1. Key set -------------------------------------------------------------------


def test_detail_returns_exactly_the_old_fields_plus_the_new_ones(
    monkeypatch, client, salon, customer, specialist, service
):
    _freeze_now(monkeypatch)
    appointment = _appointment(salon, customer, specialist, service)
    token = _token(salon, appointment)

    data = _detail(client, salon, appointment, token)

    assert set(data.keys()) == EXPECTED_KEYS


# --- 2. Names per ?lang= ----------------------------------------------------------


def test_specialist_and_service_names_resolve_per_lang_with_english_fallback(
    monkeypatch, client, salon, customer, specialist, service
):
    """The specialist has a Ukrainian name, the service only an English one,
    so one request proves both the requested language and the fallback."""
    _freeze_now(monkeypatch)
    with tenant_context(salon.id):
        Specialist.objects.filter(pk=specialist.pk).update(name={"en": "Jane", "uk": "Яна"})
    appointment = _appointment(salon, customer, specialist, service)
    token = _token(salon, appointment)

    data = _detail(client, salon, appointment, token, query="?lang=uk")

    assert data["specialist"] == {"id": specialist.id, "name": "Яна"}
    assert data["service"] == {"id": service.id, "name": "Manicure"}


# --- 3. Same values as the account list -------------------------------------------


def test_every_money_field_equals_what_the_account_list_returns_for_the_same_appointment(
    monkeypatch, client, salon, customer, specialist, service
):
    """Confirmed with a succeeded payment, so every money field is non-null
    and a mismatch cannot hide behind two nulls."""
    _freeze_now(monkeypatch)
    with tenant_context(salon.id):
        account = Account.objects.create_account(
            salon=salon,
            email="alice-account@example.com",
            password="a-strong-passw0rd!",
            role=AccountRole.CLIENT,
            customer=customer,
        )
    appointment = _appointment(salon, customer, specialist, service)
    _make_payment(salon, appointment)
    token = _token(salon, appointment)

    guest = _detail(client, salon, appointment, token)
    client.force_authenticate(user=account)
    mine_response = client.get(f"/api/v1/salons/{salon.slug}/appointments/mine/")
    client.force_authenticate(user=None)
    assert mine_response.status_code == 200
    (mine,) = mine_response.json()["results"]

    assert mine["id"] == appointment.id  # guards the setup, not the code
    for key in MONEY_KEYS:
        assert guest[key] == mine[key], key


# --- 4. Two salons, two currencies ------------------------------------------------


def test_two_salons_with_different_currencies_each_carry_their_own_currency(
    monkeypatch, client, salon, other_salon, customer, specialist, service
):
    _freeze_now(monkeypatch)
    other_salon.currency = "EUR"
    other_salon.save(update_fields=["currency"])
    assert salon.currency == "UAH"  # guards the setup, not the code
    other_customer, other_specialist, other_service = _other_salon_setup(other_salon)
    uah_appointment = _appointment(salon, customer, specialist, service)
    eur_appointment = _appointment(other_salon, other_customer, other_specialist, other_service)
    uah_token = _token(salon, uah_appointment)
    eur_token = _token(other_salon, eur_appointment)

    uah = _detail(client, salon, uah_appointment, uah_token)
    eur = _detail(client, other_salon, eur_appointment, eur_token)

    assert uah["id"] == uah_appointment.id
    assert uah["currency"] == "UAH"
    assert eur["id"] == eur_appointment.id
    assert eur["currency"] == "EUR"


# --- 5. Same salon, two appointments ----------------------------------------------


def test_token_for_the_higher_pk_appointment_returns_its_own_price_and_deposit(
    monkeypatch, client, salon, customer, specialist, service
):
    """CLAUDE.md's two-level isolation rule: two appointments in the same
    salon with different prices, acting on the higher pk."""
    _freeze_now(monkeypatch)
    lower = _appointment(salon, customer, specialist, service, price="120.00")
    higher = _appointment(
        salon, customer, specialist, service, price="300.00", start=START + dt.timedelta(days=1)
    )
    assert lower.pk < higher.pk  # guards the setup, not the code
    token = _token(salon, higher)

    data = _detail(client, salon, higher, token)

    assert data["id"] == higher.id
    assert data["service_price_at_booking"] == "300.00"
    assert data["deposit_amount"] == "60.00"


# --- 6. Zero deposit ----------------------------------------------------------------


def test_deposit_amount_is_null_when_the_deposit_rounds_to_zero(
    monkeypatch, client, salon, customer, specialist, service
):
    """20% of 2.00 is 0.40, which rounds to 0 in whole units."""
    _freeze_now(monkeypatch)
    appointment = _appointment(
        salon,
        customer,
        specialist,
        service,
        price="2.00",
        percentage="20.00",
        status=AppointmentStatus.PENDING_PAYMENT,
    )
    token = _token(salon, appointment)

    data = _detail(client, salon, appointment, token)

    assert data["deposit_amount"] is None


# --- 7. refund_deadline -------------------------------------------------------------


def test_refund_deadline_is_set_for_a_paid_confirmed_booking_and_null_for_pending_payment(
    monkeypatch, client, salon, customer, specialist, service
):
    _freeze_now(monkeypatch)
    confirmed = _appointment(salon, customer, specialist, service)
    _make_payment(salon, confirmed)
    pending = _appointment(
        salon,
        customer,
        specialist,
        service,
        status=AppointmentStatus.PENDING_PAYMENT,
        start=START + dt.timedelta(days=1),
    )
    confirmed_data = _detail(client, salon, confirmed, _token(salon, confirmed))
    pending_data = _detail(client, salon, pending, _token(salon, pending))

    assert confirmed_data["refund_deadline"] is not None
    assert dt.datetime.fromisoformat(confirmed_data["refund_deadline"]) == START - dt.timedelta(
        hours=24
    )
    assert pending_data["refund_deadline"] is None


# --- 8, 9. Deposit outcome after a guest cancel -------------------------------------


def test_cancel_at_least_24h_before_start_returns_refund_processing(
    monkeypatch, client, salon, customer, specialist, service
):
    _freeze_now(monkeypatch, START - dt.timedelta(hours=25))
    appointment = _appointment(salon, customer, specialist, service)
    _make_payment(salon, appointment)
    token = _token(salon, appointment)

    data = _cancel(client, salon, appointment, token)

    assert data["status"] == AppointmentStatus.CANCELLED
    assert data["deposit_outcome"] == "refund_processing"


def test_cancel_less_than_24h_before_start_returns_withheld(
    monkeypatch, client, salon, customer, specialist, service
):
    _freeze_now(monkeypatch, START - dt.timedelta(hours=23))
    appointment = _appointment(salon, customer, specialist, service)
    _make_payment(salon, appointment)
    token = _token(salon, appointment)

    data = _cancel(client, salon, appointment, token)

    assert data["status"] == AppointmentStatus.CANCELLED
    assert data["deposit_outcome"] == "withheld"


# --- 10. Cancel and detail share one shape ------------------------------------------


def test_cancel_response_has_the_same_key_set_as_the_detail_response(
    monkeypatch, client, salon, customer, specialist, service
):
    """Also compared with EXPECTED_KEYS: equal key sets alone already hold
    today, when both responses lack the new fields."""
    _freeze_now(monkeypatch)
    appointment = _appointment(salon, customer, specialist, service)
    token = _token(salon, appointment)

    detail = _detail(client, salon, appointment, token)
    cancelled = _cancel(client, salon, appointment, token)

    assert set(cancelled.keys()) == set(detail.keys())
    assert set(cancelled.keys()) == EXPECTED_KEYS


# --- 11. No customer contact data ---------------------------------------------------


def test_no_customer_name_email_or_phone_in_either_response(
    monkeypatch, client, salon, customer, specialist, service
):
    """The manage page can be opened from a forwarded email. Checked by key
    and by value, so a nested customer block would fail too."""
    _freeze_now(monkeypatch)
    appointment = _appointment(salon, customer, specialist, service)
    _make_payment(salon, appointment)
    token = _token(salon, appointment)

    detail = _detail(client, salon, appointment, token)
    cancelled = _cancel(client, salon, appointment, token)

    forbidden_keys = {
        "customer",
        "customer_name",
        "customer_email",
        "customer_phone",
        "name",
        "email",
        "phone",
    }
    for data in (detail, cancelled):
        assert forbidden_keys.isdisjoint(data.keys())
        body = json.dumps(data, ensure_ascii=False)
        for value in (customer.name, customer.email, customer.phone):
            assert value not in body


# --- 12, 13. Values come from the Payment when one exists ---------------------------


def test_deposit_amount_comes_from_the_payment_when_it_differs_from_the_snapshots(
    monkeypatch, client, salon, customer, specialist, service
):
    """25.00, deliberately not the 24.00 that 20% of 120.00 computes to, so
    only an implementation that reads Payment.amount passes."""
    _freeze_now(monkeypatch)
    appointment = _appointment(salon, customer, specialist, service, price="120.00")
    _make_payment(salon, appointment, amount="25.00")
    token = _token(salon, appointment)

    data = _detail(client, salon, appointment, token)

    assert data["deposit_amount"] == "25.00"


def test_currency_comes_from_the_payment_after_the_salon_changes_its_currency(
    monkeypatch, client, salon, customer, specialist, service
):
    """The Payment is made in UAH, then the salon switches to EUR: a historic
    payment keeps the currency it was paid in."""
    _freeze_now(monkeypatch)
    assert salon.currency == "UAH"  # guards the setup, not the code
    appointment = _appointment(salon, customer, specialist, service)
    _make_payment(salon, appointment)
    salon.currency = "EUR"
    salon.save(update_fields=["currency"])
    token = _token(salon, appointment)

    data = _detail(client, salon, appointment, token)

    assert data["currency"] == "UAH"
