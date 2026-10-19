"""
Stage 15 — the refund retry sweep (docs/DECISIONS.md § "Refund retry
sweep"). Tests only — written before the service, the task, the beat entry,
or `get_payment_provider` exist. Expected to fail on collection (ImportError:
cannot import name 'get_payment_provider' from 'payments.providers', the
first missing name in import order, then 'retry_owed_refunds' and
'retry_owed_refund_payments') until they are added — same two-step red shape
as test_payments_flag_stuck_refunds.py, which this file mirrors.

Names under test (the decision entry leaves them open; these follow the
`flag_stuck_refunds` / `flag_stuck_refund_payments` precedent):

    payments.services.retry_owed_refunds(*, salon, provider, now) -> int
    payments.tasks.retry_owed_refund_payments() -> None   # @shared_task
    payments.providers.get_payment_provider() -> PaymentProvider

The task gets its provider only through `get_payment_provider()`, imported
by name into `payments.tasks`, so task-level tests patch
`payments.tasks.get_payment_provider` — the same "patch the name in the
task module's own namespace" convention test_payments_flag_stuck_refunds.py
test 9 pins for `flag_stuck_refunds`. Service-level tests pass their own
provider double directly. No test relies on the default provider except the
one that checks what the default is.

The per-row `PaymentProviderError` log line is expected to label its ids as
`payment_id=<id>` and `salon_id=<id>`, the same `salon_id=%s` labelling the
existing sweep tasks use.

Real-DB integration tests throughout. Monkeypatching only for the provider,
`payments.services.initiate_refund` (wrapped to record the active tenant
context, or to raise an unexpected exception), and the concurrency test's
`select_for_update` hook. Service-level `now` values are tz-aware UTC
literals; the task reads its own `timezone.now()`, so task-level tests assert
nothing about dates. Every appointment/customer/specialist/service/payment
created here passes `salon=` explicitly (Stage 6 shell-seeding trap, per
CLAUDE.md).
"""

import datetime as dt
import logging
import re
import threading
from dataclasses import dataclass
from decimal import Decimal

import pytest
from django.db import connection
from django.db.models.query import QuerySet

import payments.services as payments_services
import payments.tasks as payments_tasks
from accounts.models import Customer
from booking.models import AppointmentStatus
from catalog.models import Service, ServiceCategory
from config.celery import app as celery_app
from core.tenancy import get_current_salon_id, tenant_context
from payments.models import Payment, PaymentStatus
from payments.providers import get_payment_provider
from payments.providers.mock import MockPaymentProvider
from payments.services import initiate_refund, retry_owed_refunds
from payments.tasks import retry_owed_refund_payments
from specialists.models import Specialist
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

START = dt.datetime(2026, 8, 20, 10, 0, tzinfo=dt.UTC)
NOW = dt.datetime(2026, 8, 19, 12, 0, tzinfo=dt.UTC)


# --- Provider doubles ---------------------------------------------------------


@dataclass(frozen=True)
class _FakeRefundIntent:
    provider_reference_id: str


class _RecordingProvider:
    """Records the provider_reference_id of every refund() call. One
    instance is shared by every call in a test (the task-level patch returns
    this same instance), so calls from all salons land in one list."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def start_payment(self, *, amount, currency, reference):
        raise NotImplementedError("not exercised by the refund retry sweep")

    def refund(self, *, provider_reference_id, reference, amount, currency):
        self.calls.append(provider_reference_id)
        return _FakeRefundIntent(provider_reference_id=f"fake_refund_{len(self.calls)}")


class _FailingFirstRefundProvider(_RecordingProvider):
    """The first refund() call raises (initiate_refund wraps it into
    PaymentProviderError); later calls succeed."""

    def refund(self, *, provider_reference_id, reference, amount, currency):
        self.calls.append(provider_reference_id)
        if len(self.calls) == 1:
            raise RuntimeError("simulated provider outage")
        return _FakeRefundIntent(provider_reference_id=f"fake_refund_{len(self.calls)}")


# --- Helpers ------------------------------------------------------------------


def _chain_for_salon(salon):
    """A specialist/service/customer for `salon` (used for the second salon,
    which has no conftest fixtures) — same construction as
    test_payments_flag_stuck_refunds.py's _make_stuck_refund_payment_for_salon."""
    with tenant_context(salon.id):
        category = ServiceCategory.objects.create(salon=salon, name={"en": "Nails"})
        service = Service.objects.create(
            salon=salon,
            category=category,
            name={"en": "Manicure"},
            duration_minutes=60,
            price="500.00",
            buffer_minutes=15,
        )
        specialist = Specialist.objects.create(salon=salon, name={"en": "Specialist"})
        customer = Customer.objects.create(
            salon=salon,
            name="Customer",
            email=f"customer-{salon.id}@example.com",
            phone="+10000000002",
        )
    return specialist, service, customer


