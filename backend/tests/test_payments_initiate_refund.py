"""
Stage 8.F — `initiate_refund` service function (docs/ARCHITECTURE.md § 8;
docs/DECISIONS.md § Stage 8 decisions, § Stage 8.F decisions). Tests only —
written before the service exists. New file, not appended to
test_payments_initiate_payment.py: a module-scope `from payments.services
import initiate_refund` fails on collection (ImportError, no such name)
until the service is written, and appending to the existing 8.C file would
take its other tests down with the same single collection error (the 8.D
lesson).

Signature under test:

    initiate_refund(*, payment_id, salon, provider, now) -> Payment

`provider` is injected — no concrete provider implementation
(`payments.providers.mock.MockPaymentProvider`) is used here, same
discipline as 8.C: `_FakeProvider`/`_RaisingProvider` below are minimal
`PaymentProvider`-shaped doubles defined purely for this file, exercising
only `refund(*, provider_reference_id, reference)` — `start_payment` is not
exercised by these tests and raises if called.

Real-DB integration tests throughout (the service does select_for_update()
plus a write) — no monkeypatching. Every backing `Appointment` is
`CONFIRMED`: `initiate_refund` reads only `Payment.status`, never
`Appointment.status` (§ Stage 8.F decisions — refund eligibility is decided
by the caller, not this service), so the appointment's own status is
irrelevant here and `CONFIRMED` (the `make_appointment` default) is used
only because it reads sensibly. `now` (§ Stage 8.G) is stamped onto
`refund_initiated_at` only at the real `SUCCEEDED` -> `REFUND_PENDING`
transition; every call site here passes the fixed `START` literal, never
`timezone.now()`. Every appointment/customer/specialist/service created
here passes `salon=` explicitly (Stage 6 shell-seeding trap, per
CLAUDE.md).
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

import pytest

from core.exceptions import InvalidStateTransitionError, PaymentProviderError
from core.tenancy import tenant_context
from payments.models import Payment, PaymentStatus
from payments.services import initiate_refund
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

START = dt.datetime(2026, 8, 21, 10, 0, tzinfo=dt.UTC)
# A known instant strictly before START, used only by the idempotent-no-op
# test below to prove a duplicate call doesn't re-stamp refund_initiated_at.
EARLIER = dt.datetime(2026, 8, 20, 9, 0, tzinfo=dt.UTC)


@dataclass(frozen=True)
class _FakeRefundIntent:
    provider_reference_id: str


class _FakeProvider:
    """Records every refund() call (provider_reference_id/reference) for
    call-count/argument assertions. start_payment is not exercised by
    initiate_refund tests and raises if called."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def start_payment(self, *, amount, currency, reference):
        raise NotImplementedError("not exercised by initiate_refund tests")

    def refund(self, *, provider_reference_id, reference):
        self.calls.append({"provider_reference_id": provider_reference_id, "reference": reference})
        return _FakeRefundIntent(provider_reference_id=f"fake_refund_{len(self.calls)}")


class _RaisingProvider:
    """refund() always raises — used for the provider-failure cases."""

    def __init__(self, exc: Exception) -> None:
        self.calls = 0
        self._exc = exc

    def start_payment(self, *, amount, currency, reference):
        raise NotImplementedError("not exercised by initiate_refund tests")

    def refund(self, *, provider_reference_id, reference):
        self.calls += 1
        raise self._exc


class _Boom(Exception):
    """Stand-in for whatever a real provider adapter might raise (network
    error, non-2xx response, ...) — initiate_refund must not leak it."""


def _make_confirmed_appointment(salon, specialist, service, customer, *, start=START):
    return make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=start,
    )


def _make_payment(
    salon, appt, *, status, provider_reference_id="existing_ref", refund_initiated_at=None
):
    with tenant_context(salon.id):
        return Payment.objects.create(
            salon=salon,
            appointment=appt,
            amount=Decimal("100.00"),
            currency=salon.currency,
            status=status,
            provider_reference_id=provider_reference_id,
            refund_initiated_at=refund_initiated_at,
        )


# --- 1. Happy path ---------------------------------------------------------


def test_initiate_refund_happy_path_transitions_to_refund_pending(
    salon, specialist, service, customer
):
    appt = _make_confirmed_appointment(salon, specialist, service, customer)
    existing = _make_payment(salon, appt, status=PaymentStatus.SUCCEEDED)
    provider = _FakeProvider()

    with tenant_context(salon.id):
        payment = initiate_refund(payment_id=existing.id, salon=salon, provider=provider, now=START)

    assert payment.status == PaymentStatus.REFUND_PENDING
    with tenant_context(salon.id):
        row = Payment.objects.get(pk=existing.pk)
    assert row.status == PaymentStatus.REFUND_PENDING
    assert row.refund_initiated_at == START


