"""
Stage 15 — persisted refund decision, `Payment.refund_due` (docs/DECISIONS.md
§ "Persisted refund decision (`Payment.refund_due`)"). Tests only — written
before the field, its migration, or any production change exists. Every test
that reads `refund_due` fails today on the missing attribute; the lock-order
tests (17, 18) and the in-transaction tests (14, 14b) also fail on the
current query order, independently of the field.

Decisions this file is written against (agreed 17.10.2026 and 19.10.2026):

- `refund_due` is set to True in the same transaction that decides a refund
  is owed: `cancel_appointment` when the cancellation is refund-eligible AND
  the Payment is SUCCEEDED; the payment webhook when a Payment becomes
  SUCCEEDED while its appointment is already EXPIRED or CANCELLED.
- An eligible cancellation of a still-PENDING Payment leaves it False: no
  money was collected yet, and the webhook decides later.
- It is never cleared, including after a refund_succeeded webhook.
- Lock order: Appointment first, then Payment, in both the cancel path and
  the webhook. Checked through CaptureQueriesContext (same precedent as
  test_booking_account_create.py's FOR UPDATE test), not with threads.

Real-DB integration tests throughout. Monkeypatching only where noted: the
provider_class substitution, `initiate_refund` patched to raise (tests 11,
13), and test 19's `select_for_update` race double. All `now` values passed
to services are tz-aware UTC literals, never `timezone.now()`. Every
appointment/customer/specialist/service/payment created here passes `salon=`
explicitly (Stage 6 shell-seeding trap, per CLAUDE.md).
"""

import datetime as dt
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

import pytest
from django.db import connection, connections
from django.db.models.query import QuerySet
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from accounts.models import Customer
from booking.models import Appointment, AppointmentStatus, CancelledBy
from booking.services import cancel_appointment
from catalog.models import Service, ServiceCategory
from core.tenancy import tenant_context
from payments.models import Payment, PaymentStatus, ProcessedWebhookEvent
from payments.services import initiate_payment
from payments.views import PaymentWebhookView
from specialists.models import Specialist
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

START = dt.datetime(2026, 8, 20, 10, 0, tzinfo=dt.UTC)
ELIGIBLE_NOW = START - dt.timedelta(hours=48)
LATE_NOW = START - dt.timedelta(hours=1)


@pytest.fixture
def client() -> APIClient:
    return APIClient()


# --- Provider doubles ---------------------------------------------------------


@dataclass(frozen=True)
class _FakeIntent:
    provider_reference_id: str
    provider_data: object = None


class _FakeRefundProvider:
    """cancel_appointment's provider: records every refund() call. Same shape
    as test_booking_cancel_appointment.py's _FakeRefundProvider."""

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
        return _FakeIntent(provider_reference_id=f"fake_refund_{len(self.calls)}")


class _RaisingRefundProvider(_FakeRefundProvider):
    def refund(self, *, provider_reference_id, reference, amount, currency):
        super().refund(
            provider_reference_id=provider_reference_id,
            reference=reference,
            amount=amount,
            currency=currency,
        )
        raise RuntimeError("network down")


class _WebhookProvider:
    """PaymentWebhookView's provider: accepts every signature, parses the
    generic envelope, records refund() calls on a CLASS-level list (the view
    builds a fresh provider_class() per request). Same shape as
    test_payments_webhook.py's _AmountRecordingProvider."""

    calls: ClassVar[list[dict]] = []

    def verify_signature(self, *, payload: bytes, signature: str) -> bool:
        return True

    def parse_webhook_event(self, *, payload: bytes) -> dict[str, str]:
        data = json.loads(payload)
        return {
            "event_id": str(data.get("event_id", "")),
            "event_type": str(data.get("event_type", "")),
            "provider_reference_id": str(data.get("provider_reference_id", "")),
        }

    def start_payment(self, *, amount, currency, reference):
        raise NotImplementedError("not exercised by webhook tests")

    def refund(self, *, provider_reference_id, reference, amount, currency):
        type(self).calls.append(
            {
                "provider_reference_id": provider_reference_id,
                "reference": reference,
                "amount": amount,
                "currency": currency,
            }
        )
        return _FakeIntent(provider_reference_id=f"fake_refund_{len(type(self).calls)}")


