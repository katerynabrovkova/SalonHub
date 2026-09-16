"""
Stage 8.C — `initiate_payment` service function (docs/ARCHITECTURE.md § 8;
docs/DECISIONS.md § Stage 8 decisions, § Stage 8.C decisions). Tests only —
written before the service exists. Expected to fail on collection
(ModuleNotFoundError: No module named 'payments.services', and separately
ImportError: cannot import name 'PaymentProviderError' from 'core.exceptions')
until both are added — same two-step red shape test_booking_cancel_appointment.py
established for cancel_appointment.

Signature under test:

    initiate_payment(*, appointment_id, salon, provider, now) ->
        tuple[Payment, provider_data | None]

`provider` and `now` are injected — no `timezone.now()` anywhere here, and no
concrete provider implementation: `payments.providers.mock.MockPaymentProvider`
is deliberately not used, since its `PaymentIntent.client_secret` is stale
against the Stage 8.C provider-neutral contract (a separate cleanup, not this
red phase). `_FakeProvider`/`_RaisingProvider` below are minimal
`PaymentProvider`-shaped doubles defined purely for this file:
`start_payment(*, amount, currency, reference)` returns an object exposing
`provider_reference_id` and `provider_data`.

Real-DB integration tests throughout (the service does select_for_update()
plus a write) — no monkeypatching except where noted (the atomic-block check).
All `now` values are tz-aware UTC literals, never `timezone.now()` (§ Stage
6.F/6.I decisions' discipline). Every appointment/customer/specialist/service
created here passes `salon=` explicitly (Stage 6 shell-seeding trap, per
CLAUDE.md).
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

import pytest
from django.db import transaction as db_transaction

from booking.models import Appointment, AppointmentStatus
from core.exceptions import InvalidStateTransitionError, PaymentProviderError
from core.tenancy import tenant_context
from payments.models import Payment, PaymentStatus
from payments.services import initiate_payment
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

NOW = dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.UTC)
START = dt.datetime(2026, 8, 21, 10, 0, tzinfo=dt.UTC)


@dataclass(frozen=True)
class _FakeIntent:
    provider_reference_id: str
    provider_data: object = "mock_provider_data"


class _FakeProvider:
    """Records every start_payment call (amount/currency/reference) for
    call-count/argument assertions. Returns a fresh provider_reference_id
    per call so a retry is distinguishable from the original call."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def start_payment(self, *, amount, currency, reference):
        self.calls.append({"amount": amount, "currency": currency, "reference": reference})
        return _FakeIntent(provider_reference_id=f"fake_ref_{len(self.calls)}")

    def refund(self, *, provider_reference_id, reference):
        raise NotImplementedError("not exercised by initiate_payment tests")


class _RaisingProvider:
    """start_payment always raises — used for the provider-failure cases."""

    def __init__(self, exc: Exception) -> None:
        self.calls = 0
        self._exc = exc

    def start_payment(self, *, amount, currency, reference):
        self.calls += 1
        raise self._exc

    def refund(self, *, provider_reference_id, reference):
        raise NotImplementedError("not exercised by initiate_payment tests")


class _Boom(Exception):
    """Stand-in for whatever a real provider adapter might raise (network
    error, non-2xx response, ...) — initiate_payment must not leak it."""


def _make_pending_appointment(salon, specialist, service, customer, *, start=START):
    return make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=start,
        status=AppointmentStatus.PENDING_PAYMENT,
    )


def _make_existing_payment(
    salon, appt, *, status, provider_reference_id="existing_ref", provider_data=None
):
    with tenant_context(salon.id):
        return Payment.objects.create(
            salon=salon,
            appointment=appt,
            amount=Decimal("100.00"),
            currency=salon.currency,
            status=status,
            provider_reference_id=provider_reference_id,
            provider_data=provider_data,
        )


# --- 1. Happy path -------------------------------------------------------


def test_initiate_payment_happy_path_creates_a_pending_payment(
    salon, specialist, service, customer
):
    appt = _make_pending_appointment(salon, specialist, service, customer)
    provider = _FakeProvider()

    with tenant_context(salon.id):
        payment, _, _ = initiate_payment(
            appointment_id=appt.id, salon=salon, provider=provider, now=NOW
        )

    assert payment.status == PaymentStatus.PENDING
    assert payment.appointment_id == appt.id
    with tenant_context(salon.id):
        row = Payment.objects.get(appointment_id=appt.id)
    assert row.pk == payment.pk
    assert row.status == PaymentStatus.PENDING


