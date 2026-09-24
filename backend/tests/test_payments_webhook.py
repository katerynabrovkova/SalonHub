"""
Stage 8.E — inbound payment webhook handler (docs/ARCHITECTURE.md § 8;
docs/DECISIONS.md § Stage 8 decisions, § Stage 8.E decisions). Tests only —
written before PaymentWebhookView, payments/urls.py, and the
/api/v1/webhooks/payments/ route exist. New file, not appended to any other
test_payments_*.py file: a module-scope `from payments.views import
PaymentWebhookView` fails on collection (ImportError: no such module/name)
until the view is written, and appending to an existing file would take its
other tests down with the same single collection error (the 8.D lesson).

Locked design this file is written against (agreed in discussion, not yet
implemented):

- PaymentWebhookView lives in a new payments/views.py.
- PaymentProvider gains a third method, `verify_signature(self, *, payload:
  bytes, signature: str) -> bool`, injected the same way as `start_payment`/
  `refund` via a `provider_class` class attribute on the view (the named
  contract test_guest_appointment_pay.py already established for
  GuestAppointmentPayView: `monkeypatch.setattr(PaymentWebhookView,
  "provider_class", <fake>)`). No internal function is ever patched directly
  — the EXPIRED-branch test proves `initiate_refund` ran by observing
  `_FakeProvider.refund` was called, exactly as test_payments_initiate_refund.py
  verifies its own provider calls.
- Response codes: invalid signature -> 401 (sender authentication, distinct
  from a malformed body); malformed/unparseable body or a missing required
  field -> 400; unknown provider_reference_id -> 404 + logger.warning
  containing the provider_reference_id; everything processed (including
  ignored event types, duplicates, and the EXPIRED case) -> 200.
- ProcessedWebhookEvent is not tenant-scoped (plain models.Model, § Stage 8.E
  decisions), so queries against it below use NO tenant_context wrapper,
  unlike every Payment/Appointment query in the same test, which does.
- The malformed-JSON test posts a raw string body
  (data="{not valid json", content_type="application/json") rather than a
  dict with format="json", which would always serialize to valid JSON.
- The 404 test captures the warning via
  caplog.at_level(logging.WARNING, logger="payments.views") and asserts the
  provider_reference_id string appears in caplog.text — tied to this file's
  assumption that payments/views.py logs via logging.getLogger(__name__).

Real-DB integration tests throughout, same discipline as the 8.C/8.D/8.F
sibling files — no monkeypatching except the provider_class substitution.
Every appointment/customer/specialist/service/payment created here passes
salon= explicitly (Stage 6 shell-seeding trap, per CLAUDE.md). All `start`
values are fixed tz-aware UTC literals, never timezone.now().
"""

import datetime as dt
import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

import pytest
from rest_framework.test import APIClient

from booking.models import Appointment, AppointmentStatus
from core.tenancy import tenant_context
from payments.models import Payment, PaymentStatus, ProcessedWebhookEvent
from payments.views import PaymentWebhookView
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

START = dt.datetime(2026, 8, 22, 10, 0, tzinfo=dt.UTC)


@pytest.fixture
def client() -> APIClient:
    return APIClient()


@dataclass(frozen=True)
class _FakeRefundIntent:
    provider_reference_id: str


class _FakeProvider:
    """verify_signature accepts unconditionally; refund() records every call
    (provider_reference_id/reference) on a CLASS-level list, since the view
    builds a fresh provider_class() per request — the same shape
    test_guest_appointment_pay.py established for _FakeProvider.calls.
    start_payment is not exercised by webhook tests and raises if reached."""

    calls: ClassVar[list[dict]] = []

    def verify_signature(self, *, payload: bytes, signature: str) -> bool:
        return True

    def parse_webhook_event(self, *, payload: bytes) -> dict[str, str]:
        # Same generic-envelope-from-raw-bytes shape as
        # MockPaymentProvider.parse_webhook_event — this test file's bodies
        # already speak that envelope directly.
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        return {
            "event_id": str(data.get("event_id", "")),
            "event_type": str(data.get("event_type", "")),
            "provider_reference_id": str(data.get("provider_reference_id", "")),
        }

    def start_payment(self, *, amount, currency, reference):
        raise NotImplementedError("not exercised by webhook tests")

    def refund(self, *, provider_reference_id, reference, amount, currency):
        type(self).calls.append(
            {"provider_reference_id": provider_reference_id, "reference": reference}
        )
        return _FakeRefundIntent(provider_reference_id=f"fake_refund_{len(type(self).calls)}")


