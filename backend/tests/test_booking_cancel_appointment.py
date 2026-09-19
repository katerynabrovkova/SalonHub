"""
Stage 7.E — `cancel_appointment` service function (docs/ARCHITECTURE.md § 2,
§ 7, the State machines section; docs/DECISIONS.md § Stage 7.E decisions).
Tests only — written before the service exists. Expected to fail on
collection (ImportError: cannot import name 'cancel_appointment' from
'booking.services') until that function is added — same two-step red shape
`test_booking_create_appointment.py` established for `create_appointment`
itself.

Signature under test:

    cancel_appointment(*, appointment_id, salon, cancelled_by, now, provider,
                        reason="") -> Appointment

`provider` (docs/DECISIONS.md § "Refund-eligibility gap (found 19.09.2026,
closing Stage 8)") is required on every call below, even in tests that have
nothing to do with refunds — a `MockPaymentProvider()` instance, same
injection discipline as `payments.services.initiate_payment`/
`initiate_refund`. It's only actually exercised by the dedicated
refund-eligibility tests near the bottom of this file, which create a
SUCCEEDED `Payment` and assert on `provider.calls`; every other test here
has no `Payment` row at all, so `_is_refund_eligible`'s outcome is moot and
`provider.refund()` is never reached regardless of what it returns. One of
those tests uses a provider whose `refund()` raises, to pin that
cancel_appointment swallows `PaymentProviderError` rather than letting it
mask an already-committed cancellation.

Real-DB integration tests throughout (the service does select_for_update()
plus a write) — no monkeypatching. All `now` values are tz-aware UTC
literals, never `timezone.now()` (§ Stage 6.F/6.I decisions' discipline).
Every appointment/customer/token created here passes `salon=` explicitly
(Stage 6 shell-seeding trap, per CLAUDE.md).
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

import pytest

from booking.models import Appointment, AppointmentStatus, CancelledBy
from booking.services import cancel_appointment
from core.exceptions import InvalidStateTransitionError
from core.tenancy import tenant_context
from payments.models import Payment, PaymentStatus
from payments.providers.mock import MockPaymentProvider
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

START = dt.datetime(2026, 8, 20, 10, 0, tzinfo=dt.UTC)
NOW = dt.datetime(2026, 8, 18, 9, 0, tzinfo=dt.UTC)


def _unrelated_fields(appt: Appointment) -> dict:
    """Fields cancel_appointment must never touch, for the "only the
    cancellation fields changed" assertions (items 1/2)."""
    return {
        "start_datetime": appt.start_datetime,
        "end_datetime": appt.end_datetime,
        "blocked_until": appt.blocked_until,
        "specialist_id": appt.specialist_id,
        "service_id": appt.service_id,
        "customer_id": appt.customer_id,
        "service_price_at_booking": appt.service_price_at_booking,
        "deposit_percentage_at_booking": appt.deposit_percentage_at_booking,
        "hold_expires_at": appt.hold_expires_at,
    }


# --- 1 & 2. Happy path: any ACTIVE status -----------------------------------


def test_cancel_appointment_happy_path_cancels_a_pending_payment_appointment(
    salon, specialist, service, customer
):
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.PENDING_PAYMENT,
    )
    # Refresh before snapshotting: the in-memory object from .create() still
    # carries service_price_at_booking/deposit_percentage_at_booking as
    # whatever Python type the service/salon fixtures assigned (str/int),
    # not the Decimal a DB read returns — the same coercion trap
    # test_booking_create_appointment.py's snapshot-fields test documents.
    # Comparing against a later DB-fetched row without this fails on type,
    # not value.
    with tenant_context(salon.id):
        appt.refresh_from_db()
    before = _unrelated_fields(appt)

    with tenant_context(salon.id):
        result = cancel_appointment(
            appointment_id=appt.id,
            salon=salon,
            cancelled_by=CancelledBy.CUSTOMER,
            now=NOW,
            provider=MockPaymentProvider(),
            reason="change of plans",
        )

    assert result.status == AppointmentStatus.CANCELLED
    assert result.cancelled_at == NOW
    assert result.cancelled_by == CancelledBy.CUSTOMER
    assert result.cancellation_reason == "change of plans"

    with tenant_context(salon.id):
        row = Appointment.objects.get(pk=appt.id)
    assert row.status == AppointmentStatus.CANCELLED
    assert row.cancelled_at == NOW
    assert row.cancelled_by == CancelledBy.CUSTOMER
    assert row.cancellation_reason == "change of plans"
    assert _unrelated_fields(row) == before