class _FailingWebhookProvider(_WebhookProvider):
    calls: ClassVar[list[dict]] = []

    def refund(self, *, provider_reference_id, reference, amount, currency):
        type(self).calls.append({"provider_reference_id": provider_reference_id})
        raise RuntimeError("simulated provider refund failure")


class _StartPaymentProvider:
    def start_payment(self, *, amount, currency, reference):
        return _FakeIntent(provider_reference_id=f"start_ref_{reference}")

    def refund(self, *, provider_reference_id, reference, amount, currency):
        raise NotImplementedError("not exercised by initiate_payment")


@pytest.fixture(autouse=True)
def _reset_webhook_provider_calls():
    _WebhookProvider.calls = []
    _FailingWebhookProvider.calls = []
    yield


# --- Helpers ------------------------------------------------------------------


def _appt(salon, specialist, service, customer, *, status):
    return make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=status,
    )


def _payment(salon, appt, *, status, provider_reference_id="ref_due"):
    with tenant_context(salon.id):
        return Payment.objects.create(
            salon=salon,
            appointment=appt,
            amount=Decimal("100.00"),
            currency=salon.currency,
            status=status,
            provider_reference_id=provider_reference_id,
        )


def _payment_row(salon, payment_id) -> Payment:
    with tenant_context(salon.id):
        return Payment.objects.get(pk=payment_id)


def _appt_row(salon, appt_id) -> Appointment:
    with tenant_context(salon.id):
        return Appointment.objects.get(pk=appt_id)


def _cancel(salon, appt, *, cancelled_by, now, provider=None):
    with tenant_context(salon.id):
        return cancel_appointment(
            appointment_id=appt.id,
            salon=salon,
            cancelled_by=cancelled_by,
            now=now,
            provider=provider if provider is not None else _FakeRefundProvider(),
        )


def _post_webhook(client, *, event_id, event_type, provider_reference_id):
    return client.post(
        "/api/v1/webhooks/payments/",
        {
            "event_id": event_id,
            "event_type": event_type,
            "provider_reference_id": provider_reference_id,
        },
        format="json",
        HTTP_X_SIGNATURE="sig",
    )


def _other_salon_chain(other_salon):
    """A specialist/service/customer for `other_salon`, same construction as
    test_booking_account_appointment_cancel.py's other_salon_appointment."""
    with tenant_context(other_salon.id):
        category = ServiceCategory.objects.create(salon=other_salon, name={"en": "Brows"})
        other_service = Service.objects.create(
            salon=other_salon,
            category=category,
            name={"en": "Brow shaping"},
            duration_minutes=30,
            price="400.00",
            buffer_minutes=0,
        )
        other_specialist = Specialist.objects.create(salon=other_salon, name={"en": "Olga"})
        other_customer = Customer.objects.create(
            salon=other_salon, name="Bob", email="bob@example.com", phone="+10000000009"
        )
    return other_specialist, other_service, other_customer


_FROM_TABLE = re.compile(r'FROM "(\w+)"')


def _locked_table(sql: str) -> str | None:
    """The table a SELECT ... FOR UPDATE reads from, or None for any other
    statement."""
    if "FOR UPDATE" not in sql.upper():
        return None
    match = _FROM_TABLE.search(sql)
    return match.group(1) if match else sql


def _for_update_tables(queries) -> list[str]:
    return [t for q in queries if (t := _locked_table(q["sql"])) is not None]


def _is_refund_due_update(sql: str) -> bool:
    return sql.startswith('UPDATE "payments_payment"') and '"refund_due"' in sql


def _cancel_block_queries(captured) -> list[dict]:
    """The queries of cancel_appointment's own atomic() block. Inside the
    test's outer transaction that block is a savepoint: the nearest SAVEPOINT
    before the Appointment lock, up to and including its RELEASE SAVEPOINT."""
    sqls = [q["sql"].strip() for q in captured]
    lock_idx = next(
        (i for i, s in enumerate(sqls) if _locked_table(s) == "booking_appointment"), None
    )
    assert lock_idx is not None, f"no Appointment FOR UPDATE captured: {sqls}"
    savepoint_idxs = [i for i in range(lock_idx) if sqls[i].startswith("SAVEPOINT ")]
    assert savepoint_idxs, f"no SAVEPOINT captured before the Appointment lock: {sqls}"
    sp_idx = savepoint_idxs[-1]
    release = "RELEASE " + sqls[sp_idx]
    release_idx = next((i for i in range(lock_idx, len(sqls)) if sqls[i] == release), None)
    assert release_idx is not None, f"no {release!r} captured after the lock: {sqls}"
    return captured[sp_idx : release_idx + 1]


