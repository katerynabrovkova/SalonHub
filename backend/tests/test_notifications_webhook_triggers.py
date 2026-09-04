"""
Stage 9 step (c) commit 1 — the three webhook-fired notification triggers
(docs/DECISIONS.md § Step (c) scope narrowing; § Step (c) `dedup_key`
formats; § Step (c) — `PAYMENT_SUCCEEDED` suppressed on the
EXPIRED-appointment branch). Real-DB integration tests over
PaymentWebhookView.

Written before payments/views.py records or dispatches any Notification —
expected red: the assertions on Notification rows / dispatch fail until the
view wires the three triggers.

CELERY_TASK_ALWAYS_EAGER is True in tests (conftest._celery_eager), so a
dispatched send_notification_task runs inline. The dispatch itself is
registered with transaction.on_commit, so a test that wants the send to
actually run wraps the request in django_capture_on_commit_callbacks(
execute=True); test 8 uses execute=False to prove the deferral.
"""

import datetime as dt
from decimal import Decimal
from typing import ClassVar

import psycopg
import pytest
from django.core import mail
from django.db import IntegrityError
from rest_framework.test import APIClient

from booking.models import Appointment, AppointmentStatus
from core.tenancy import tenant_context
from notifications.models import (
    Notification,
    NotificationChannel,
    NotificationStatus,
    NotificationTrigger,
)
from payments.models import Payment, PaymentStatus, ProcessedWebhookEvent
from payments.views import PaymentWebhookView
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

START = dt.datetime(2026, 8, 22, 10, 0, tzinfo=dt.UTC)


@pytest.fixture
def client() -> APIClient:
    return APIClient()


class _FakeProvider:
    """verify_signature accepts unconditionally; refund records its calls on
    a class-level list (the view builds a fresh provider_class() per request)
    and is otherwise a no-op — the EXPIRED test drives
    should_initiate_refund=True; start_payment is not exercised."""

    refund_calls: ClassVar[list[tuple[str, str]]] = []

    def verify_signature(self, *, payload: bytes, signature: str) -> bool:
        return True

    def start_payment(self, *, amount, currency, reference):
        raise NotImplementedError("not exercised by these tests")

    def refund(self, *, provider_reference_id, reference):
        type(self).refund_calls.append((provider_reference_id, reference))
        return None


@pytest.fixture(autouse=True)
def _reset_fake_provider_refund_calls():
    _FakeProvider.refund_calls = []
    yield


def _url() -> str:
    return "/api/v1/webhooks/payments/"


def _appt(salon, specialist, service, customer, *, status, start=START):
    return make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=start,
        status=status,
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


def _post(client, event_id, event_type, provider_reference_id):
    return client.post(
        _url(),
        {
            "event_id": event_id,
            "event_type": event_type,
            "provider_reference_id": provider_reference_id,
        },
        format="json",
        HTTP_X_SIGNATURE="sig",
    )


def _notifications(salon, **filters):
    with tenant_context(salon.id):
        return list(Notification.objects.filter(**filters))


# --- 1. payment_succeeded on a PENDING_PAYMENT appointment ----------------


def test_payment_succeeded_records_payment_succeeded_and_booking_confirmed(
    client, salon, specialist, service, customer, monkeypatch, django_capture_on_commit_callbacks
):
    """The confirming path: a successful payment on a PENDING_PAYMENT
    appointment records both PAYMENT_SUCCEEDED and BOOKING_CONFIRMED, each a
    PENDING email Notification with its own dedup_key."""
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _FakeProvider)
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.PENDING_PAYMENT)
    payment = _make_payment(
        salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_1"
    )

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        response = _post(client, "evt_1", "payment_succeeded", "ref_1")

    assert response.status_code == 200

    with tenant_context(salon.id):
        rows = {n.trigger_type: n for n in Notification.objects.all()}
    assert set(rows) == {
        NotificationTrigger.PAYMENT_SUCCEEDED,
        NotificationTrigger.BOOKING_CONFIRMED,
    }

    succeeded = rows[NotificationTrigger.PAYMENT_SUCCEEDED]
    assert succeeded.status == NotificationStatus.SENT  # dispatched + delivered
    assert succeeded.channel == NotificationChannel.EMAIL
    assert succeeded.customer_id == customer.id
    assert succeeded.appointment_id == appt.id
    assert succeeded.dedup_key == f"payment_succeeded:payment:{payment.pk}"

    confirmed = rows[NotificationTrigger.BOOKING_CONFIRMED]
    assert confirmed.channel == NotificationChannel.EMAIL
    assert confirmed.customer_id == customer.id
    assert confirmed.appointment_id == appt.id
    assert confirmed.dedup_key == f"booking_confirmed:appointment:{appt.pk}"

    assert len(callbacks) == 2
    assert [m.to for m in mail.outbox] == [[customer.email], [customer.email]]