class _RejectingProvider:
    """verify_signature always rejects. start_payment/refund raise if ever
    reached — proves the view stops at signature verification and never
    gets to the point of calling either."""

    def verify_signature(self, *, payload: bytes, signature: str) -> bool:
        return False

    def parse_webhook_event(self, *, payload: bytes) -> dict[str, str]:
        raise AssertionError("must not be called past an invalid signature")

    def start_payment(self, *, amount, currency, reference):
        raise AssertionError("must not be called past an invalid signature")

    def refund(self, *, provider_reference_id, reference, amount, currency):
        raise AssertionError("must not be called past an invalid signature")


class _AmountRecordingProvider(_FakeProvider):
    """Same as _FakeProvider, but refund() also records amount/currency, on
    its OWN class-level list — kept separate so _FakeProvider.calls' exact
    dict shape, which existing tests compare with ==, is unchanged."""

    calls: ClassVar[list[dict]] = []

    def refund(self, *, provider_reference_id, reference, amount, currency):
        type(self).calls.append(
            {
                "provider_reference_id": provider_reference_id,
                "reference": reference,
                "amount": amount,
                "currency": currency,
            }
        )
        return _FakeRefundIntent(provider_reference_id=f"fake_refund_{len(type(self).calls)}")


class _FailingRefundProvider(_FakeProvider):
    """refund() records the attempt, then raises — stand-in for a provider
    whose refund call fails (network error, provider-reported failure).
    initiate_refund wraps any exception into PaymentProviderError."""

    calls: ClassVar[list[dict]] = []

    def refund(self, *, provider_reference_id, reference, amount, currency):
        type(self).calls.append(
            {"provider_reference_id": provider_reference_id, "reference": reference}
        )
        raise RuntimeError("simulated provider refund failure")


@pytest.fixture(autouse=True)
def _reset_fake_provider_calls():
    _FakeProvider.calls = []
    _AmountRecordingProvider.calls = []
    _FailingRefundProvider.calls = []
    yield


def _webhook_url() -> str:
    return "/api/v1/webhooks/payments/"


def _make_pending_appointment(salon, specialist, service, customer, *, start=START):
    return make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=start,
        status=AppointmentStatus.PENDING_PAYMENT,
    )


def _make_expired_appointment(salon, specialist, service, customer, *, start=START):
    return make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=start,
        status=AppointmentStatus.EXPIRED,
    )


def _make_cancelled_appointment(salon, specialist, service, customer, *, start=START):
    return make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=start,
        status=AppointmentStatus.CANCELLED,
    )


def _make_confirmed_appointment(salon, specialist, service, customer, *, start=START):
    return make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=start,
        status=AppointmentStatus.CONFIRMED,
    )


def _make_payment(salon, appt, *, status, provider_reference_id):
    with tenant_context(salon.id):
        return Payment.objects.create(
            salon=salon,
            appointment=appt,
            amount=Decimal("100.00"),
            currency=salon.currency,
            status=status,
            provider_reference_id=provider_reference_id,
        )


# --- 1. payment_succeeded happy path ---------------------------------------


def test_payment_succeeded_transitions_payment_and_appointment_and_returns_200(
    client, salon, specialist, service, customer, monkeypatch
):
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _FakeProvider)
    appt = _make_pending_appointment(salon, specialist, service, customer)
    payment = _make_payment(
        salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_1"
    )

    response = client.post(
        _webhook_url(),
        {"event_id": "evt_1", "event_type": "payment_succeeded", "provider_reference_id": "ref_1"},
        format="json",
        HTTP_X_SIGNATURE="sig",
    )

    assert response.status_code == 200
    with tenant_context(salon.id):
        payment_row = Payment.objects.get(pk=payment.pk)
        appt_row = Appointment.objects.get(pk=appt.pk)
    assert payment_row.status == PaymentStatus.SUCCEEDED
    assert appt_row.status == AppointmentStatus.CONFIRMED
    assert ProcessedWebhookEvent.objects.filter(provider_event_id="evt_1").count() == 1