def test_cancel_appointment_also_cancels_a_confirmed_appointment(
    salon, specialist, service, customer
):
    """Proves the guard is "in ACTIVE_APPOINTMENT_STATUSES", not
    "== PENDING_PAYMENT" — CONFIRMED is the other member of that set."""
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )
    # Same coercion trap as the PENDING_PAYMENT test above — refresh before
    # snapshotting so `before` is comparable to the later DB-fetched `row`.
    with tenant_context(salon.id):
        appt.refresh_from_db()
    before = _unrelated_fields(appt)

    with tenant_context(salon.id):
        result = cancel_appointment(
            appointment_id=appt.id,
            salon=salon,
            cancelled_by=CancelledBy.CUSTOMER,
            now=NOW,
            provider=MockPaymentProvider(),
            reason="change of plans",
        )

    assert result.status == AppointmentStatus.CANCELLED
    assert result.cancelled_at == NOW
    assert result.cancelled_by == CancelledBy.CUSTOMER
    assert result.cancellation_reason == "change of plans"

    with tenant_context(salon.id):
        row = Appointment.objects.get(pk=appt.id)
    assert row.status == AppointmentStatus.CANCELLED
    assert row.cancelled_at == NOW
    assert row.cancelled_by == CancelledBy.CUSTOMER
    assert row.cancellation_reason == "change of plans"
    assert _unrelated_fields(row) == before


# --- 3. Non-active statuses raise InvalidStateTransitionError ---------------


def test_cancel_appointment_rejects_an_already_cancelled_appointment(
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

    with tenant_context(salon.id), pytest.raises(InvalidStateTransitionError) as exc_info:
        cancel_appointment(
            appointment_id=appt.id,
            salon=salon,
            cancelled_by=CancelledBy.CUSTOMER,
            now=NOW,
            provider=MockPaymentProvider(),
        )

    assert exc_info.value.details == {"current_status": AppointmentStatus.CANCELLED}
    with tenant_context(salon.id):
        row = Appointment.objects.get(pk=appt.id)
    assert row.status == AppointmentStatus.CANCELLED
    assert row.cancelled_at is None
    assert row.cancelled_by is None
    assert row.cancellation_reason == ""


def test_cancel_appointment_rejects_an_expired_appointment(salon, specialist, service, customer):
    """The sweep-flipped-to-EXPIRED case — the second distinct reason this
    guard exists, alongside repeat-cancel."""
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.EXPIRED,
    )

    with tenant_context(salon.id), pytest.raises(InvalidStateTransitionError) as exc_info:
        cancel_appointment(
            appointment_id=appt.id,
            salon=salon,
            cancelled_by=CancelledBy.CUSTOMER,
            now=NOW,
            provider=MockPaymentProvider(),
        )

    assert exc_info.value.details == {"current_status": AppointmentStatus.EXPIRED}
    with tenant_context(salon.id):
        row = Appointment.objects.get(pk=appt.id)
    assert row.status == AppointmentStatus.EXPIRED
    assert row.cancelled_at is None
    assert row.cancelled_by is None
    assert row.cancellation_reason == ""


# --- 4. cancelled_by round-trips for every role ------------------------------


def test_cancel_appointment_stores_cancelled_by_guest(salon, specialist, service, customer):
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )
    with tenant_context(salon.id):
        result = cancel_appointment(
            appointment_id=appt.id,
            salon=salon,
            cancelled_by=CancelledBy.GUEST,
            now=NOW,
            provider=MockPaymentProvider(),
        )
    assert result.cancelled_by == CancelledBy.GUEST


