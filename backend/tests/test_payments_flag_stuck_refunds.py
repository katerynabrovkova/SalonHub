"""
Stage 8.G — `flag_stuck_refunds` service function and the
`flag_stuck_refund_payments` periodic Celery task (docs/ARCHITECTURE.md § 8;
docs/DECISIONS.md § Stage 8 decisions, § Stage 8.G decisions). Tests only —
written before either the service or `payments/tasks.py` exist. Expected to
fail on collection (ImportError: cannot import name 'flag_stuck_refunds'
from 'payments.services', or ModuleNotFoundError: No module named
'payments.tasks') until they're added — same two-step red shape
`test_booking_expire_appointments.py` established for the structurally
identical 7.F sweep, which this file mirrors throughout.

Signatures under test:

    flag_stuck_refunds(*, salon, now) -> int
    flag_stuck_refund_payments() -> None   # @shared_task

Real-DB integration tests throughout (the service does select_for_update()
plus a write) — no monkeypatching except test 9's one-salon failure double
and test 10's select_for_update race double. All `now`/`refund_initiated_at`
values in the SERVICE-level tests (1-7, 10) are tz-aware UTC literals, never
`timezone.now()` — same discipline as § Stage 7.F decisions. The TASK-level
tests (8-9) are the one exception: the task reads its own `now =
timezone.now()` internally (mirroring § Stage 7.F decisions), so there is no
literal to inject from the test side — `refund_initiated_at` there is built
from real `timezone.now()` instead.

Every Payment's backing Appointment is CONFIRMED: flag_stuck_refunds reads
only Payment.status / Payment.refund_initiated_at / Payment.flagged_for_review,
never Appointment.status, so the appointment's own status is irrelevant here
and CONFIRMED (the make_appointment default) is used only because it reads
sensibly — same reasoning as § Stage 8.F's own test file.

Test 10 alone runs under @pytest.mark.django_db(transaction=True): it needs
its competing write to be a REAL commit, not a savepoint nested inside one
giant uncommitted test transaction, for the locked select_for_update read to
see it — approved construction (see conversation): patch
django.db.models.query.QuerySet.select_for_update (the exact ORM call
sitting between the already-evaluated candidate query and the locked read),
perform the competing write from inside that patch in its own atomic() block,
then delegate to the real select_for_update. The recheck's own `if`
statements are never patched.

Every appointment/customer/specialist/service/payment created here passes
salon= explicitly (Stage 6 shell-seeding trap, per CLAUDE.md).
"""

import datetime as dt
import logging
from decimal import Decimal

import payments.tasks as payments_tasks
import pytest
from django.db import transaction
from django.db.models.query import QuerySet
from django.utils import timezone
from payments.tasks import flag_stuck_refund_payments

from accounts.models import Customer
from catalog.models import Service, ServiceCategory
from core.tenancy import tenant_context
from payments.constants import STUCK_REFUND_THRESHOLD
from payments.models import Payment, PaymentStatus
from payments.services import flag_stuck_refunds
from specialists.models import Specialist
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

START = dt.datetime(2026, 8, 20, 9, 0, tzinfo=dt.UTC)
NOW = dt.datetime(2026, 8, 24, 12, 0, tzinfo=dt.UTC)
# Comfortably past / under the 72h threshold, relative to NOW — never
# exactly on the boundary, so these two never depend on __lte vs __lt.
STUCK_SINCE = NOW - STUCK_REFUND_THRESHOLD - dt.timedelta(hours=1)
FRESH_SINCE = NOW - STUCK_REFUND_THRESHOLD + dt.timedelta(hours=1)


def _make_confirmed_appointment(salon, specialist, service, customer, *, start=START):
    return make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=start,
    )


def _make_refund_pending_payment(
    salon,
    appt,
    *,
    refund_initiated_at,
    status=PaymentStatus.REFUND_PENDING,
    flagged_for_review=False,
):
    with tenant_context(salon.id):
        return Payment.objects.create(
            salon=salon,
            appointment=appt,
            amount=Decimal("100.00"),
            currency=salon.currency,
            status=status,
            provider_reference_id="stuck_ref",
            refund_initiated_at=refund_initiated_at,
            flagged_for_review=flagged_for_review,
        )