# --- 2. payment_failed happy path -------------------------------------------


def test_payment_failed_marks_payment_failed_and_leaves_appointment_pending_payment(
    client, salon, specialist, service, customer, monkeypatch
):
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _FakeProvider)
    appt = _make_pending_appointment(salon, specialist, service, customer)
    payment = _make_payment(
        salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_2"
    )

    response = client.post(
        _webhook_url(),
        {"event_id": "evt_2", "event_type": "payment_failed", "provider_reference_id": "ref_2"},
        format="json",
        HTTP_X_SIGNATURE="sig",
    )

    assert response.status_code == 200
    with tenant_context(salon.id):
        payment_row = Payment.objects.get(pk=payment.pk)
        appt_row = Appointment.objects.get(pk=appt.pk)
    assert payment_row.status == PaymentStatus.FAILED
    assert appt_row.status == AppointmentStatus.PENDING_PAYMENT
    assert ProcessedWebhookEvent.objects.filter(provider_event_id="evt_2").count() == 1


# --- 3. refund_succeeded happy path -----------------------------------------


def test_refund_succeeded_marks_payment_refunded(
    client, salon, specialist, service, customer, monkeypatch
):
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _FakeProvider)
    appt = _make_confirmed_appointment(salon, specialist, service, customer)
    payment = _make_payment(
        salon, appt, status=PaymentStatus.REFUND_PENDING, provider_reference_id="ref_3"
    )

    response = client.post(
        _webhook_url(),
        {"event_id": "evt_3", "event_type": "refund_succeeded", "provider_reference_id": "ref_3"},
        format="json",
        HTTP_X_SIGNATURE="sig",
    )

    assert response.status_code == 200
    with tenant_context(salon.id):
        payment_row = Payment.objects.get(pk=payment.pk)
        appt_row = Appointment.objects.get(pk=appt.pk)
    assert payment_row.status == PaymentStatus.REFUNDED
    assert appt_row.status == AppointmentStatus.CONFIRMED
    assert _FakeProvider.calls == []
    assert ProcessedWebhookEvent.objects.filter(provider_event_id="evt_3").count() == 1


# --- 4. EXPIRED branch / sweep-vs-webhook rule 3 ----------------------------


def test_payment_succeeded_on_expired_appointment_does_not_resurrect_and_initiates_refund(
    client, salon, specialist, service, customer, monkeypatch
):
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _FakeProvider)
    appt = _make_expired_appointment(salon, specialist, service, customer)
    payment = _make_payment(
        salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_4"
    )

    response = client.post(
        _webhook_url(),
        {"event_id": "evt_4", "event_type": "payment_succeeded", "provider_reference_id": "ref_4"},
        format="json",
        HTTP_X_SIGNATURE="sig",
    )

    assert response.status_code == 200
    with tenant_context(salon.id):
        payment_row = Payment.objects.get(pk=payment.pk)
        appt_row = Appointment.objects.get(pk=appt.pk)
    assert payment_row.status == PaymentStatus.REFUND_PENDING
    assert appt_row.status == AppointmentStatus.EXPIRED
    assert _FakeProvider.calls == [{"provider_reference_id": "ref_4", "reference": str(appt.id)}]
    assert ProcessedWebhookEvent.objects.filter(provider_event_id="evt_4").count() == 1


# --- 5. Idempotency: duplicate event_id delivery ----------------------------