# --- 1-4. Cancel path: refund-eligible cancellations set refund_due ----------


@pytest.mark.parametrize(
    ("cancelled_by", "now"),
    [
        (CancelledBy.STAFF, ELIGIBLE_NOW),  # 1
        (CancelledBy.STAFF, LATE_NOW),  # 2: salon-initiated refunds regardless of timing
        (CancelledBy.CUSTOMER, START - dt.timedelta(hours=24)),  # 3: exactly the cutoff
        (CancelledBy.GUEST, ELIGIBLE_NOW),  # 4
    ],
    ids=["staff_48h", "staff_1h", "customer_exactly_24h", "guest_48h"],
)
def test_eligible_cancel_of_a_succeeded_payment_sets_refund_due(
    salon, specialist, service, customer, cancelled_by, now
):
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.CONFIRMED)
    payment = _payment(salon, appt, status=PaymentStatus.SUCCEEDED)
    provider = _FakeRefundProvider()

    _cancel(salon, appt, cancelled_by=cancelled_by, now=now, provider=provider)

    row = _payment_row(salon, payment.id)
    assert row.refund_due is True
    assert row.status == PaymentStatus.REFUND_PENDING
    assert len(provider.calls) == 1


# --- 5-6. Late customer/guest cancel: deposit withheld, refund_due False -----


@pytest.mark.parametrize(
    "cancelled_by", [CancelledBy.CUSTOMER, CancelledBy.GUEST], ids=["customer", "guest"]
)
def test_late_customer_cancel_of_a_succeeded_payment_leaves_refund_due_false(
    salon, specialist, service, customer, cancelled_by
):
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.CONFIRMED)
    payment = _payment(salon, appt, status=PaymentStatus.SUCCEEDED)
    provider = _FakeRefundProvider()

    _cancel(
        salon,
        appt,
        cancelled_by=cancelled_by,
        now=START - dt.timedelta(hours=23, minutes=59),
        provider=provider,
    )

    row = _payment_row(salon, payment.id)
    assert row.refund_due is False
    assert row.status == PaymentStatus.SUCCEEDED
    assert provider.calls == []


# --- 7. Eligible cancel of a still-PENDING payment: the webhook decides ------


def test_eligible_cancel_of_a_pending_payment_leaves_refund_due_false(
    salon, specialist, service, customer
):
    """No money collected yet, so nothing is owed now. If the payment
    succeeds later, the webhook's late-payment path decides (test 15)."""
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.PENDING_PAYMENT)
    payment = _payment(salon, appt, status=PaymentStatus.PENDING)
    provider = _FakeRefundProvider()

    _cancel(salon, appt, cancelled_by=CancelledBy.STAFF, now=ELIGIBLE_NOW, provider=provider)

    row = _payment_row(salon, payment.id)
    assert row.refund_due is False
    assert row.status == PaymentStatus.PENDING
    assert provider.calls == []


# --- 8-9. Webhook late-payment path sets refund_due ---------------------------