def test_initiate_refund_calls_provider_refund_exactly_once(salon, specialist, service, customer):
    appt = _make_confirmed_appointment(salon, specialist, service, customer)
    existing = _make_payment(salon, appt, status=PaymentStatus.SUCCEEDED)
    provider = _FakeProvider()

    with tenant_context(salon.id):
        initiate_refund(payment_id=existing.id, salon=salon, provider=provider, now=START)

    assert len(provider.calls) == 1


def test_initiate_refund_calls_provider_with_reference_id_and_appointment_reference(
    salon, specialist, service, customer
):
    appt = _make_confirmed_appointment(salon, specialist, service, customer)
    existing = _make_payment(
        salon, appt, status=PaymentStatus.SUCCEEDED, provider_reference_id="existing_ref"
    )
    provider = _FakeProvider()

    with tenant_context(salon.id):
        initiate_refund(payment_id=existing.id, salon=salon, provider=provider, now=START)

    assert provider.calls == [{"provider_reference_id": "existing_ref", "reference": str(appt.id)}]


# --- 2. Ordering: status written before the provider is called ------------


def test_initiate_refund_writes_refund_pending_before_calling_provider(
    salon, specialist, service, customer
):
    appt = _make_confirmed_appointment(salon, specialist, service, customer)
    existing = _make_payment(salon, appt, status=PaymentStatus.SUCCEEDED)
    seen: dict = {}

    class _AssertingProvider(_FakeProvider):
        def refund(self, *, provider_reference_id, reference):
            seen["status_at_call_time"] = Payment.objects.get(pk=existing.pk).status
            return super().refund(provider_reference_id=provider_reference_id, reference=reference)

    provider = _AssertingProvider()

    with tenant_context(salon.id):
        initiate_refund(payment_id=existing.id, salon=salon, provider=provider, now=START)

    assert seen["status_at_call_time"] == PaymentStatus.REFUND_PENDING


# --- 3. Idempotency: refund already in flight or already done -------------


def test_initiate_refund_existing_refund_pending_is_returned_unchanged(
    salon, specialist, service, customer
):
    appt = _make_confirmed_appointment(salon, specialist, service, customer)
    existing = _make_payment(
        salon, appt, status=PaymentStatus.REFUND_PENDING, refund_initiated_at=EARLIER
    )
    provider = _FakeProvider()

    with tenant_context(salon.id):
        payment = initiate_refund(payment_id=existing.id, salon=salon, provider=provider, now=START)

    assert payment.pk == existing.pk
    assert payment.status == PaymentStatus.REFUND_PENDING
    assert provider.calls == []
    with tenant_context(salon.id):
        row = Payment.objects.get(pk=existing.pk)
    assert row.refund_initiated_at == EARLIER


def test_initiate_refund_existing_refunded_is_returned_unchanged(
    salon, specialist, service, customer
):
    appt = _make_confirmed_appointment(salon, specialist, service, customer)
    existing = _make_payment(salon, appt, status=PaymentStatus.REFUNDED)
    provider = _FakeProvider()

    with tenant_context(salon.id):
        payment = initiate_refund(payment_id=existing.id, salon=salon, provider=provider, now=START)

    assert payment.pk == existing.pk
    assert payment.status == PaymentStatus.REFUNDED
    assert provider.calls == []


# --- 4. Status gate -> 409 for every non-refundable status -----------------


def test_initiate_refund_rejects_a_pending_payment(salon, specialist, service, customer):
    appt = _make_confirmed_appointment(salon, specialist, service, customer)
    existing = _make_payment(salon, appt, status=PaymentStatus.PENDING)
    provider = _FakeProvider()

    with tenant_context(salon.id), pytest.raises(InvalidStateTransitionError):
        initiate_refund(payment_id=existing.id, salon=salon, provider=provider, now=START)

    assert provider.calls == []
    with tenant_context(salon.id):
        row = Payment.objects.get(pk=existing.pk)
    assert row.status == PaymentStatus.PENDING


def test_initiate_refund_rejects_a_failed_payment(salon, specialist, service, customer):
    appt = _make_confirmed_appointment(salon, specialist, service, customer)
    existing = _make_payment(salon, appt, status=PaymentStatus.FAILED)
    provider = _FakeProvider()

    with tenant_context(salon.id), pytest.raises(InvalidStateTransitionError):
        initiate_refund(payment_id=existing.id, salon=salon, provider=provider, now=START)

    assert provider.calls == []
    with tenant_context(salon.id):
        row = Payment.objects.get(pk=existing.pk)
    assert row.status == PaymentStatus.FAILED