def test_duplicate_event_id_second_delivery_is_200_noop(
    client, salon, specialist, service, customer, monkeypatch
):
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _FakeProvider)
    appt = _make_pending_appointment(salon, specialist, service, customer)
    payment = _make_payment(
        salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_5"
    )
    body = {
        "event_id": "evt_5",
        "event_type": "payment_succeeded",
        "provider_reference_id": "ref_5",
    }

    first = client.post(_webhook_url(), body, format="json", HTTP_X_SIGNATURE="sig")
    second = client.post(_webhook_url(), body, format="json", HTTP_X_SIGNATURE="sig")

    assert first.status_code == 200
    assert second.status_code == 200
    with tenant_context(salon.id):
        payment_row = Payment.objects.get(pk=payment.pk)
        appt_row = Appointment.objects.get(pk=appt.pk)
    assert payment_row.status == PaymentStatus.SUCCEEDED
    assert appt_row.status == AppointmentStatus.CONFIRMED
    assert ProcessedWebhookEvent.objects.filter(provider_event_id="evt_5").count() == 1


# --- 6. Unknown/ignored event_type ------------------------------------------


def test_unknown_event_type_is_200_and_writes_no_state_or_ledger_row(
    client, salon, specialist, service, customer, monkeypatch
):
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _FakeProvider)
    appt = _make_pending_appointment(salon, specialist, service, customer)
    payment = _make_payment(
        salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_6"
    )

    response = client.post(
        _webhook_url(),
        {
            "event_id": "evt_6",
            "event_type": "something_unrecognized",
            "provider_reference_id": "ref_6",
        },
        format="json",
        HTTP_X_SIGNATURE="sig",
    )

    assert response.status_code == 200
    with tenant_context(salon.id):
        payment_row = Payment.objects.get(pk=payment.pk)
        appt_row = Appointment.objects.get(pk=appt.pk)
    assert payment_row.status == PaymentStatus.PENDING
    assert appt_row.status == AppointmentStatus.PENDING_PAYMENT
    assert ProcessedWebhookEvent.objects.filter(provider_event_id="evt_6").count() == 0


# --- 7. Invalid signature ----------------------------------------------------


def test_invalid_signature_is_401_and_writes_no_state_or_ledger_row(
    client, salon, specialist, service, customer, monkeypatch
):
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _RejectingProvider)
    appt = _make_pending_appointment(salon, specialist, service, customer)
    payment = _make_payment(
        salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_7"
    )

    response = client.post(
        _webhook_url(),
        {"event_id": "evt_7", "event_type": "payment_succeeded", "provider_reference_id": "ref_7"},
        format="json",
        HTTP_X_SIGNATURE="bad-sig",
    )

    assert response.status_code == 401
    with tenant_context(salon.id):
        payment_row = Payment.objects.get(pk=payment.pk)
        appt_row = Appointment.objects.get(pk=appt.pk)
    assert payment_row.status == PaymentStatus.PENDING
    assert appt_row.status == AppointmentStatus.PENDING_PAYMENT
    assert ProcessedWebhookEvent.objects.filter(provider_event_id="evt_7").count() == 0


# --- 8. Broken/unparseable body ----------------------------------------------


def test_malformed_json_body_is_400_and_nothing_happens(client, salon, monkeypatch):
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _FakeProvider)

    response = client.post(
        _webhook_url(),
        data="{not valid json",
        content_type="application/json",
        HTTP_X_SIGNATURE="sig",
    )

    assert response.status_code == 400
    assert ProcessedWebhookEvent.objects.count() == 0


def test_body_missing_required_field_is_400_and_nothing_happens(client, salon, monkeypatch):
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _FakeProvider)

    response = client.post(
        _webhook_url(),
        {"event_id": "evt_9", "provider_reference_id": "ref_9"},
        format="json",
        HTTP_X_SIGNATURE="sig",
    )

    assert response.status_code == 400
    assert ProcessedWebhookEvent.objects.filter(provider_event_id="evt_9").count() == 0


# --- 9. provider_reference_id not found --------------------------------------