@pytest.mark.parametrize(
    "appt_status",
    [AppointmentStatus.EXPIRED, AppointmentStatus.CANCELLED],
    ids=["expired", "cancelled"],
)
def test_payment_succeeded_on_a_released_appointment_sets_refund_due(
    client, salon, specialist, service, customer, monkeypatch, appt_status
):
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _WebhookProvider)
    appt = _appt(salon, specialist, service, customer, status=appt_status)
    payment = _payment(salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_8")

    response = _post_webhook(
        client, event_id="evt_8", event_type="payment_succeeded", provider_reference_id="ref_8"
    )

    assert response.status_code == 200
    row = _payment_row(salon, payment.id)
    assert row.refund_due is True
    assert row.status == PaymentStatus.REFUND_PENDING
    assert _appt_row(salon, appt.id).status == appt_status
    assert len(_WebhookProvider.calls) == 1


# --- 10. Webhook normal confirmation: nothing owed ---------------------------


def test_payment_succeeded_on_a_pending_payment_appointment_leaves_refund_due_false(
    client, salon, specialist, service, customer, monkeypatch
):
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _WebhookProvider)
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.PENDING_PAYMENT)
    payment = _payment(salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_10")

    response = _post_webhook(
        client, event_id="evt_10", event_type="payment_succeeded", provider_reference_id="ref_10"
    )

    assert response.status_code == 200
    row = _payment_row(salon, payment.id)
    assert row.refund_due is False
    assert row.status == PaymentStatus.SUCCEEDED
    assert _appt_row(salon, appt.id).status == AppointmentStatus.CONFIRMED
    assert _WebhookProvider.calls == []


# --- 11-13. The decision survives a failure after it --------------------------


def test_cancel_decision_survives_initiate_refund_raising(
    salon, specialist, service, customer, monkeypatch
):
    """initiate_refund patched to raise something cancel_appointment does not
    swallow: the cancellation and the recorded decision are both already
    committed, and the Payment is still SUCCEEDED (no refund started), which
    is exactly the row the planned retry sweep will look for."""

    def _boom(**kwargs):
        raise RuntimeError("crash after the cancel transaction")

    monkeypatch.setattr("booking.services.initiate_refund", _boom)
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.CONFIRMED)
    payment = _payment(salon, appt, status=PaymentStatus.SUCCEEDED)

    with pytest.raises(RuntimeError, match="crash after the cancel transaction"):
        _cancel(salon, appt, cancelled_by=CancelledBy.STAFF, now=ELIGIBLE_NOW)

    assert _appt_row(salon, appt.id).status == AppointmentStatus.CANCELLED
    row = _payment_row(salon, payment.id)
    assert row.status == PaymentStatus.SUCCEEDED
    assert row.refund_due is True


def test_cancel_provider_refund_failure_keeps_refund_due(salon, specialist, service, customer):
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.CONFIRMED)
    payment = _payment(salon, appt, status=PaymentStatus.SUCCEEDED)
    provider = _RaisingRefundProvider()

    _cancel(salon, appt, cancelled_by=CancelledBy.STAFF, now=ELIGIBLE_NOW, provider=provider)

    assert len(provider.calls) == 1
    row = _payment_row(salon, payment.id)
    assert row.status == PaymentStatus.REFUND_PENDING
    assert row.refund_due is True


def test_webhook_decision_survives_initiate_refund_raising(
    client, salon, specialist, service, customer, monkeypatch
):
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _WebhookProvider)

    def _boom(**kwargs):
        raise RuntimeError("crash after the webhook transaction")

    monkeypatch.setattr("payments.views.initiate_refund", _boom)
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.CANCELLED)
    payment = _payment(salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_13")

    # core.exceptions.exception_handler turns the unrecognized exception into
    # the generic 500 envelope (DEBUG is off under test) rather than
    # re-raising it.
    response = _post_webhook(
        client, event_id="evt_13", event_type="payment_succeeded", provider_reference_id="ref_13"
    )

    assert response.status_code == 500
    row = _payment_row(salon, payment.id)
    assert row.status == PaymentStatus.SUCCEEDED
    assert row.refund_due is True
    assert ProcessedWebhookEvent.objects.filter(provider_event_id="evt_13").count() == 1


def test_webhook_provider_refund_failure_keeps_refund_due(
    client, salon, specialist, service, customer, monkeypatch
):
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _FailingWebhookProvider)
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.EXPIRED)
    payment = _payment(salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_13b")

    response = _post_webhook(
        client, event_id="evt_13b", event_type="payment_succeeded", provider_reference_id="ref_13b"
    )

    assert response.status_code == 502
    row = _payment_row(salon, payment.id)
    assert row.status == PaymentStatus.REFUND_PENDING
    assert row.refund_due is True


# --- 14. The decision is written inside the deciding transaction -------------


def test_cancel_writes_refund_due_inside_its_own_transaction(salon, specialist, service, customer):
    """Distinguishes "set inside the cancel transaction" from "set after its
    commit, just before initiate_refund" — tests 11/12 cannot tell those
    apart. The cancel block is a savepoint inside the test's transaction, so
    the UPDATE must land between its SAVEPOINT and RELEASE SAVEPOINT."""
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.CONFIRMED)
    _payment(salon, appt, status=PaymentStatus.SUCCEEDED)

    with CaptureQueriesContext(connection) as ctx:
        _cancel(salon, appt, cancelled_by=CancelledBy.STAFF, now=ELIGIBLE_NOW)

    block = _cancel_block_queries(ctx.captured_queries)
    assert any(_is_refund_due_update(q["sql"]) for q in block), (
        "expected an UPDATE of payments_payment.refund_due inside the cancel "
        f"transaction, got: {[q['sql'] for q in block]}"
    )