# --- 2. second delivery: transition guard, NOT the dedup constraint ------


def test_payment_succeeded_second_delivery_stopped_by_transition_guard(
    client, salon, specialist, service, customer, monkeypatch, django_capture_on_commit_callbacks
):
    """Covers the status-transition guard (`if payment_row.status ==
    PENDING`), NOT the dedup unique constraint. A second delivery with a
    fresh event_id (so the ProcessedWebhookEvent ledger does not
    short-circuit it) finds the Payment already SUCCEEDED, so the
    notification code inside the PENDING branch never runs — no INSERT, so
    the dedup_key constraint is never exercised here."""
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _FakeProvider)
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.PENDING_PAYMENT)
    _make_payment(salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_2")

    with django_capture_on_commit_callbacks(execute=True):
        _post(client, "evt_2a", "payment_succeeded", "ref_2")
    with django_capture_on_commit_callbacks(execute=True) as second_callbacks:
        response = _post(client, "evt_2b", "payment_succeeded", "ref_2")

    assert response.status_code == 200
    assert second_callbacks == []
    with tenant_context(salon.id):
        assert Notification.objects.count() == 2  # still just the first delivery's pair


# --- 3. dedup unique constraint (notification_trigger_channel_dedup_uniq) -


def test_payment_succeeded_dedup_unique_constraint_is_tolerated_per_row(
    client, salon, specialist, service, customer, monkeypatch, django_capture_on_commit_callbacks
):
    """Covers notification_trigger_channel_dedup_uniq specifically. A row
    already occupies the PAYMENT_SUCCEEDED dedup_key, so the webhook's
    INSERT for it raises UniqueViolation; the helper tolerates that as a
    no-op and dispatches nothing for it. BOOKING_CONFIRMED, created in its
    own independent savepoint, is unaffected and still recorded. 200."""
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _FakeProvider)
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.PENDING_PAYMENT)
    payment = _make_payment(
        salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_3"
    )

    with tenant_context(salon.id):
        Notification.objects.create(
            salon=salon,
            appointment=appt,
            customer_id=customer.id,
            trigger_type=NotificationTrigger.PAYMENT_SUCCEEDED,
            channel=NotificationChannel.EMAIL,
            dedup_key=f"payment_succeeded:payment:{payment.pk}",
            status=NotificationStatus.SENT,
        )

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        response = _post(client, "evt_3", "payment_succeeded", "ref_3")

    assert response.status_code == 200
    with tenant_context(salon.id):
        succeeded = Notification.objects.filter(trigger_type=NotificationTrigger.PAYMENT_SUCCEEDED)
        assert succeeded.count() == 1
        assert succeeded.get().status == NotificationStatus.SENT  # untouched
        assert (
            Notification.objects.filter(trigger_type=NotificationTrigger.BOOKING_CONFIRMED).count()
            == 1
        )
    assert len(callbacks) == 1  # only booking_confirmed dispatched
    assert [m.to for m in mail.outbox] == [[customer.email]]


# --- 4. EXPIRED-appointment suppression ---------------------------------