def test_initiate_payment_calls_provider_start_payment_exactly_once(
    salon, specialist, service, customer
):
    appt = _make_pending_appointment(salon, specialist, service, customer)
    provider = _FakeProvider()

    with tenant_context(salon.id):
        initiate_payment(appointment_id=appt.id, salon=salon, provider=provider, now=NOW)

    assert len(provider.calls) == 1


def test_initiate_payment_persists_provider_reference_id_on_payment(
    salon, specialist, service, customer
):
    appt = _make_pending_appointment(salon, specialist, service, customer)
    provider = _FakeProvider()

    with tenant_context(salon.id):
        payment, _, _ = initiate_payment(
            appointment_id=appt.id, salon=salon, provider=provider, now=NOW
        )

    assert payment.provider_reference_id == "fake_ref_1"
    with tenant_context(salon.id):
        row = Payment.objects.get(pk=payment.pk)
    assert row.provider_reference_id == "fake_ref_1"


def test_initiate_payment_returns_provider_data_from_provider_response(
    salon, specialist, service, customer
):
    appt = _make_pending_appointment(salon, specialist, service, customer)
    provider = _FakeProvider()

    with tenant_context(salon.id):
        _, provider_data, _ = initiate_payment(
            appointment_id=appt.id, salon=salon, provider=provider, now=NOW
        )

    assert provider_data == "mock_provider_data"


def test_initiate_payment_persists_provider_data_on_the_payment_row(
    salon, specialist, service, customer
):
    appt = _make_pending_appointment(salon, specialist, service, customer)
    provider = _FakeProvider()

    with tenant_context(salon.id):
        payment, _, _ = initiate_payment(
            appointment_id=appt.id, salon=salon, provider=provider, now=NOW
        )

    assert payment.provider_data == "mock_provider_data"
    with tenant_context(salon.id):
        row = Payment.objects.get(pk=payment.pk)
    assert row.provider_data == "mock_provider_data"


# --- 2. Amount / currency snapshot rules ---------------------------------


def test_initiate_payment_amount_is_rounded_half_up_deposit_from_booking_snapshot(
    salon, specialist, service, customer
):
    appt = _make_pending_appointment(salon, specialist, service, customer)
    with tenant_context(salon.id):
        appt.service_price_at_booking = Decimal("101.00")
        appt.deposit_percentage_at_booking = Decimal("50.00")
        appt.save(update_fields=["service_price_at_booking", "deposit_percentage_at_booking"])
    provider = _FakeProvider()

    with tenant_context(salon.id):
        payment, _, _ = initiate_payment(
            appointment_id=appt.id, salon=salon, provider=provider, now=NOW
        )

    # 101.00 x 50% = 50.50 exactly — a true half-up boundary: half-up gives
    # 51, Python's default banker's rounding would give 50. Pins the
    # specific rounding rule, not just "some rounding happens."
    assert payment.amount == Decimal("51.00")


def test_initiate_payment_currency_is_frozen_from_salon_currency(
    salon, specialist, service, customer
):
    appt = _make_pending_appointment(salon, specialist, service, customer)
    provider = _FakeProvider()

    with tenant_context(salon.id):
        payment, _, _ = initiate_payment(
            appointment_id=appt.id, salon=salon, provider=provider, now=NOW
        )

    assert payment.currency == salon.currency == "UAH"


# --- 3. Transaction shape: lock+recheck atomic, provider call outside ----


@pytest.mark.django_db(transaction=True)
def test_initiate_payment_calls_provider_outside_the_db_transaction(
    salon, specialist, service, customer
):
    appt = _make_pending_appointment(salon, specialist, service, customer)
    seen: dict = {}

    class _AssertingProvider(_FakeProvider):
        def start_payment(self, *, amount, currency, reference):
            seen["in_atomic_block"] = db_transaction.get_connection().in_atomic_block
            return super().start_payment(amount=amount, currency=currency, reference=reference)

    provider = _AssertingProvider()

    with tenant_context(salon.id):
        initiate_payment(appointment_id=appt.id, salon=salon, provider=provider, now=NOW)

    assert seen["in_atomic_block"] is False


def test_initiate_payment_writes_row_only_after_provider_responds(
    salon, specialist, service, customer
):
    appt = _make_pending_appointment(salon, specialist, service, customer)
    seen: dict = {}

    class _AssertingProvider(_FakeProvider):
        def start_payment(self, *, amount, currency, reference):
            seen["existed_before_call"] = Payment.objects.filter(appointment_id=appt.id).exists()
            return super().start_payment(amount=amount, currency=currency, reference=reference)

    provider = _AssertingProvider()

    with tenant_context(salon.id):
        initiate_payment(appointment_id=appt.id, salon=salon, provider=provider, now=NOW)

    assert seen["existed_before_call"] is False
    with tenant_context(salon.id):
        assert Payment.objects.filter(appointment_id=appt.id).exists()