def test_initiate_refund_rejects_an_expired_payment(salon, specialist, service, customer):
    appt = _make_confirmed_appointment(salon, specialist, service, customer)
    existing = _make_payment(salon, appt, status=PaymentStatus.EXPIRED)
    provider = _FakeProvider()

    with tenant_context(salon.id), pytest.raises(InvalidStateTransitionError):
        initiate_refund(payment_id=existing.id, salon=salon, provider=provider, now=START)

    assert provider.calls == []
    with tenant_context(salon.id):
        row = Payment.objects.get(pk=existing.pk)
    assert row.status == PaymentStatus.EXPIRED


def test_initiate_refund_rejects_a_cancelled_payment(salon, specialist, service, customer):
    appt = _make_confirmed_appointment(salon, specialist, service, customer)
    existing = _make_payment(salon, appt, status=PaymentStatus.CANCELLED)
    provider = _FakeProvider()

    with tenant_context(salon.id), pytest.raises(InvalidStateTransitionError):
        initiate_refund(payment_id=existing.id, salon=salon, provider=provider, now=START)

    assert provider.calls == []
    with tenant_context(salon.id):
        row = Payment.objects.get(pk=existing.pk)
    assert row.status == PaymentStatus.CANCELLED


def test_initiate_refund_invalid_state_error_carries_current_status_in_details(
    salon, specialist, service, customer
):
    appt = _make_confirmed_appointment(salon, specialist, service, customer)
    existing = _make_payment(salon, appt, status=PaymentStatus.PENDING)
    provider = _FakeProvider()

    with tenant_context(salon.id), pytest.raises(InvalidStateTransitionError) as exc_info:
        initiate_refund(payment_id=existing.id, salon=salon, provider=provider, now=START)

    assert exc_info.value.details == {"current_status": PaymentStatus.PENDING}


def test_initiate_refund_rejects_a_processing_payment(salon, specialist, service, customer):
    # PROCESSING is currently unreachable (no code assigns it anywhere — §
    # Stage 8.F decisions), but the status gate must be an ALLOWLIST (only
    # SUCCEEDED / REFUND_PENDING / REFUNDED are handled) so a future
    # real-provider PROCESSING state can't silently slip into the happy path.
    appt = _make_confirmed_appointment(salon, specialist, service, customer)
    existing = _make_payment(salon, appt, status=PaymentStatus.PROCESSING)
    provider = _FakeProvider()

    with tenant_context(salon.id), pytest.raises(InvalidStateTransitionError):
        initiate_refund(payment_id=existing.id, salon=salon, provider=provider, now=START)

    assert provider.calls == []
    with tenant_context(salon.id):
        row = Payment.objects.get(pk=existing.pk)
    assert row.status == PaymentStatus.PROCESSING


# --- 5. Nonexistent payment / tenant isolation ------------------------------


def test_initiate_refund_does_not_find_another_salons_payment(
    salon, other_salon, specialist, service, customer
):
    appt = _make_confirmed_appointment(salon, specialist, service, customer)
    existing = _make_payment(salon, appt, status=PaymentStatus.SUCCEEDED)
    provider = _FakeProvider()

    with tenant_context(salon.id), pytest.raises(Payment.DoesNotExist):
        initiate_refund(payment_id=existing.id, salon=other_salon, provider=provider, now=START)

    assert provider.calls == []


def test_initiate_refund_nonexistent_payment_id_raises_bare_does_not_exist(salon):
    provider = _FakeProvider()

    with tenant_context(salon.id), pytest.raises(Payment.DoesNotExist):
        initiate_refund(payment_id=999_999_999, salon=salon, provider=provider, now=START)

    assert provider.calls == []


# --- 6. Provider-call failure ------------------------------------------------


def test_initiate_refund_provider_failure_raises_payment_provider_error(
    salon, specialist, service, customer
):
    appt = _make_confirmed_appointment(salon, specialist, service, customer)
    existing = _make_payment(salon, appt, status=PaymentStatus.SUCCEEDED)
    provider = _RaisingProvider(_Boom("network down"))

    with tenant_context(salon.id), pytest.raises(PaymentProviderError):
        initiate_refund(payment_id=existing.id, salon=salon, provider=provider, now=START)


def test_initiate_refund_provider_failure_leaves_row_in_refund_pending(
    salon, specialist, service, customer
):
    appt = _make_confirmed_appointment(salon, specialist, service, customer)
    existing = _make_payment(salon, appt, status=PaymentStatus.SUCCEEDED)
    provider = _RaisingProvider(_Boom("network down"))

    with tenant_context(salon.id), pytest.raises(PaymentProviderError):
        initiate_refund(payment_id=existing.id, salon=salon, provider=provider, now=START)

    with tenant_context(salon.id):
        row = Payment.objects.get(pk=existing.pk)
    assert row.status == PaymentStatus.REFUND_PENDING