def test_cancel_appointment_stores_cancelled_by_customer(salon, specialist, service, customer):
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )
    with tenant_context(salon.id):
        result = cancel_appointment(
            appointment_id=appt.id,
            salon=salon,
            cancelled_by=CancelledBy.CUSTOMER,
            now=NOW,
            provider=MockPaymentProvider(),
        )
    assert result.cancelled_by == CancelledBy.CUSTOMER


def test_cancel_appointment_stores_cancelled_by_staff(salon, specialist, service, customer):
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )
    with tenant_context(salon.id):
        result = cancel_appointment(
            appointment_id=appt.id,
            salon=salon,
            cancelled_by=CancelledBy.STAFF,
            now=NOW,
            provider=MockPaymentProvider(),
        )
    assert result.cancelled_by == CancelledBy.STAFF


# --- 5. reason is optional and wired through ---------------------------------


def test_cancel_appointment_defaults_reason_to_empty_string(salon, specialist, service, customer):
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )
    with tenant_context(salon.id):
        result = cancel_appointment(
            appointment_id=appt.id,
            salon=salon,
            cancelled_by=CancelledBy.CUSTOMER,
            now=NOW,
            provider=MockPaymentProvider(),
        )
    assert result.cancellation_reason == ""


def test_cancel_appointment_stores_a_non_empty_reason(salon, specialist, service, customer):
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )
    with tenant_context(salon.id):
        result = cancel_appointment(
            appointment_id=appt.id,
            salon=salon,
            cancelled_by=CancelledBy.CUSTOMER,
            now=NOW,
            provider=MockPaymentProvider(),
            reason="specialist unavailable",
        )
    assert result.cancellation_reason == "specialist unavailable"


# --- 6. Nonexistent appointment -----------------------------------------------


def test_cancel_appointment_nonexistent_id_raises_bare_does_not_exist(salon):
    """The service doesn't know HTTP — a nonexistent id is a bare
    Appointment.DoesNotExist, not a translated 404."""
    with tenant_context(salon.id), pytest.raises(Appointment.DoesNotExist):
        cancel_appointment(
            appointment_id=999_999_999,
            salon=salon,
            cancelled_by=CancelledBy.CUSTOMER,
            now=NOW,
            provider=MockPaymentProvider(),
        )


# --- 7. Tenant isolation ------------------------------------------------------


def test_cancel_appointment_does_not_find_another_salons_appointment(
    salon, other_salon, specialist, service, customer
):
    """specialist/service/customer/appointment all belong to `salon`; the
    call passes salon=other_salon. select_for_update().get(salon=salon,
    pk=...) filters by the passed salon, so a foreign appointment is
    indistinguishable from a nonexistent one — this proves `salon` is
    actually applied in the fetch, not silently ignored."""
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )

    with tenant_context(salon.id), pytest.raises(Appointment.DoesNotExist):
        cancel_appointment(
            appointment_id=appt.id,
            salon=other_salon,
            cancelled_by=CancelledBy.CUSTOMER,
            now=NOW,
            provider=MockPaymentProvider(),
        )


# --- 8. Refund-eligibility gap fix (docs/DECISIONS.md § "Refund-eligibility
# gap (found 19.09.2026, closing Stage 8)") ----------------------------------


@dataclass(frozen=True)
class _FakeRefundIntent:
    provider_reference_id: str