def test_webhook_writes_refund_due_before_recording_the_event(
    client, salon, specialist, service, customer, monkeypatch
):
    """The ProcessedWebhookEvent insert is the last statement of the
    webhook's transaction; once it commits, a redelivery is a no-op. The
    decision must be written before it, not after the commit."""
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _WebhookProvider)
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.CANCELLED)
    _payment(salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_14b")

    with CaptureQueriesContext(connection) as ctx:
        _post_webhook(
            client,
            event_id="evt_14b",
            event_type="payment_succeeded",
            provider_reference_id="ref_14b",
        )

    sqls = [q["sql"] for q in ctx.captured_queries]
    ledger_idx = next(
        i
        for i, s in enumerate(sqls)
        if s.startswith('INSERT INTO "payments_processedwebhookevent"')
    )
    due_idxs = [i for i, s in enumerate(sqls) if _is_refund_due_update(s)]
    assert due_idxs, f"expected an UPDATE of payments_payment.refund_due, got: {sqls}"
    assert due_idxs[0] < ledger_idx


# --- 15-16. Late cancel and late payment ---------------------------------------


def test_late_cancel_of_an_unpaid_booking_then_late_payment_sets_refund_due(
    client, salon, specialist, service, customer, monkeypatch
):
    """The case the decision record exists for: a late customer cancel of an
    unpaid booking records nothing; the payment confirmed afterwards is
    refunded in full, and that decision is recorded by the webhook."""
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _WebhookProvider)
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.PENDING_PAYMENT)
    payment = _payment(salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_15")
    cancel_provider = _FakeRefundProvider()

    _cancel(salon, appt, cancelled_by=CancelledBy.CUSTOMER, now=LATE_NOW, provider=cancel_provider)

    assert _payment_row(salon, payment.id).refund_due is False
    assert cancel_provider.calls == []

    response = _post_webhook(
        client, event_id="evt_15", event_type="payment_succeeded", provider_reference_id="ref_15"
    )

    assert response.status_code == 200
    row = _payment_row(salon, payment.id)
    assert row.refund_due is True
    assert row.status == PaymentStatus.REFUND_PENDING
    assert _WebhookProvider.calls == [
        {
            "provider_reference_id": "ref_15",
            "reference": str(appt.id),
            "amount": payment.amount,
            "currency": payment.currency,
        }
    ]


def test_late_cancel_of_a_paid_booking_then_another_success_event_keeps_refund_due_false(
    client, salon, specialist, service, customer, monkeypatch
):
    """The withheld case: CANCELLED + SUCCEEDED looks like the late-payment
    case above, but the payment was already SUCCEEDED before the cancel. A
    further payment_succeeded event (new event_id) must not turn it into a
    refund."""
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _WebhookProvider)
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.CONFIRMED)
    payment = _payment(salon, appt, status=PaymentStatus.SUCCEEDED, provider_reference_id="ref_16")

    _cancel(salon, appt, cancelled_by=CancelledBy.CUSTOMER, now=LATE_NOW)

    response = _post_webhook(
        client, event_id="evt_16", event_type="payment_succeeded", provider_reference_id="ref_16"
    )

    assert response.status_code == 200
    row = _payment_row(salon, payment.id)
    assert row.refund_due is False
    assert row.status == PaymentStatus.SUCCEEDED
    assert _WebhookProvider.calls == []


# --- refund_due is never cleared ------------------------------------------------


def test_refund_succeeded_keeps_refund_due_true(
    client, salon, specialist, service, customer, monkeypatch
):
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _WebhookProvider)
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.CONFIRMED)
    payment = _payment(
        salon, appt, status=PaymentStatus.SUCCEEDED, provider_reference_id="ref_kept"
    )
    _cancel(salon, appt, cancelled_by=CancelledBy.STAFF, now=ELIGIBLE_NOW)

    response = _post_webhook(
        client,
        event_id="evt_kept",
        event_type="refund_succeeded",
        provider_reference_id="ref_kept",
    )

    assert response.status_code == 200
    row = _payment_row(salon, payment.id)
    assert row.status == PaymentStatus.REFUNDED
    assert row.refund_due is True