def _make_stuck_refund_payment_for_salon(
    salon,
    *,
    refund_initiated_at,
    status=PaymentStatus.REFUND_PENDING,
    flagged_for_review=False,
    start=START,
):
    """Creates a fresh specialist/service/customer/appointment/payment chain
    for `salon`. Used only by the task-level tests (8-9), which need a
    second, fully independent salon rather than the shared `salon` fixture's
    own service/specialist/customer — same reasoning as
    test_booking_expire_appointments.py's own _make_overdue_appointment."""
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
    appt = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=start,
    )
    with tenant_context(salon.id):
        return Payment.objects.create(
            salon=salon,
            appointment=appt,
            amount=Decimal("100.00"),
            currency=salon.currency,
            status=status,
            provider_reference_id=f"stuck_ref_{salon.id}",
            refund_initiated_at=refund_initiated_at,
            flagged_for_review=flagged_for_review,
        )


# --- 1. Happy path: a stuck REFUND_PENDING past threshold gets flagged ------


def test_flag_stuck_refunds_flags_a_refund_pending_past_threshold(
    salon, specialist, service, customer
):
    appt = _make_confirmed_appointment(salon, specialist, service, customer)
    payment = _make_refund_pending_payment(salon, appt, refund_initiated_at=STUCK_SINCE)

    with tenant_context(salon.id):
        count = flag_stuck_refunds(salon=salon, now=NOW)

    assert count == 1
    with tenant_context(salon.id):
        row = Payment.objects.get(pk=payment.pk)
    assert row.flagged_for_review is True


# --- 2. Just under threshold is not touched ----------------------------------


def test_flag_stuck_refunds_does_not_flag_one_just_under_threshold(
    salon, specialist, service, customer
):
    appt = _make_confirmed_appointment(salon, specialist, service, customer)
    payment = _make_refund_pending_payment(salon, appt, refund_initiated_at=FRESH_SINCE)

    with tenant_context(salon.id):
        count = flag_stuck_refunds(salon=salon, now=NOW)

    assert count == 0
    with tenant_context(salon.id):
        row = Payment.objects.get(pk=payment.pk)
    assert row.flagged_for_review is False


# --- 3. NULL refund_initiated_at is never swept ------------------------------


def test_flag_stuck_refunds_never_flags_a_null_refund_initiated_at(
    salon, specialist, service, customer
):
    """refund_initiated_at is NULL for a payment that never entered a
    refund, or for any REFUND_PENDING row predating the 8.G field migration
    — __lte on NULL is never true in SQL, so these are never swept (correct:
    there is no recorded stuck-since time to measure)."""
    appt = _make_confirmed_appointment(salon, specialist, service, customer)
    payment = _make_refund_pending_payment(salon, appt, refund_initiated_at=None)

    with tenant_context(salon.id):
        count = flag_stuck_refunds(salon=salon, now=NOW)

    assert count == 0
    with tenant_context(salon.id):
        row = Payment.objects.get(pk=payment.pk)
    assert row.flagged_for_review is False


# --- 4. Already-flagged row is excluded by the query filter itself ----------


def test_flag_stuck_refunds_does_not_reflag_an_already_flagged_row(
    salon, specialist, service, customer
):
    """flagged_for_review=True at creation time — excluded by the candidate
    query's own flagged_for_review=False filter, never even reaching the
    per-row lock. Contrast with test 10, which pins the separate, later
    under-lock recheck guard."""
    appt = _make_confirmed_appointment(salon, specialist, service, customer)
    payment = _make_refund_pending_payment(
        salon, appt, refund_initiated_at=STUCK_SINCE, flagged_for_review=True
    )

    with tenant_context(salon.id):
        count = flag_stuck_refunds(salon=salon, now=NOW)

    assert count == 0
    with tenant_context(salon.id):
        row = Payment.objects.get(pk=payment.pk)
    assert row.flagged_for_review is True


# --- 5. A REFUNDED row is ignored regardless of staleness -------------------


def test_flag_stuck_refunds_ignores_a_refunded_row(salon, specialist, service, customer):
    appt = _make_confirmed_appointment(salon, specialist, service, customer)
    payment = _make_refund_pending_payment(
        salon, appt, status=PaymentStatus.REFUNDED, refund_initiated_at=STUCK_SINCE
    )

    with tenant_context(salon.id):
        count = flag_stuck_refunds(salon=salon, now=NOW)

    assert count == 0
    with tenant_context(salon.id):
        row = Payment.objects.get(pk=payment.pk)
    assert row.flagged_for_review is False


# --- 6. Mixed batch: only the genuinely stuck rows are flagged --------------