# --- 4. Appointment not in PENDING_PAYMENT --------------------------------


def test_initiate_payment_rejects_a_confirmed_appointment(salon, specialist, service, customer):
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )
    provider = _FakeProvider()

    with tenant_context(salon.id), pytest.raises(InvalidStateTransitionError):
        initiate_payment(appointment_id=appt.id, salon=salon, provider=provider, now=NOW)

    assert provider.calls == []


def test_initiate_payment_rejects_a_cancelled_appointment(salon, specialist, service, customer):
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CANCELLED,
    )
    provider = _FakeProvider()

    with tenant_context(salon.id), pytest.raises(InvalidStateTransitionError):
        initiate_payment(appointment_id=appt.id, salon=salon, provider=provider, now=NOW)

    assert provider.calls == []


def test_initiate_payment_rejects_an_expired_appointment(salon, specialist, service, customer):
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.EXPIRED,
    )
    provider = _FakeProvider()

    with tenant_context(salon.id), pytest.raises(InvalidStateTransitionError):
        initiate_payment(appointment_id=appt.id, salon=salon, provider=provider, now=NOW)

    assert provider.calls == []


def test_initiate_payment_invalid_state_error_carries_current_status_in_details(
    salon, specialist, service, customer
):
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CANCELLED,
    )
    provider = _FakeProvider()

    with tenant_context(salon.id), pytest.raises(InvalidStateTransitionError) as exc_info:
        initiate_payment(appointment_id=appt.id, salon=salon, provider=provider, now=NOW)

    assert exc_info.value.details == {"current_status": AppointmentStatus.CANCELLED}


# --- 5. Idempotency: existing PENDING payment -----------------------------


def test_initiate_payment_existing_pending_payment_is_returned_unchanged(
    salon, specialist, service, customer
):
    appt = _make_pending_appointment(salon, specialist, service, customer)
    existing = _make_existing_payment(salon, appt, status=PaymentStatus.PENDING)
    provider = _FakeProvider()

    with tenant_context(salon.id):
        payment, _, _ = initiate_payment(
            appointment_id=appt.id, salon=salon, provider=provider, now=NOW
        )

    assert payment.pk == existing.pk
    assert payment.provider_reference_id == existing.provider_reference_id
    assert payment.status == PaymentStatus.PENDING


def test_initiate_payment_existing_pending_payment_returns_provider_data_none(
    salon, specialist, service, customer
):
    appt = _make_pending_appointment(salon, specialist, service, customer)
    _make_existing_payment(salon, appt, status=PaymentStatus.PENDING)
    provider = _FakeProvider()

    with tenant_context(salon.id):
        _, provider_data, _ = initiate_payment(
            appointment_id=appt.id, salon=salon, provider=provider, now=NOW
        )

    assert provider_data is None


def test_initiate_payment_existing_pending_payment_returns_stored_provider_data(
    salon, specialist, service, customer
):
    appt = _make_pending_appointment(salon, specialist, service, customer)
    _make_existing_payment(
        salon,
        appt,
        status=PaymentStatus.PENDING,
        provider_data="https://secure.example/invoice/abc",
    )
    provider = _FakeProvider()

    with tenant_context(salon.id):
        payment, provider_data, _ = initiate_payment(
            appointment_id=appt.id, salon=salon, provider=provider, now=NOW
        )

    assert provider_data == "https://secure.example/invoice/abc"
    assert payment.provider_data == "https://secure.example/invoice/abc"
    assert provider.calls == []


def test_initiate_payment_existing_pending_payment_does_not_call_provider_again(
    salon, specialist, service, customer
):
    appt = _make_pending_appointment(salon, specialist, service, customer)
    _make_existing_payment(salon, appt, status=PaymentStatus.PENDING)
    provider = _FakeProvider()

    with tenant_context(salon.id):
        initiate_payment(appointment_id=appt.id, salon=salon, provider=provider, now=NOW)

    assert provider.calls == []


# --- 6. Retry: existing FAILED payment ------------------------------------


def test_initiate_payment_existing_failed_payment_retries_same_row_with_a_different_provider_reference_id(  # noqa: E501
    salon, specialist, service, customer
):
    appt = _make_pending_appointment(salon, specialist, service, customer)
    existing = _make_existing_payment(
        salon, appt, status=PaymentStatus.FAILED, provider_reference_id="old_ref"
    )
    provider = _FakeProvider()

    with tenant_context(salon.id):
        payment, _, _ = initiate_payment(
            appointment_id=appt.id, salon=salon, provider=provider, now=NOW
        )

    assert payment.pk == existing.pk
    assert payment.provider_reference_id != "old_ref"
    with tenant_context(salon.id):
        assert Payment.objects.filter(appointment_id=appt.id).count() == 1


