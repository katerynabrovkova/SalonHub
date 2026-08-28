"""
Auth email sending (docs/DECISIONS.md § Stage 3-R.D.3).

A standalone Celery task, not the Notification/channel abstraction of
docs/ARCHITECTURE.md § 9 — reconciling that abstraction with these
credential emails is Stage 9 work. Always dispatched via ``.delay()`` from
the view layer, never called directly — an SMTP timeout must not become a
failed HTTP response.

The task takes plain primitives (recipient address, pre-signed token, salon
slug): the view generates the token while tenant context is bound and
passes what it already holds, so the worker never re-derives it through
``unscoped_objects`` (docs/DECISIONS.md § Stage 3-R.D.3).
"""

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail


@shared_task
def send_verification_email(recipient_email: str, token: str, salon_slug: str) -> None:
    link = f"{settings.FRONTEND_URL}/salons/{salon_slug}/verify-email#token={token}"
    send_mail(
        subject="Confirm your email",
        message=f"Confirm your email by visiting: {link}\n\nThis link expires in 48 hours.",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient_email],
    )