# --- 17-18. Lock order: Appointment first, then Payment -----------------------


@pytest.mark.parametrize(
    "appt_status",
    [AppointmentStatus.PENDING_PAYMENT, AppointmentStatus.EXPIRED, AppointmentStatus.CANCELLED],
    ids=["pending_payment", "expired", "cancelled"],
)
def test_webhook_locks_the_appointment_before_the_payment(
    client, salon, specialist, service, customer, monkeypatch, appt_status
):
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _WebhookProvider)
    appt = _appt(salon, specialist, service, customer, status=appt_status)
    _payment(salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_17")

    with CaptureQueriesContext(connection) as ctx:
        response = _post_webhook(
            client,
            event_id="evt_17",
            event_type="payment_succeeded",
            provider_reference_id="ref_17",
        )

    assert response.status_code == 200
    tables = _for_update_tables(ctx.captured_queries)
    assert tables[:2] == ["booking_appointment", "payments_payment"], tables


def test_cancel_locks_the_payment_after_the_appointment_inside_its_transaction(
    salon, specialist, service, customer
):
    """Today cancel_appointment's own block locks only the Appointment; the
    Payment lock seen later belongs to initiate_refund's separate transaction,
    so only the block's own FOR UPDATEs are compared."""
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.CONFIRMED)
    _payment(salon, appt, status=PaymentStatus.SUCCEEDED)

    with CaptureQueriesContext(connection) as ctx:
        _cancel(salon, appt, cancelled_by=CancelledBy.STAFF, now=ELIGIBLE_NOW)

    block = _cancel_block_queries(ctx.captured_queries)
    assert _for_update_tables(block) == ["booking_appointment", "payments_payment"]


# --- 19. Guard: the webhook rechecks the Payment after locking ----------------


@pytest.mark.django_db(transaction=True)
def test_webhook_rechecks_payment_status_after_locking(
    client, salon, specialist, service, customer, monkeypatch
):
    """Intended to pass today and after the lock-order change. The webhook
    reads the Payment without a lock to find its salon and appointment; a
    competing delivery commits REFUND_PENDING after that read but before the
    first lock. The status recheck under the lock must see it and do
    nothing: no second refund.

    The competing write goes through a SEPARATE database connection, so it is
    a real commit by another session rather than a write inside the webhook's
    own transaction; transaction=True makes the test data visible to that
    connection. Patch target and one-shot guard as in
    test_payments_flag_stuck_refunds.py's race test; the first
    select_for_update of either model triggers it, whichever the webhook
    locks first."""
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _WebhookProvider)
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.CANCELLED)
    payment = _payment(salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_19")

    fired = {"done": False}
    original_select_for_update = QuerySet.select_for_update

    def _select_for_update_with_race(self, *args, **kwargs):
        if not fired["done"] and self.model in (Payment, Appointment):
            fired["done"] = True
            other = connections.create_connection("default")
            try:
                with other.cursor() as cursor:
                    cursor.execute(
                        "UPDATE payments_payment SET status = %s WHERE id = %s",
                        [PaymentStatus.REFUND_PENDING, payment.pk],
                    )
            finally:
                other.close()
        return original_select_for_update(self, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "select_for_update", _select_for_update_with_race)

    response = _post_webhook(
        client, event_id="evt_19", event_type="payment_succeeded", provider_reference_id="ref_19"
    )

    assert fired["done"] is True
    assert response.status_code == 200
    assert _payment_row(salon, payment.id).status == PaymentStatus.REFUND_PENDING
    assert _appt_row(salon, appt.id).status == AppointmentStatus.CANCELLED
    assert _WebhookProvider.calls == []


# --- 20-22. Two-salon isolation -----------------------------------------------


def test_cancel_sets_refund_due_only_on_its_own_salons_payment(
    salon, other_salon, specialist, service, customer
):
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.CONFIRMED)
    payment = _payment(salon, appt, status=PaymentStatus.SUCCEEDED, provider_reference_id="ref_a")
    o_specialist, o_service, o_customer = _other_salon_chain(other_salon)
    other_appt = _appt(
        other_salon, o_specialist, o_service, o_customer, status=AppointmentStatus.CONFIRMED
    )
    other_payment = _payment(
        other_salon, other_appt, status=PaymentStatus.SUCCEEDED, provider_reference_id="ref_b"
    )

    _cancel(salon, appt, cancelled_by=CancelledBy.STAFF, now=ELIGIBLE_NOW)

    assert _payment_row(salon, payment.id).refund_due is True
    other_row = _payment_row(other_salon, other_payment.id)
    assert other_row.refund_due is False
    assert other_row.status == PaymentStatus.SUCCEEDED