def test_unknown_provider_reference_id_is_404_and_logs_warning(client, salon, monkeypatch, caplog):
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _FakeProvider)

    with caplog.at_level(logging.WARNING, logger="payments.views"):
        response = client.post(
            _webhook_url(),
            {
                "event_id": "evt_10",
                "event_type": "payment_succeeded",
                "provider_reference_id": "ref_missing",
            },
            format="json",
            HTTP_X_SIGNATURE="sig",
        )

    assert response.status_code == 404
    assert "ref_missing" in caplog.text
    assert ProcessedWebhookEvent.objects.filter(provider_event_id="evt_10").count() == 0


# --- 10. EXPIRED branch: provider refund failure ------------------------------


def test_payment_succeeded_on_expired_appointment_refund_failure_is_502_and_leaves_refund_pending(
    client, salon, specialist, service, customer, monkeypatch
):
    """Characterizes the EXPIRED branch's current failure behavior, which the
    CANCELLED branch below must mirror (docs/DECISIONS.md § Stage 15
    planning, item 14 design details). initiate_refund commits
    REFUND_PENDING before calling the provider, so the failure leaves the row
    there (for the § Stage 8.G stuck-refund sweep to flag); its
    PaymentProviderError is not caught by the view and maps to 502. The
    ledger row was committed by the view's own atomic() block before the
    refund call, so a provider retry of this event is a no-op."""
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _FailingRefundProvider)
    appt = _make_expired_appointment(salon, specialist, service, customer)
    payment = _make_payment(
        salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_11"
    )

    response = client.post(
        _webhook_url(),
        {
            "event_id": "evt_11",
            "event_type": "payment_succeeded",
            "provider_reference_id": "ref_11",
        },
        format="json",
        HTTP_X_SIGNATURE="sig",
    )

    assert response.status_code == 502
    with tenant_context(salon.id):
        payment_row = Payment.objects.get(pk=payment.pk)
        appt_row = Appointment.objects.get(pk=appt.pk)
    assert payment_row.status == PaymentStatus.REFUND_PENDING
    assert appt_row.status == AppointmentStatus.EXPIRED
    assert _FailingRefundProvider.calls == [
        {"provider_reference_id": "ref_11", "reference": str(appt.id)}
    ]
    assert ProcessedWebhookEvent.objects.filter(provider_event_id="evt_11").count() == 1


# --- 11. CANCELLED branch (item 14 design details, money fix) ----------------
#
# docs/DECISIONS.md § Stage 15 planning, item 14 design details: a
# payment_succeeded webhook on a CANCELLED appointment triggers a FULL refund
# of payment.amount, mirroring the EXPIRED branch, and never confirms the
# appointment. START (2026-08-22) is already in the past, i.e. well inside
# the <24h window where the customer-cancellation deposit-forfeit rule would
# refuse a refund — the full refund here must not depend on that rule.


def test_payment_succeeded_on_cancelled_appointment_initiates_full_refund_and_stays_cancelled(
    client, salon, specialist, service, customer, monkeypatch
):
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _AmountRecordingProvider)
    appt = _make_cancelled_appointment(salon, specialist, service, customer)
    payment = _make_payment(
        salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_12"
    )

    response = client.post(
        _webhook_url(),
        {
            "event_id": "evt_12",
            "event_type": "payment_succeeded",
            "provider_reference_id": "ref_12",
        },
        format="json",
        HTTP_X_SIGNATURE="sig",
    )

    assert response.status_code == 200
    with tenant_context(salon.id):
        payment_row = Payment.objects.get(pk=payment.pk)
        appt_row = Appointment.objects.get(pk=appt.pk)
    assert payment_row.status == PaymentStatus.REFUND_PENDING
    assert appt_row.status == AppointmentStatus.CANCELLED
    assert _AmountRecordingProvider.calls == [
        {
            "provider_reference_id": "ref_12",
            "reference": str(appt.id),
            "amount": payment.amount,
            "currency": payment.currency,
        }
    ]
    assert ProcessedWebhookEvent.objects.filter(provider_event_id="evt_12").count() == 1