def test_flag_stuck_refunds_flags_only_the_stuck_ones_in_a_mixed_batch(
    salon, specialist, service, customer
):
    # Distinct start times so the exclusion constraint (same specialist,
    # overlapping active-status intervals) doesn't reject these as
    # double-bookings — same spacing reasoning as test_booking_expire_
    # appointments.py's own mixed-batch test.
    stuck_appt = _make_confirmed_appointment(salon, specialist, service, customer, start=START)
    fresh_appt = _make_confirmed_appointment(
        salon, specialist, service, customer, start=START + dt.timedelta(hours=3)
    )
    already_flagged_appt = _make_confirmed_appointment(
        salon, specialist, service, customer, start=START + dt.timedelta(hours=6)
    )
    stuck = _make_refund_pending_payment(salon, stuck_appt, refund_initiated_at=STUCK_SINCE)
    fresh = _make_refund_pending_payment(salon, fresh_appt, refund_initiated_at=FRESH_SINCE)
    already_flagged = _make_refund_pending_payment(
        salon,
        already_flagged_appt,
        refund_initiated_at=STUCK_SINCE,
        flagged_for_review=True,
    )

    with tenant_context(salon.id):
        count = flag_stuck_refunds(salon=salon, now=NOW)

    assert count == 1
    with tenant_context(salon.id):
        stuck_row = Payment.objects.get(pk=stuck.pk)
        fresh_row = Payment.objects.get(pk=fresh.pk)
        already_flagged_row = Payment.objects.get(pk=already_flagged.pk)
    assert stuck_row.flagged_for_review is True
    assert fresh_row.flagged_for_review is False
    assert already_flagged_row.flagged_for_review is True


# --- 7. Tenant isolation ------------------------------------------------------


def test_flag_stuck_refunds_does_not_touch_another_salons_payment(
    salon, other_salon, specialist, service, customer
):
    """specialist/service/customer/appointment/payment all belong to
    `salon`; the call passes salon=other_salon. Proves `salon` is actually
    applied in the candidate query, not silently ignored — same reasoning as
    test_booking_expire_appointments.py's own tenant-isolation test."""
    appt = _make_confirmed_appointment(salon, specialist, service, customer)
    payment = _make_refund_pending_payment(salon, appt, refund_initiated_at=STUCK_SINCE)

    with tenant_context(salon.id):
        count = flag_stuck_refunds(salon=other_salon, now=NOW)

    assert count == 0
    with tenant_context(salon.id):
        row = Payment.objects.get(pk=payment.pk)
    assert row.flagged_for_review is False


# --- 8. Task: cross-tenant loop visits every salon ---------------------------


def test_flag_stuck_refund_payments_task_flags_across_every_salon(
    salon, specialist, service, customer, other_salon
):
    """Called directly and synchronously (not via .delay()/a worker). A
    stuck REFUND_PENDING row in each of two salons, one task call. If
    tenant_context didn't bind per salon, or bled from one iteration into
    the next, at least one of these two rows would end up missed or
    mis-attributed."""
    stuck_since = timezone.now() - STUCK_REFUND_THRESHOLD - dt.timedelta(hours=1)
    appt_a = _make_confirmed_appointment(salon, specialist, service, customer)
    payment_a = _make_refund_pending_payment(salon, appt_a, refund_initiated_at=stuck_since)
    payment_b = _make_stuck_refund_payment_for_salon(other_salon, refund_initiated_at=stuck_since)

    flag_stuck_refund_payments()

    with tenant_context(salon.id):
        row_a = Payment.objects.get(pk=payment_a.id)
    with tenant_context(other_salon.id):
        row_b = Payment.objects.get(pk=payment_b.id)
    assert row_a.flagged_for_review is True
    assert row_b.flagged_for_review is True


# --- 9. Task: one failing salon does not abort the run -----------------------