def test_initiate_payment_existing_failed_payment_transitions_status_back_to_pending(
    salon, specialist, service, customer
):
    appt = _make_pending_appointment(salon, specialist, service, customer)
    existing = _make_existing_payment(salon, appt, status=PaymentStatus.FAILED)
    provider = _FakeProvider()

    with tenant_context(salon.id):
        payment, _, _ = initiate_payment(
            appointment_id=appt.id, salon=salon, provider=provider, now=NOW
        )

    assert payment.status == PaymentStatus.PENDING
    with tenant_context(salon.id):
        row = Payment.objects.get(pk=existing.pk)
    assert row.status == PaymentStatus.PENDING


def test_initiate_payment_existing_failed_payment_calls_provider_again(
    salon, specialist, service, customer
):
    appt = _make_pending_appointment(salon, specialist, service, customer)
    _make_existing_payment(salon, appt, status=PaymentStatus.FAILED)
    provider = _FakeProvider()

    with tenant_context(salon.id):
        initiate_payment(appointment_id=appt.id, salon=salon, provider=provider, now=NOW)

    assert len(provider.calls) == 1


def test_initiate_payment_existing_failed_payment_retry_overwrites_stale_provider_data(
    salon, specialist, service, customer
):
    appt = _make_pending_appointment(salon, specialist, service, customer)
    existing = _make_existing_payment(
        salon, appt, status=PaymentStatus.FAILED, provider_data="stale_old_url"
    )
    provider = _FakeProvider()

    with tenant_context(salon.id):
        payment, provider_data, _ = initiate_payment(
            appointment_id=appt.id, salon=salon, provider=provider, now=NOW
        )

    assert payment.pk == existing.pk
    assert provider_data == "mock_provider_data"
    assert payment.provider_data == "mock_provider_data"
    with tenant_context(salon.id):
        row = Payment.objects.get(pk=existing.pk)
    assert row.provider_data == "mock_provider_data"


# --- 7. Nonexistent appointment / tenant isolation ------------------------


def test_initiate_payment_nonexistent_appointment_id_raises_bare_does_not_exist(salon):
    provider = _FakeProvider()
    with tenant_context(salon.id), pytest.raises(Appointment.DoesNotExist):
        initiate_payment(appointment_id=999_999_999, salon=salon, provider=provider, now=NOW)


def test_initiate_payment_does_not_find_another_salons_appointment(
    salon, other_salon, specialist, service, customer
):
    appt = _make_pending_appointment(salon, specialist, service, customer)
    provider = _FakeProvider()

    with tenant_context(salon.id), pytest.raises(Appointment.DoesNotExist):
        initiate_payment(appointment_id=appt.id, salon=other_salon, provider=provider, now=NOW)


# --- 8. Provider-call failure ----------------------------------------------


def test_initiate_payment_provider_failure_raises_payment_provider_error(
    salon, specialist, service, customer
):
    appt = _make_pending_appointment(salon, specialist, service, customer)
    provider = _RaisingProvider(_Boom("network down"))

    with tenant_context(salon.id), pytest.raises(PaymentProviderError):
        initiate_payment(appointment_id=appt.id, salon=salon, provider=provider, now=NOW)


def test_initiate_payment_provider_failure_does_not_create_a_payment_row(
    salon, specialist, service, customer
):
    appt = _make_pending_appointment(salon, specialist, service, customer)
    provider = _RaisingProvider(_Boom("network down"))

    with tenant_context(salon.id), pytest.raises(PaymentProviderError):
        initiate_payment(appointment_id=appt.id, salon=salon, provider=provider, now=NOW)

    with tenant_context(salon.id):
        assert not Payment.objects.filter(appointment_id=appt.id).exists()


def test_initiate_payment_provider_failure_then_retry_succeeds_as_a_fresh_attempt(
    salon, specialist, service, customer
):
    appt = _make_pending_appointment(salon, specialist, service, customer)
    failing_provider = _RaisingProvider(_Boom("network down"))

    with tenant_context(salon.id), pytest.raises(PaymentProviderError):
        initiate_payment(appointment_id=appt.id, salon=salon, provider=failing_provider, now=NOW)

    working_provider = _FakeProvider()
    with tenant_context(salon.id):
        payment, provider_data, _ = initiate_payment(
            appointment_id=appt.id, salon=salon, provider=working_provider, now=NOW
        )

    assert payment.status == PaymentStatus.PENDING
    assert provider_data == "mock_provider_data"
    assert len(working_provider.calls) == 1