def test_payment_succeeded_on_expired_appointment_emits_no_notification(
    client, salon, specialist, service, customer, monkeypatch, django_capture_on_commit_callbacks
):
    """Covers the EXPIRED-suppression decision. A successful payment landing
    on an already-EXPIRED appointment creates NO PAYMENT_SUCCEEDED and NO
    BOOKING_CONFIRMED row; the existing refund behaviour is unchanged."""
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _FakeProvider)
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.EXPIRED)
    payment = _make_payment(
        salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_4"
    )

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        response = _post(client, "evt_4", "payment_succeeded", "ref_4")

    assert response.status_code == 200
    assert callbacks == []
    assert _notifications(salon) == []
    with tenant_context(salon.id):
        payment_row = Payment.objects.get(pk=payment.pk)
        appt_row = Appointment.objects.get(pk=appt.pk)
    # initiate_refund genuinely ran: it stamps REFUND_PENDING *before*
    # calling provider.refund (Stage 8.F asymmetric-risk order), and the
    # fake recorded the call — so this is "refund happened + no
    # notification", not "refund skipped".
    assert payment_row.status == PaymentStatus.REFUND_PENDING
    assert _FakeProvider.refund_calls == [("ref_4", str(appt.id))]
    assert appt_row.status == AppointmentStatus.EXPIRED  # not resurrected
    assert mail.outbox == []


# --- 5. payment_failed -------------------------------------------------


def test_payment_failed_records_one_notification_with_ref_in_dedup_key(
    client, salon, specialist, service, customer, monkeypatch, django_capture_on_commit_callbacks
):
    """The PENDING -> FAILED transition records one PAYMENT_FAILED email
    Notification whose dedup_key carries the provider_reference_id."""
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _FakeProvider)
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.PENDING_PAYMENT)
    payment = _make_payment(
        salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_5"
    )

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        response = _post(client, "evt_5", "payment_failed", "ref_5")

    assert response.status_code == 200
    rows = _notifications(salon)
    assert len(rows) == 1
    row = rows[0]
    assert row.trigger_type == NotificationTrigger.PAYMENT_FAILED
    assert row.channel == NotificationChannel.EMAIL
    assert row.customer_id == customer.id
    assert row.appointment_id == appt.id
    assert row.dedup_key == f"payment_failed:payment:{payment.pk}:ref_5"

    assert len(callbacks) == 1
    assert _notifications(salon)[0].status == NotificationStatus.SENT
    assert [m.to for m in mail.outbox] == [[customer.email]]


def test_payment_failed_twice_with_different_refs_records_two_rows(
    client, salon, specialist, service, customer, monkeypatch, django_capture_on_commit_callbacks
):
    """The attempt discriminator: the same Payment row failing twice with
    two provider_reference_ids (as initiate_payment's FAILED -> PENDING
    retry-in-place produces) yields two PAYMENT_FAILED rows, not one."""
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _FakeProvider)
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.PENDING_PAYMENT)
    payment = _make_payment(
        salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_6a"
    )

    with django_capture_on_commit_callbacks(execute=True):
        _post(client, "evt_6a", "payment_failed", "ref_6a")

    # The guest retries: FAILED -> PENDING in place, fresh provider_reference_id
    # (payments/services.py:88-92).
    with tenant_context(salon.id):
        row = Payment.objects.get(pk=payment.pk)
        row.status = PaymentStatus.PENDING
        row.provider_reference_id = "ref_6b"
        row.save(update_fields=["status", "provider_reference_id"])

    with django_capture_on_commit_callbacks(execute=True):
        _post(client, "evt_6b", "payment_failed", "ref_6b")

    with tenant_context(salon.id):
        keys = sorted(
            Notification.objects.filter(
                trigger_type=NotificationTrigger.PAYMENT_FAILED
            ).values_list("dedup_key", flat=True)
        )
    assert keys == [
        f"payment_failed:payment:{payment.pk}:ref_6a",
        f"payment_failed:payment:{payment.pk}:ref_6b",
    ]