def test_payment_succeeded_on_cancelled_appointment_duplicate_event_id_refunds_once(
    client, salon, specialist, service, customer, monkeypatch
):
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _FakeProvider)
    appt = _make_cancelled_appointment(salon, specialist, service, customer)
    payment = _make_payment(
        salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_13"
    )
    body = {
        "event_id": "evt_13",
        "event_type": "payment_succeeded",
        "provider_reference_id": "ref_13",
    }

    first = client.post(_webhook_url(), body, format="json", HTTP_X_SIGNATURE="sig")
    second = client.post(_webhook_url(), body, format="json", HTTP_X_SIGNATURE="sig")

    assert first.status_code == 200
    assert second.status_code == 200
    with tenant_context(salon.id):
        payment_row = Payment.objects.get(pk=payment.pk)
        appt_row = Appointment.objects.get(pk=appt.pk)
    assert payment_row.status == PaymentStatus.REFUND_PENDING
    assert appt_row.status == AppointmentStatus.CANCELLED
    assert _FakeProvider.calls == [{"provider_reference_id": "ref_13", "reference": str(appt.id)}]
    assert ProcessedWebhookEvent.objects.filter(provider_event_id="evt_13").count() == 1


def test_payment_succeeded_on_cancelled_appointment_second_event_id_refunds_only_once(
    client, salon, specialist, service, customer, monkeypatch
):
    """A different event_id for the same Payment (e.g. the provider sending
    a second success notification with a different reasonCode) passes the
    ProcessedWebhookEvent ledger check — only the Payment's own status
    (already REFUND_PENDING after the first event) can stop a second
    refund."""
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _FakeProvider)
    appt = _make_cancelled_appointment(salon, specialist, service, customer)
    payment = _make_payment(
        salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_14"
    )

    first = client.post(
        _webhook_url(),
        {
            "event_id": "evt_14a",
            "event_type": "payment_succeeded",
            "provider_reference_id": "ref_14",
        },
        format="json",
        HTTP_X_SIGNATURE="sig",
    )
    second = client.post(
        _webhook_url(),
        {
            "event_id": "evt_14b",
            "event_type": "payment_succeeded",
            "provider_reference_id": "ref_14",
        },
        format="json",
        HTTP_X_SIGNATURE="sig",
    )

    assert first.status_code == 200
    assert second.status_code == 200
    with tenant_context(salon.id):
        payment_row = Payment.objects.get(pk=payment.pk)
        appt_row = Appointment.objects.get(pk=appt.pk)
    assert payment_row.status == PaymentStatus.REFUND_PENDING
    assert appt_row.status == AppointmentStatus.CANCELLED
    assert _FakeProvider.calls == [{"provider_reference_id": "ref_14", "reference": str(appt.id)}]
    assert ProcessedWebhookEvent.objects.filter(provider_event_id="evt_14a").count() == 1
    assert ProcessedWebhookEvent.objects.filter(provider_event_id="evt_14b").count() == 1


def test_payment_succeeded_on_cancelled_appointment_refund_failure_matches_expired_branch(
    client, salon, specialist, service, customer, monkeypatch
):
    """Same outcome as test 10 (the EXPIRED branch's failure case): 502,
    Payment left REFUND_PENDING, ledger row recorded, appointment untouched."""
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _FailingRefundProvider)
    appt = _make_cancelled_appointment(salon, specialist, service, customer)
    payment = _make_payment(
        salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_15"
    )

    response = client.post(
        _webhook_url(),
        {
            "event_id": "evt_15",
            "event_type": "payment_succeeded",
            "provider_reference_id": "ref_15",
        },
        format="json",
        HTTP_X_SIGNATURE="sig",
    )

    assert response.status_code == 502
    with tenant_context(salon.id):
        payment_row = Payment.objects.get(pk=payment.pk)
        appt_row = Appointment.objects.get(pk=appt.pk)
    assert payment_row.status == PaymentStatus.REFUND_PENDING
    assert appt_row.status == AppointmentStatus.CANCELLED
    assert _FailingRefundProvider.calls == [
        {"provider_reference_id": "ref_15", "reference": str(appt.id)}
    ]
    assert ProcessedWebhookEvent.objects.filter(provider_event_id="evt_15").count() == 1