def _make_payment(
    salon,
    specialist,
    service,
    customer,
    *,
    provider_reference_id,
    status=PaymentStatus.SUCCEEDED,
    refund_due=True,
    slot=0,
):
    """A CANCELLED appointment (the usual owner of an owed refund) with its
    Payment. `slot` spaces the start times 3 hours apart."""
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START + dt.timedelta(hours=3 * slot),
        status=AppointmentStatus.CANCELLED,
    )
    with tenant_context(salon.id):
        return Payment.objects.create(
            salon=salon,
            appointment=appt,
            amount=Decimal("100.00"),
            currency=salon.currency,
            status=status,
            provider_reference_id=provider_reference_id,
            refund_due=refund_due,
        )


def _row(payment) -> Payment:
    with tenant_context(payment.salon_id):
        return Payment.objects.get(pk=payment.pk)


def _patch_provider(monkeypatch, provider) -> None:
    monkeypatch.setattr(payments_tasks, "get_payment_provider", lambda: provider)


# --- 1. Service: refunds a stuck payment --------------------------------------


def test_retry_owed_refunds_refunds_a_stuck_payment(salon, specialist, service, customer):
    payment = _make_payment(salon, specialist, service, customer, provider_reference_id="stuck")
    provider = _RecordingProvider()

    with tenant_context(salon.id):
        count = retry_owed_refunds(salon=salon, provider=provider, now=NOW)

    assert count == 1
    assert provider.calls == ["stuck"]
    row = _row(payment)
    assert row.status == PaymentStatus.REFUND_PENDING
    assert row.refund_initiated_at == NOW
    assert row.refund_due is True


# --- 2. Service: skips everything that is not SUCCEEDED + refund_due ----------


@pytest.mark.parametrize(
    ("status", "refund_due"),
    [
        (PaymentStatus.SUCCEEDED, False),
        (PaymentStatus.REFUND_PENDING, True),
        (PaymentStatus.REFUNDED, True),
    ],
    ids=["succeeded_without_refund_due", "refund_pending", "refunded"],
)
def test_retry_owed_refunds_skips_rows_that_are_not_owed(
    salon, specialist, service, customer, status, refund_due
):
    """Positive control: an owed payment in the same salon is refunded in the
    same run, so a sweep that does nothing cannot pass this test."""
    payment = _make_payment(
        salon,
        specialist,
        service,
        customer,
        provider_reference_id="not_owed",
        status=status,
        refund_due=refund_due,
        slot=0,
    )
    owed = _make_payment(salon, specialist, service, customer, provider_reference_id="owed", slot=1)
    provider = _RecordingProvider()

    with tenant_context(salon.id):
        count = retry_owed_refunds(salon=salon, provider=provider, now=NOW)

    assert count == 1
    assert provider.calls == ["owed"]
    assert _row(owed).status == PaymentStatus.REFUND_PENDING
    row = _row(payment)
    assert row.status == status
    assert row.refund_initiated_at is None


