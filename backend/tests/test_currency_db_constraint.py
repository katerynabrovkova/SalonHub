"""
Stage 8.B-bis — CheckConstraint enforcing ISO 4217 format (^[A-Z]{3}$) on
Salon.currency and Payment.currency at the database level
(docs/DECISIONS.md § Stage 8 decisions). Tests only — written before either
constraint exists.

Every invalid-currency case below calls .objects.create() directly, never
full_clean() — this is itself the proof that enforcement is DB-level and
not dependent on the validator being invoked; there is no separate
"bypasses full_clean" test, since each case here already demonstrates that
by construction.

Real-DB integration tests (a CheckConstraint is a database-level
guarantee). Each expected-failure case wraps the failing create() in its
own transaction.atomic() savepoint, inside pytest.raises(IntegrityError) —
mirroring test_customer_email_uniqueness.py — because Postgres aborts the
whole surrounding transaction after an IntegrityError, and pytest-django
wraps each test in one.
"""

import datetime as dt

import pytest
from django.db import IntegrityError, transaction

from core.tenancy import tenant_context
from payments.models import Payment
from tenants.models import Salon
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

START = dt.datetime(2026, 8, 20, 10, 0, tzinfo=dt.UTC)


# --- Salon.currency ----------------------------------------------------


def test_salon_currency_valid_value_succeeds():
    salon = Salon.objects.create(
        name="Valid Currency Salon",
        slug="valid-currency-salon",
        currency="USD",
        contact_email="owner@valid-currency-salon.example",
    )
    assert salon.currency == "USD"


def test_salon_currency_empty_string_rejected():
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Salon.objects.create(
                name="Empty Currency Salon",
                slug="empty-currency-salon",
                currency="",
                contact_email="owner@empty-currency-salon.example",
            )


def test_salon_currency_lowercase_rejected():
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Salon.objects.create(
                name="Lowercase Currency Salon",
                slug="lowercase-currency-salon",
                currency="uah",
                contact_email="owner@lowercase-currency-salon.example",
            )


def test_salon_currency_wrong_length_rejected():
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Salon.objects.create(
                name="Wrong Length Currency Salon",
                slug="wrong-length-currency-salon",
                currency="US",
                contact_email="owner@wrong-length-currency-salon.example",
            )


# --- Payment.currency ----------------------------------------------------


def _make_payment(*, salon: Salon, appointment, currency: str) -> Payment:
    with tenant_context(salon.id):
        return Payment.objects.create(
            salon=salon, appointment=appointment, amount="100.00", currency=currency
        )


def test_payment_currency_valid_value_succeeds(salon, specialist, service, customer):
    appointment = make_appointment(
        salon=salon, customer=customer, specialist=specialist, service=service, start=START
    )
    payment = _make_payment(salon=salon, appointment=appointment, currency="USD")
    assert payment.currency == "USD"


def test_payment_currency_empty_string_rejected(salon, specialist, service, customer):
    appointment = make_appointment(
        salon=salon, customer=customer, specialist=specialist, service=service, start=START
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _make_payment(salon=salon, appointment=appointment, currency="")


def test_payment_currency_lowercase_rejected(salon, specialist, service, customer):
    appointment = make_appointment(
        salon=salon, customer=customer, specialist=specialist, service=service, start=START
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _make_payment(salon=salon, appointment=appointment, currency="uah")


def test_payment_currency_wrong_length_rejected(salon, specialist, service, customer):
    appointment = make_appointment(
        salon=salon, customer=customer, specialist=specialist, service=service, start=START
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _make_payment(salon=salon, appointment=appointment, currency="US")
