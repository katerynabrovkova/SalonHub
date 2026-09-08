"""
Stage 9 step (e) part 2 — notifications.tasks.send_due_appointment_reminders_task,
the periodic day-before reminder sweep task (docs/DECISIONS.md § Step (e)
decisions).

Mirrors payments.tasks.flag_stuck_refund_payments exactly: `now` read once
before the loop, Salon.objects.all() iterated, tenant_context(salon.id)
bound per iteration, per-salon try/except with logger.exception so one
failing salon does not abort the run.

Written before the task exists — expected red: ImportError on
send_due_appointment_reminders_task until notifications/tasks.py adds it.

Called directly and synchronously (not via .delay()), the same discipline
as the flag_stuck_refund_payments task tests. The patch target for the
one-salon-failure double is notifications.tasks.send_due_appointment_reminders
(the bare imported name in the task module's own namespace), the same
"from module import name, not a namespaced reference" convention
test_payments_flag_stuck_refunds.py pins.
"""

import datetime as dt
import logging

import pytest
from django.core import mail
from django.utils import timezone

from accounts.models import Customer
from booking.models import AppointmentStatus
from catalog.models import Service, ServiceCategory
from core.tenancy import tenant_context
from notifications import tasks as notif_tasks
from notifications.models import Notification, NotificationStatus, NotificationTrigger
from notifications.tasks import send_due_appointment_reminders_task
from specialists.models import Specialist
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db


def _entities(salon, *, suffix):
    with tenant_context(salon.id):
        category = ServiceCategory.objects.create(salon=salon, name=f"Nails {suffix}")
        service = Service.objects.create(
            salon=salon,
            category=category,
            name=f"Manicure {suffix}",
            duration_minutes=60,
            price="500.00",
            buffer_minutes=15,
        )
        specialist = Specialist.objects.create(salon=salon, name=f"Jane {suffix}")
        customer = Customer.objects.create(
            salon=salon,
            name=f"Alice {suffix}",
            email=f"alice-{suffix}@example.com",
            phone="+10000000000",
        )
    return specialist, service, customer


def _reminder_rows(salon):
    with tenant_context(salon.id):
        return list(
            Notification.objects.filter(trigger_type=NotificationTrigger.APPOINTMENT_REMINDER)
        )


def test_task_calls_service_once_per_salon_with_a_single_shared_now(
    salon, other_salon, monkeypatch
):
    calls: list[tuple[int, dt.datetime]] = []

    def _fake(*, salon, now):
        calls.append((salon.id, now))
        return 0

    monkeypatch.setattr(notif_tasks, "send_due_appointment_reminders", _fake)

    send_due_appointment_reminders_task()

    assert len(calls) == 2
    assert {salon_id for salon_id, _ in calls} == {salon.id, other_salon.id}
    nows = {now for _, now in calls}
    assert len(nows) == 1  # one timezone.now(), read before the loop, shared across salons


def test_task_delivers_reminders_across_two_salons(
    salon, other_salon, django_capture_on_commit_callbacks
):
    spec_a, svc_a, cust_a = _entities(salon, suffix="a")
    spec_b, svc_b, cust_b = _entities(other_salon, suffix="b")
    start = timezone.now() + dt.timedelta(hours=24, minutes=30)  # inside (now+24h, now+25h]
    appt_a = make_appointment(
        salon=salon,
        customer=cust_a,
        specialist=spec_a,
        service=svc_a,
        start=start,
        status=AppointmentStatus.CONFIRMED,
    )
    appt_b = make_appointment(
        salon=other_salon,
        customer=cust_b,
        specialist=spec_b,
        service=svc_b,
        start=start,
        status=AppointmentStatus.CONFIRMED,
    )

    with django_capture_on_commit_callbacks(execute=True):
        send_due_appointment_reminders_task()

    assert [r.appointment_id for r in _reminder_rows(salon)] == [appt_a.id]
    assert [r.appointment_id for r in _reminder_rows(other_salon)] == [appt_b.id]
    assert all(
        r.status == NotificationStatus.SENT
        for r in _reminder_rows(salon) + _reminder_rows(other_salon)
    )
    assert sorted(m.to[0] for m in mail.outbox) == sorted([cust_a.email, cust_b.email])


def test_task_continues_past_a_failing_salon(
    salon, other_salon, monkeypatch, caplog, django_capture_on_commit_callbacks
):
    spec_b, svc_b, cust_b = _entities(other_salon, suffix="b")
    start = timezone.now() + dt.timedelta(hours=24, minutes=30)
    appt_b = make_appointment(
        salon=other_salon,
        customer=cust_b,
        specialist=spec_b,
        service=svc_b,
        start=start,
        status=AppointmentStatus.CONFIRMED,
    )
    caplog.set_level(logging.ERROR, logger="notifications.tasks")

    real_service = notif_tasks.send_due_appointment_reminders
    failing_salon_id = salon.id

    def _fake(*, salon, now):
        if salon.id == failing_salon_id:
            raise RuntimeError("boom")
        return real_service(salon=salon, now=now)

    monkeypatch.setattr(notif_tasks, "send_due_appointment_reminders", _fake)

    with django_capture_on_commit_callbacks(execute=True):
        send_due_appointment_reminders_task()  # must not raise / propagate

    assert [r.appointment_id for r in _reminder_rows(other_salon)] == [appt_b.id]
    assert any(str(failing_salon_id) in rec.getMessage() for rec in caplog.records)