def test_retry_owed_refunds_does_not_touch_another_salons_payment(
    salon, other_salon, specialist, service, customer
):
    """The stuck payment belongs to `salon`; the call passes
    salon=other_salon. Same shape as test_payments_flag_stuck_refunds.py
    test 7. Positive control: other_salon's own owed payment is refunded in
    the same call, so a sweep that does nothing cannot pass this test."""
    payment = _make_payment(salon, specialist, service, customer, provider_reference_id="stuck")
    o_specialist, o_service, o_customer = _chain_for_salon(other_salon)
    own = _make_payment(
        other_salon, o_specialist, o_service, o_customer, provider_reference_id="own"
    )
    provider = _RecordingProvider()

    with tenant_context(other_salon.id):
        count = retry_owed_refunds(salon=other_salon, provider=provider, now=NOW)

    assert count == 1
    assert provider.calls == ["own"]
    assert _row(own).status == PaymentStatus.REFUND_PENDING
    assert _row(payment).status == PaymentStatus.SUCCEEDED


def test_retry_owed_refunds_applies_its_own_salon_argument(
    salon, other_salon, specialist, service, customer
):
    """The salon filter in the candidate query is a second guard behind the
    tenant-scoped manager, and 2b cannot see it: there the bound context and
    the argument are the same salon. Here the context is `salon` (so the
    manager alone would find the payment) and the argument is other_salon,
    the same shape as test_payments_flag_stuck_refunds.py test 7. Positive
    control: the same call with salon=salon refunds the row, so a sweep that
    does nothing cannot pass this test."""
    payment = _make_payment(salon, specialist, service, customer, provider_reference_id="stuck")
    provider = _RecordingProvider()

    with tenant_context(salon.id):
        count = retry_owed_refunds(salon=other_salon, provider=provider, now=NOW)

    assert count == 0
    assert provider.calls == []
    assert _row(payment).status == PaymentStatus.SUCCEEDED

    with tenant_context(salon.id):
        control_count = retry_owed_refunds(salon=salon, provider=provider, now=NOW)

    assert control_count == 1
    assert provider.calls == ["stuck"]
    assert _row(payment).status == PaymentStatus.REFUND_PENDING


# --- 3. Service: a provider failure does not stop the next row ----------------


def test_retry_owed_refunds_provider_error_on_one_row_still_refunds_the_next(
    caplog, salon, specialist, service, customer
):
    """The first refund() call fails. initiate_refund has already committed
    that row as REFUND_PENDING (flag_stuck_refunds' territory); the sweep
    logs it with both ids and still refunds the other row of the same
    salon. Which row fails first depends on the sweep's iteration order, so
    the failing payment is identified from the provider's first call."""
    caplog.set_level(logging.WARNING)
    first = _make_payment(
        salon, specialist, service, customer, provider_reference_id="ref_one", slot=0
    )
    second = _make_payment(
        salon, specialist, service, customer, provider_reference_id="ref_two", slot=1
    )
    by_ref = {"ref_one": first, "ref_two": second}
    provider = _FailingFirstRefundProvider()

    with tenant_context(salon.id):
        retry_owed_refunds(salon=salon, provider=provider, now=NOW)  # must not raise

    assert sorted(provider.calls) == ["ref_one", "ref_two"]
    assert _row(first).status == PaymentStatus.REFUND_PENDING
    assert _row(second).status == PaymentStatus.REFUND_PENDING

    failed = by_ref[provider.calls[0]]
    assert any(
        re.search(rf"\bpayment_id={failed.pk}\b", record.getMessage())
        and re.search(rf"\bsalon_id={salon.id}\b", record.getMessage())
        for record in caplog.records
    ), [record.getMessage() for record in caplog.records]


# --- 4. Service: a concurrent normal initiate_refund refunds only once --------