def test_payment_failed_replay_same_ref_records_one_row_and_no_second_dispatch(
    client, salon, specialist, service, customer, monkeypatch, django_capture_on_commit_callbacks
):
    """Covers the status-transition guard, NOT the dedup constraint. A
    replay with the same provider_reference_id and a fresh event_id finds
    the Payment already FAILED, so the notification code inside the PENDING
    branch never runs — no INSERT, so notification_trigger_channel_dedup_uniq
    is never reached. The provider_reference_id in the PAYMENT_FAILED
    dedup_key is not constraint protection (no conflict is reachable): it is
    what makes a legitimate *different*-ref attempt a new row (test 6)."""
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _FakeProvider)
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.PENDING_PAYMENT)
    _make_payment(salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_7")

    with django_capture_on_commit_callbacks(execute=True):
        _post(client, "evt_7a", "payment_failed", "ref_7")
    with django_capture_on_commit_callbacks(execute=True) as replay_callbacks:
        _post(client, "evt_7b", "payment_failed", "ref_7")

    assert replay_callbacks == []
    with tenant_context(salon.id):
        assert (
            Notification.objects.filter(trigger_type=NotificationTrigger.PAYMENT_FAILED).count()
            == 1
        )


# --- 8. on_commit deferral --------------------------------------------


def test_send_is_deferred_until_after_commit(
    client, salon, specialist, service, customer, monkeypatch, django_capture_on_commit_callbacks
):
    """The send is registered with transaction.on_commit, not run inline:
    inside the capture block (execute=False) the rows exist but are still
    PENDING and nothing has been mailed; running the captured callbacks
    then delivers them."""
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _FakeProvider)
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.PENDING_PAYMENT)
    _make_payment(salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_8")

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        response = _post(client, "evt_8", "payment_succeeded", "ref_8")
        with tenant_context(salon.id):
            assert set(Notification.objects.values_list("status", flat=True)) == {
                NotificationStatus.PENDING
            }
        assert mail.outbox == []

    assert response.status_code == 200
    assert len(callbacks) == 2

    for callback in callbacks:
        callback()

    with tenant_context(salon.id):
        assert set(Notification.objects.values_list("status", flat=True)) == {
            NotificationStatus.SENT
        }
    assert len(mail.outbox) == 2


# --- 9. the IntegrityError catch is narrowed to the dedup constraint ----


def test_notification_insert_non_dedup_integrity_error_is_not_swallowed(
    client, salon, specialist, service, customer, monkeypatch, django_capture_on_commit_callbacks
):
    """The helper tolerates ONLY a UniqueViolation on
    notification_trigger_channel_dedup_uniq. Any other IntegrityError — here
    a composite tenant FK violation (ForeignKeyViolation), the case § Step
    (c) decisions calls out — must propagate, never be silently treated as
    an already-recorded event. A broad `except IntegrityError` catch would
    swallow it: the webhook would 200, mark the event processed, and lose
    the notification with no trace."""
    monkeypatch.setattr(PaymentWebhookView, "provider_class", _FakeProvider)
    appt = _appt(salon, specialist, service, customer, status=AppointmentStatus.PENDING_PAYMENT)
    _make_payment(salon, appt, status=PaymentStatus.PENDING, provider_reference_id="ref_9")

    def _raise_fk_violation(*args, **kwargs):
        exc = IntegrityError("insert or update violates foreign key constraint")
        exc.__cause__ = psycopg.errors.ForeignKeyViolation("fk")
        raise exc

    monkeypatch.setattr(Notification.objects, "create", _raise_fk_violation)

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        response = _post(client, "evt_9", "payment_succeeded", "ref_9")

    # Re-raised out of the helper, not swallowed: core.exceptions turns an
    # unrecognised IntegrityError into a loud 500 (it only translates a
    # UniqueViolation to a 400), the webhook transaction rolls back, and
    # nothing is recorded, marked processed, or dispatched.
    assert response.status_code == 500
    assert callbacks == []
    assert _notifications(salon) == []
    assert ProcessedWebhookEvent.objects.filter(provider_event_id="evt_9").count() == 0