def test_flag_stuck_refund_payments_task_continues_past_a_failing_salon(
    monkeypatch, caplog, salon, specialist, service, customer, other_salon
):
    """
    One salon's processing raises; the task must log it and continue to the
    next salon rather than aborting the whole run — same cross-tenant-loop
    safety requirement 7.F's own task enforces.

    Patch target: `payments.tasks.flag_stuck_refunds`, not
    `payments.services.flag_stuck_refunds`. The task is expected to do
    `from payments.services import flag_stuck_refunds` and call it as a bare
    module-global name — same "from module import name, not a namespaced
    reference" convention `test_booking_expire_appointments.py` pins for
    `booking.tasks`'s own call to `expire_overdue_appointments` — so the name
    to intercept lives in `payments.tasks`'s own namespace, not
    `payments.services`'s.
    """
    caplog.set_level(logging.ERROR, logger="payments.tasks")
    stuck_since = timezone.now() - STUCK_REFUND_THRESHOLD - dt.timedelta(hours=1)
    appt_a = _make_confirmed_appointment(salon, specialist, service, customer)
    payment_a = _make_refund_pending_payment(salon, appt_a, refund_initiated_at=stuck_since)
    payment_b = _make_stuck_refund_payment_for_salon(other_salon, refund_initiated_at=stuck_since)

    failing_salon_id = salon.id

    def _fake_flag_stuck_refunds(*, salon, now):
        if salon.id == failing_salon_id:
            raise RuntimeError("boom")
        # A minimal stand-in for the real service, scoped exactly like it
        # would be, so salon B's row is observably flagged without
        # depending on the (not-yet-written) real service.
        return Payment.objects.filter(
            salon=salon,
            status=PaymentStatus.REFUND_PENDING,
            refund_initiated_at__lte=now - STUCK_REFUND_THRESHOLD,
            flagged_for_review=False,
        ).update(flagged_for_review=True)

    monkeypatch.setattr(payments_tasks, "flag_stuck_refunds", _fake_flag_stuck_refunds)

    flag_stuck_refund_payments()  # must not raise/propagate

    with tenant_context(salon.id):
        row_a = Payment.objects.get(pk=payment_a.id)
    with tenant_context(other_salon.id):
        row_b = Payment.objects.get(pk=payment_b.id)
    assert row_a.flagged_for_review is False
    assert row_b.flagged_for_review is True
    assert any(str(failing_salon_id) in record.getMessage() for record in caplog.records)


# --- 10. Under-lock recheck is load-bearing, not dead code -------------------


@pytest.mark.django_db(transaction=True)
def test_flag_stuck_refunds_race_lost_between_query_and_lock_is_caught_by_recheck(
    monkeypatch, salon, specialist, service, customer
):
    """
    Proves the under-lock recheck (`if payment.flagged_for_review: continue`
    / `if payment.status != PaymentStatus.REFUND_PENDING: continue`) is
    load-bearing, not dead code the query filter already makes unreachable.

    The row IS a genuine candidate at the unlocked query — flagged_for_review
    is still False there — but a separate, already-COMMITTED write flips it
    to flagged_for_review=True before select_for_update's own SELECT runs,
    landing strictly in the gap between the unlocked candidate query (already
    evaluated by the time the loop body runs — Django materializes the whole
    values_list the moment the for loop starts iterating) and the locked
    read.

    @pytest.mark.django_db(transaction=True) (same precedent as
    test_payments_initiate_payment.py's transaction-boundary test) makes the
    competing atomic() block below a REAL commit, not a savepoint nested
    inside one giant uncommitted test transaction — satisfying "committed
    before select_for_update reads" for real, not just via same-connection
    read-your-own-writes visibility.

    Monkeypatch target: django.db.models.query.QuerySet.select_for_update —
    the exact ORM call the service makes between "candidate_ids already
    evaluated" and "the row is actually read/locked". TenantScopedQuerySet
    (Payment's queryset class) does not override select_for_update, so
    patching the base class transparently intercepts
    Payment.objects.select_for_update() too. The recheck's own two `if`
    statements are never touched. A one-shot `fired` guard + `self.model is
    Payment` check limits the interception to exactly the call this test
    cares about.
    """
    appt = _make_confirmed_appointment(salon, specialist, service, customer)
    stuck_payment = _make_refund_pending_payment(salon, appt, refund_initiated_at=STUCK_SINCE)

    fired = {"done": False}
    original_select_for_update = QuerySet.select_for_update

    def _select_for_update_with_race(self, *args, **kwargs):
        if not fired["done"] and self.model is Payment:
            fired["done"] = True
            # A separate, fully-committed transaction (transaction=True
            # makes this a real COMMIT) landing after the unlocked candidate
            # query already captured this row, but before the row is
            # locked/read.
            with tenant_context(salon.id), transaction.atomic():
                Payment.objects.filter(pk=stuck_payment.pk).update(flagged_for_review=True)
        return original_select_for_update(self, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "select_for_update", _select_for_update_with_race)

    with tenant_context(salon.id):
        count = flag_stuck_refunds(salon=salon, now=NOW)

    # The row DID reach select_for_update — proof it was a genuine candidate
    # at the unlocked query (test 4's already-flagged-at-creation row never
    # reaches select_for_update at all: the query filter excludes it first).
    assert fired["done"] is True
    # ...yet the recheck excluded it, not the query filter: the query had
    # already let it through before the competing write even happened.
    assert count == 0
    with tenant_context(salon.id):
        row = Payment.objects.get(pk=stuck_payment.pk)
    # True because of the RACE write above, not because the service flagged it.
    assert row.flagged_for_review is True