@pytest.mark.django_db(transaction=True)
def test_retry_owed_refunds_and_a_concurrent_initiate_refund_call_the_provider_once(
    monkeypatch, salon, specialist, service, customer
):
    """
    The sweep has already selected the row (unlocked candidate query) when
    the cancel or webhook after-commit call refunds it. That competing
    initiate_refund runs in its own thread, so on its own database
    connection: a real concurrent session that commits REFUND_PENDING, not a
    savepoint inside the sweep's transaction (the problem recorded in
    "Known issue: the `flag_stuck_refunds` race test uses a savepoint").
    transaction=True makes the test data visible to that connection.

    It is triggered from the sweep's first select_for_update on Payment —
    the gap between the unlocked candidate query and the lock — and joined
    before the sweep's own locked read runs. The sweep must then find
    REFUND_PENDING and not call the provider again.
    """
    payment = _make_payment(salon, specialist, service, customer, provider_reference_id="raced")
    provider = _RecordingProvider()

    fired = {"done": False}
    competing: dict = {}
    original_select_for_update = QuerySet.select_for_update

    def _competing_refund() -> None:
        try:
            with tenant_context(salon.id):
                initiate_refund(payment_id=payment.pk, salon=salon, provider=provider, now=NOW)
            competing["ok"] = True
        except Exception as exc:  # surfaced by the assertion below
            competing["error"] = exc
        finally:
            connection.close()

    def _select_for_update_with_race(self, *args, **kwargs):
        if not fired["done"] and self.model is Payment:
            fired["done"] = True
            thread = threading.Thread(target=_competing_refund)
            thread.start()
            thread.join(timeout=30)
            assert not thread.is_alive(), "competing initiate_refund did not finish"
        return original_select_for_update(self, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "select_for_update", _select_for_update_with_race)

    with tenant_context(salon.id):
        retry_owed_refunds(salon=salon, provider=provider, now=NOW)

    assert fired["done"] is True
    assert competing == {"ok": True}
    assert provider.calls == ["raced"]
    assert _row(payment).status == PaymentStatus.REFUND_PENDING


# --- 5. Task: each salon in its own tenant context ----------------------------


def test_retry_owed_refund_payments_runs_each_refund_in_its_payments_salon_context(
    monkeypatch, salon, other_salon, specialist, service, customer
):
    """A stuck payment in each of two salons. Every initiate_refund call
    records the tenant context active at that moment; it must be the
    payment's own salon, and the salon passed in."""
    payment_a = _make_payment(salon, specialist, service, customer, provider_reference_id="ref_a")
    b_specialist, b_service, b_customer = _chain_for_salon(other_salon)
    payment_b = _make_payment(
        other_salon, b_specialist, b_service, b_customer, provider_reference_id="ref_b"
    )
    provider = _RecordingProvider()
    _patch_provider(monkeypatch, provider)

    seen: list[dict] = []
    real_initiate_refund = payments_services.initiate_refund

    def _recording_initiate_refund(*, payment_id, salon, provider, now):
        seen.append(
            {
                "payment_id": payment_id,
                "active_salon_id": get_current_salon_id(),
                "passed_salon_id": salon.id,
                "payment_salon_id": Payment.unscoped_objects.get(pk=payment_id).salon_id,
            }
        )
        return real_initiate_refund(payment_id=payment_id, salon=salon, provider=provider, now=now)

    monkeypatch.setattr(payments_services, "initiate_refund", _recording_initiate_refund)

    retry_owed_refund_payments()

    assert sorted(call["payment_id"] for call in seen) == sorted([payment_a.pk, payment_b.pk])
    for call in seen:
        assert call["active_salon_id"] == call["payment_salon_id"], call
        assert call["passed_salon_id"] == call["payment_salon_id"], call
    assert sorted(provider.calls) == ["ref_a", "ref_b"]
    assert _row(payment_a).status == PaymentStatus.REFUND_PENDING
    assert _row(payment_b).status == PaymentStatus.REFUND_PENDING


# --- 6-7. Task: an unexpected exception ends one salon's batch only -----------


def _raise_on_first_call(monkeypatch) -> list[int]:
    """Wraps initiate_refund so the very first call (whichever salon and
    row the task reaches first) raises an unexpected exception — not a
    PaymentProviderError. Returns the list of attempted payment ids."""
    attempted: list[int] = []
    real_initiate_refund = payments_services.initiate_refund

    def _wrapped(*, payment_id, salon, provider, now):
        attempted.append(payment_id)
        if len(attempted) == 1:
            raise RuntimeError("unexpected bug on one row")
        return real_initiate_refund(payment_id=payment_id, salon=salon, provider=provider, now=now)

    monkeypatch.setattr(payments_services, "initiate_refund", _wrapped)
    return attempted