class _FakeRefundProvider:
    """Records every refund() call for call-count/argument assertions — same
    shape as test_payments_initiate_refund.py's _FakeProvider (a separate,
    self-contained double rather than an import across test files).
    start_payment is not exercised by cancel_appointment and raises if
    somehow called."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def start_payment(self, *, amount, currency, reference):
        raise NotImplementedError("not exercised by cancel_appointment tests")

    def refund(self, *, provider_reference_id, reference, amount, currency):
        self.calls.append(
            {
                "provider_reference_id": provider_reference_id,
                "reference": reference,
                "amount": amount,
                "currency": currency,
            }
        )
        return _FakeRefundIntent(provider_reference_id=f"fake_refund_{len(self.calls)}")


def _make_succeeded_payment(salon, appt, *, provider_reference_id="existing_ref"):
    with tenant_context(salon.id):
        return Payment.objects.create(
            salon=salon,
            appointment=appt,
            amount=Decimal("100.00"),
            currency=salon.currency,
            status=PaymentStatus.SUCCEEDED,
            provider_reference_id=provider_reference_id,
        )


def _payment_status(salon, payment_id) -> str:
    with tenant_context(salon.id):
        return Payment.objects.get(pk=payment_id).status


def test_cancel_appointment_salon_initiated_at_least_24h_before_start_triggers_refund(
    salon, specialist, service, customer
):
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )
    payment = _make_succeeded_payment(salon, appt)
    provider = _FakeRefundProvider()

    with tenant_context(salon.id):
        cancel_appointment(
            appointment_id=appt.id,
            salon=salon,
            cancelled_by=CancelledBy.STAFF,
            now=START - dt.timedelta(hours=48),
            provider=provider,
        )

    assert len(provider.calls) == 1
    assert provider.calls[0]["amount"] == payment.amount
    assert _payment_status(salon, payment.id) == PaymentStatus.REFUND_PENDING


def test_cancel_appointment_salon_initiated_less_than_24h_before_start_still_triggers_refund(
    salon, specialist, service, customer
):
    """Salon-initiated cancellations always refund, regardless of timing —
    the ≥24h cutoff applies only to customer-initiated cancellations."""
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )
    payment = _make_succeeded_payment(salon, appt)
    provider = _FakeRefundProvider()

    with tenant_context(salon.id):
        cancel_appointment(
            appointment_id=appt.id,
            salon=salon,
            cancelled_by=CancelledBy.STAFF,
            now=START - dt.timedelta(hours=1),
            provider=provider,
        )

    assert len(provider.calls) == 1
    assert _payment_status(salon, payment.id) == PaymentStatus.REFUND_PENDING


def test_cancel_appointment_customer_initiated_at_least_24h_before_start_triggers_refund(
    salon, specialist, service, customer
):
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )
    payment = _make_succeeded_payment(salon, appt)
    provider = _FakeRefundProvider()

    with tenant_context(salon.id):
        cancel_appointment(
            appointment_id=appt.id,
            salon=salon,
            cancelled_by=CancelledBy.CUSTOMER,
            now=START - dt.timedelta(hours=24),  # exactly the cutoff -> eligible ("≥24h")
            provider=provider,
        )

    assert len(provider.calls) == 1
    assert _payment_status(salon, payment.id) == PaymentStatus.REFUND_PENDING


def test_cancel_appointment_guest_initiated_at_least_24h_before_start_triggers_refund(
    salon, specialist, service, customer
):
    """GUEST is the other customer-initiated value — the one
    GuestAppointmentCancelView actually passes in production — proving it's
    covered by the same eligibility rule as CUSTOMER, not just CUSTOMER
    itself."""
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )
    payment = _make_succeeded_payment(salon, appt)
    provider = _FakeRefundProvider()

    with tenant_context(salon.id):
        cancel_appointment(
            appointment_id=appt.id,
            salon=salon,
            cancelled_by=CancelledBy.GUEST,
            now=START - dt.timedelta(hours=48),
            provider=provider,
        )

    assert len(provider.calls) == 1
    assert _payment_status(salon, payment.id) == PaymentStatus.REFUND_PENDING


def test_cancel_appointment_customer_initiated_less_than_24h_before_start_triggers_no_refund(
    salon, specialist, service, customer
):
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )
    payment = _make_succeeded_payment(salon, appt)
    provider = _FakeRefundProvider()

    with tenant_context(salon.id):
        cancel_appointment(
            appointment_id=appt.id,
            salon=salon,
            cancelled_by=CancelledBy.CUSTOMER,
            now=START - dt.timedelta(hours=23, minutes=59),  # just under -> ineligible
            provider=provider,
        )

    assert provider.calls == []
    assert _payment_status(salon, payment.id) == PaymentStatus.SUCCEEDED


def test_cancel_appointment_with_no_payment_succeeds_without_refund_attempt(
    salon, specialist, service, customer
):
    """No Payment row at all (never paid) — cancellation must still succeed,
    and there's nothing to look up a status on, let alone refund. `now` is
    deliberately ≥24h-eligible timing, to prove it's the absence of a
    Payment — not ineligibility — that skips the refund attempt."""
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.PENDING_PAYMENT,
    )
    provider = _FakeRefundProvider()

    with tenant_context(salon.id):
        result = cancel_appointment(
            appointment_id=appt.id,
            salon=salon,
            cancelled_by=CancelledBy.CUSTOMER,
            now=START - dt.timedelta(hours=48),
            provider=provider,
        )

    assert result.status == AppointmentStatus.CANCELLED
    assert provider.calls == []
    with tenant_context(salon.id):
        assert not Payment.objects.filter(appointment=appt).exists()


def test_cancel_appointment_with_a_non_succeeded_payment_triggers_no_refund(
    salon, specialist, service, customer
):
    """A Payment that exists but never reached SUCCEEDED (e.g. still
    PENDING) has no money collected to refund — cancel_appointment must not
    call initiate_refund against it even when otherwise eligible."""
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.PENDING_PAYMENT,
    )
    with tenant_context(salon.id):
        payment = Payment.objects.create(
            salon=salon,
            appointment=appt,
            amount=Decimal("100.00"),
            currency=salon.currency,
            status=PaymentStatus.PENDING,
            provider_reference_id="existing_ref",
        )
    provider = _FakeRefundProvider()

    with tenant_context(salon.id):
        cancel_appointment(
            appointment_id=appt.id,
            salon=salon,
            cancelled_by=CancelledBy.STAFF,
            now=START - dt.timedelta(hours=48),
            provider=provider,
        )

    assert provider.calls == []
    assert _payment_status(salon, payment.id) == PaymentStatus.PENDING


class _RaisingRefundProvider:
    """refund() always raises — same shape as
    test_payments_initiate_refund.py's _RaisingProvider. Used to prove
    cancel_appointment does not let a provider failure undo or mask an
    already-committed cancellation (docs/DECISIONS.md § "Refund-eligibility
    gap (found 19.09.2026, closing Stage 8)")."""

    def __init__(self, exc: Exception) -> None:
        self.calls = 0
        self._exc = exc

    def start_payment(self, *, amount, currency, reference):
        raise NotImplementedError("not exercised by cancel_appointment tests")

    def refund(self, *, provider_reference_id, reference, amount, currency):
        self.calls += 1
        raise self._exc


def test_cancel_appointment_still_cancels_even_when_the_refund_provider_call_fails(
    salon, specialist, service, customer
):
    """A failing provider.refund() must not undo or mask the cancellation,
    which already committed in its own, earlier transaction: the
    appointment stays CANCELLED, and cancel_appointment does not raise —
    initiate_refund already left Payment REFUND_PENDING (committed before
    its own provider call) for the Stage 8.G sweep to recover, so there's
    nothing further to signal synchronously to this function's caller."""
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.CONFIRMED,
    )
    payment = _make_succeeded_payment(salon, appt)
    provider = _RaisingRefundProvider(RuntimeError("network down"))

    with tenant_context(salon.id):
        result = cancel_appointment(
            appointment_id=appt.id,
            salon=salon,
            cancelled_by=CancelledBy.STAFF,
            now=START - dt.timedelta(hours=48),
            provider=provider,
        )

    assert provider.calls == 1
    assert result.status == AppointmentStatus.CANCELLED
    assert result.cancelled_at == START - dt.timedelta(hours=48)

    with tenant_context(salon.id):
        row = Appointment.objects.get(pk=appt.id)
    assert row.status == AppointmentStatus.CANCELLED
    assert _payment_status(salon, payment.id) == PaymentStatus.REFUND_PENDING
