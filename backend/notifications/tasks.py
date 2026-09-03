"""
Stage 9 step (b.4.2) — the Celery task wrapping the send service
(docs/ARCHITECTURE.md § 9; docs/DECISIONS.md § Stage 9 decisions, step (b)).

Thin orchestration over notifications.services: pick the channel adapter,
bind tenant context, call send_notification; on failure retry a small
bounded number of times, and once retries are spent call
mark_notification_failed so the row lands in its honest terminal state
rather than stuck PENDING.

Conventions mirror payments/tasks.py: module-level logger, @shared_task,
tenant_context bound per call from an id the caller already holds (never
re-derived via unscoped_objects).
"""

import logging

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from django.utils import timezone

from core.tenancy import tenant_context
from notifications.channels.base import NotificationChannel
from notifications.channels.email import EmailChannel
from notifications.models import Notification
from notifications.services import mark_notification_failed, send_notification
from tenants.models import Salon

logger = logging.getLogger(__name__)

# Channel adapter per Notification.channel value. Email-only today; the
# Telegram adapter is added here in Stage 10 (same interface). A plain
# module-level dict, not a settings-based registry (premature) — a test
# overrides an entry with monkeypatch.setitem, the task-shaped equivalent
# of the provider_class view attribute payments uses.
_CHANNELS: dict[str, type[NotificationChannel]] = {
    "email": EmailChannel,  # NotificationChannel.EMAIL value (notifications.models)
}


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_notification_task(self, notification_id: int, salon_id: int) -> None:
    """
    salon_id is passed in by the dispatcher (step c) because the triggering
    event happened in that salon — it is never re-derived here via
    unscoped_objects.

    Retry policy (docs/DECISIONS.md § Stage 9 decisions, step (b)): on a
    send failure, self.retry() a small bounded number of times
    (max_retries=3, ~60s apart). self.retry() owns the retry count; when it
    is exhausted it raises MaxRetriesExceededError — the signal to call
    mark_notification_failed and stop. self.request.retries is never
    hand-counted here. self.retry() is called without exc= so the
    exhausted branch is always MaxRetriesExceededError (the exc= branch is
    version-sensitive); the failure itself is logged here instead.

    A redelivered or duplicate task is already a no-op via the send
    service's own under-lock status recheck, so this task adds no
    idempotency logic of its own.
    """
    now = timezone.now()
    with tenant_context(salon_id):
        salon = Salon.objects.get(pk=salon_id)
        notification = Notification.objects.get(pk=notification_id)
        channel = _CHANNELS[notification.channel]()
        try:
            send_notification(
                notification_id=notification_id, salon=salon, channel=channel, now=now
            )
        except Exception as exc:
            logger.warning(
                "send_notification_task: send failed for notification_id=%s: %r",
                notification_id,
                exc,
            )
            try:
                self.retry()
            except MaxRetriesExceededError:
                mark_notification_failed(notification_id=notification_id, salon=salon)