def test_retry_owed_refund_payments_one_failing_row_does_not_block_the_other_salons(
    monkeypatch, salon, other_salon, specialist, service, customer
):
    payment_a = _make_payment(salon, specialist, service, customer, provider_reference_id="ref_a")
    b_specialist, b_service, b_customer = _chain_for_salon(other_salon)
    payment_b = _make_payment(
        other_salon, b_specialist, b_service, b_customer, provider_reference_id="ref_b"
    )
    by_id = {payment_a.pk: payment_a, payment_b.pk: payment_b}
    provider = _RecordingProvider()
    _patch_provider(monkeypatch, provider)
    attempted = _raise_on_first_call(monkeypatch)

    retry_owed_refund_payments()  # must not raise

    assert attempted, "the task never called initiate_refund"
    failed = by_id[attempted[0]]
    other = payment_b if failed is payment_a else payment_a
    assert _row(failed).status == PaymentStatus.SUCCEEDED
    assert _row(other).status == PaymentStatus.REFUND_PENDING
    assert provider.calls == [other.provider_reference_id]


def test_retry_owed_refund_payments_unexpected_error_ends_that_salons_batch_only(
    monkeypatch, salon, other_salon, specialist, service, customer
):
    """Two stuck payments per salon. The first row the task reaches raises an
    unexpected exception: it is not caught per row, so the rest of that
    salon's batch is skipped for this run, while the other salon is still
    fully processed."""
    a1 = _make_payment(salon, specialist, service, customer, provider_reference_id="a1", slot=0)
    a2 = _make_payment(salon, specialist, service, customer, provider_reference_id="a2", slot=1)
    b_specialist, b_service, b_customer = _chain_for_salon(other_salon)
    b1 = _make_payment(
        other_salon, b_specialist, b_service, b_customer, provider_reference_id="b1", slot=0
    )
    b2 = _make_payment(
        other_salon, b_specialist, b_service, b_customer, provider_reference_id="b2", slot=1
    )
    payments_by_salon = {salon.id: [a1, a2], other_salon.id: [b1, b2]}
    provider = _RecordingProvider()
    _patch_provider(monkeypatch, provider)
    attempted = _raise_on_first_call(monkeypatch)

    retry_owed_refund_payments()  # must not raise

    assert attempted, "the task never called initiate_refund"
    failing_salon_id = Payment.unscoped_objects.get(pk=attempted[0]).salon_id
    healthy_salon_id = other_salon.id if failing_salon_id == salon.id else salon.id

    failing_rows = payments_by_salon[failing_salon_id]
    attempted_in_failing = [pid for pid in attempted if pid in {p.pk for p in failing_rows}]
    assert attempted_in_failing == [attempted[0]]
    for payment in failing_rows:
        row = _row(payment)
        assert row.status == PaymentStatus.SUCCEEDED
        assert row.refund_due is True

    healthy_rows = payments_by_salon[healthy_salon_id]
    for payment in healthy_rows:
        assert _row(payment).status == PaymentStatus.REFUND_PENDING
    assert sorted(provider.calls) == sorted(p.provider_reference_id for p in healthy_rows)


# --- 8. get_payment_provider and the beat schedule ----------------------------


def test_get_payment_provider_returns_a_mock_provider_instance():
    assert isinstance(get_payment_provider(), MockPaymentProvider)


def test_retry_owed_refund_payments_is_in_the_beat_schedule_every_1800_seconds():
    entries = [
        entry
        for entry in celery_app.conf.beat_schedule.values()
        if entry["task"] == "payments.tasks.retry_owed_refund_payments"
    ]
    assert len(entries) == 1, celery_app.conf.beat_schedule
    assert entries[0]["schedule"] == 1800.0