def test_cancel_through_another_salon_does_not_find_the_appointment_or_set_refund_due(
    salon, other_salon, specialist, service, customer
):
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.CONFIRMED)
    payment = _payment(salon, appt, status=PaymentStatus.SUCCEEDED)

    with tenant_context(other_salon.id), pytest.raises(Appointment.DoesNotExist):
        cancel_appointment(
            appointment_id=appt.id,
            salon=other_salon,
            cancelled_by=CancelledBy.STAFF,
            now=ELIGIBLE_NOW,
            provider=_FakeRefundProvider(),
        )

    assert _appt_row(salon, appt.id).status == AppointmentStatus.CONFIRMED
    row = _payment_row(salon, payment.id)
    assert row.refund_due is False
    assert row.status == PaymentStatus.SUCCEEDED


def test_webhook_sets_refund_due_only_on_the_referenced_salons_payment(
    client, salon, other_salon, specialist, service, customer, monkeypatch
):
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _WebhookProvider)
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.CANCELLED)
    payment = _payment(salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_a")
    o_specialist, o_service, o_customer = _other_salon_chain(other_salon)
    other_appt = _appt(
        other_salon, o_specialist, o_service, o_customer, status=AppointmentStatus.CANCELLED
    )
    other_payment = _payment(
        other_salon, other_appt, status=PaymentStatus.PENDING, provider_reference_id="ref_b"
    )

    response = _post_webhook(
        client, event_id="evt_22", event_type="payment_succeeded", provider_reference_id="ref_a"
    )

    assert response.status_code == 200
    assert _payment_row(salon, payment.id).refund_due is True
    other_row = _payment_row(other_salon, other_payment.id)
    assert other_row.refund_due is False
    assert other_row.status == PaymentStatus.PENDING


# --- Same-salon isolation: only the cancelled appointment's Payment ----------


def test_cancel_sets_refund_due_only_on_the_cancelled_appointments_payment(
    salon, specialist, service, customer
):
    """Two paid appointments in the same salon. The second one is cancelled,
    so a Payment lookup that ignored the appointment (and took the first row
    by pk) would mark and refund the wrong customer's deposit."""
    first_appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.CONFIRMED)
    first_payment = _payment(
        salon, first_appt, status=PaymentStatus.SUCCEEDED, provider_reference_id="ref_first"
    )
    second_appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START + dt.timedelta(hours=3),
        status=AppointmentStatus.CONFIRMED,
    )
    second_payment = _payment(
        salon, second_appt, status=PaymentStatus.SUCCEEDED, provider_reference_id="ref_second"
    )
    provider = _FakeRefundProvider()

    _cancel(salon, second_appt, cancelled_by=CancelledBy.STAFF, now=ELIGIBLE_NOW, provider=provider)

    second_row = _payment_row(salon, second_payment.id)
    assert second_row.refund_due is True
    assert second_row.status == PaymentStatus.REFUND_PENDING
    first_row = _payment_row(salon, first_payment.id)
    assert first_row.refund_due is False
    assert first_row.status == PaymentStatus.SUCCEEDED
    assert [call["provider_reference_id"] for call in provider.calls] == ["ref_second"]


# --- 24. A new Payment starts with refund_due False --------------------------


def test_initiate_payment_creates_a_payment_with_refund_due_false(
    salon, specialist, service, customer
):
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.PENDING_PAYMENT)

    with tenant_context(salon.id):
        payment, _, _ = initiate_payment(
            appointment_id=appt.id, salon=salon, provider=_StartPaymentProvider(), now=ELIGIBLE_NOW
        )

    assert _payment_row(salon, payment.id).refund_due is False
