"""
Stage 9 step (b.4.2) — notifications.tasks.send_notification_task
(docs/ARCHITECTURE.md § 9; docs/DECISIONS.md § Stage 9 decisions, step (b)).
Real-DB.

Written before notifications/tasks.py exists — expected to fail on
collection (ModuleNotFoundError: No module named 'notifications.tasks')
until it is added.

CELERY_TASK_ALWAYS_EAGER is True in tests (conftest._celery_eager), so
.delay(...) runs the task synchronously.

The failure test pins exactly our boundary and nothing else: when
self.retry() reports retries exhausted (MaxRetriesExceededError), the task
catches it and calls mark_notification_failed so the row lands FAILED. It
does NOT exercise Celery's real retry loop — how eager mode counts and
re-invokes retries is version-dependent, and that counting is Celery's
job, tested upstream — so self.retry is stubbed to raise the exhausted
signal directly. See docs/DECISIONS.md § Stage 9 Open questions for why
the full retry loop is not integration-tested at unit level.
"""

import pytest
from celery.exceptions import MaxRetriesExceededError
from django.core import mail

from core.tenancy import get_current_salon_id, tenant_context
from notifications import tasks as notif_tasks
from notifications.channels.base import NotificationChannel
from notifications.models import Notification, NotificationStatus, NotificationTrigger
from notifications.tasks import send_notification_task

pytestmark = pytest.mark.django_db


class _RaisingChannel(NotificationChannel):
    def send(self, *, recipient: str, subject: str, body: str) -> None:
        raise RuntimeError("smtp down")


def _make_pending(salon, customer=None, *, dedup_key="k"):
    with tenant_context(salon.id):
        return Notification.objects.create(
            salon=salon,
            customer=customer,
            trigger_type=NotificationTrigger.BOOKING_CONFIRMED,
            channel="email",
            dedup_key=dedup_key,
            status=NotificationStatus.PENDING,
        )


def _reload(salon, notification_id):
    with tenant_context(salon.id):
        return Notification.objects.get(pk=notification_id)


def test_send_notification_task_sends_via_email_and_marks_sent(salon, customer):
    notification = _make_pending(salon, customer)

    send_notification_task.delay(notification.id, salon.id)

    assert _reload(salon, notification.id).status == NotificationStatus.SENT
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [customer.email]


def test_send_notification_task_marks_failed_when_retry_signals_exhausted(
    salon, customer, monkeypatch
):
    # Our boundary only: the send fails, self.retry() reports exhausted, and
    # OUR except-branch writes FAILED. self.retry is stubbed to raise the
    # exhausted signal directly, so nothing here depends on how Celery's
    # eager mode counts or re-invokes retries.
    monkeypatch.setitem(notif_tasks._CHANNELS, "email", _RaisingChannel)

    def _retry_exhausted(*args, **kwargs):
        raise MaxRetriesExceededError("simulated: retries exhausted")

    monkeypatch.setattr(send_notification_task, "retry", _retry_exhausted)
    notification = _make_pending(salon, customer)

    result = send_notification_task.delay(notification.id, salon.id)

    assert result.successful()  # task swallowed the exhausted signal, ended cleanly
    reloaded = _reload(salon, notification.id)
    assert reloaded.status == NotificationStatus.FAILED
    assert reloaded.sent_at is None


def test_send_notification_task_binds_its_own_tenant_context(salon, customer):
    notification = _make_pending(salon, customer)

    assert get_current_salon_id() is None  # the caller binds nothing
    send_notification_task.delay(notification.id, salon.id)

    assert _reload(salon, notification.id).status == NotificationStatus.SENT
