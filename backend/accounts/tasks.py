"""
Auth email sending (docs/DECISIONS.md § Stage 3-R.D.3, § Stage 3-R.D.5).

Standalone Celery tasks, not the Notification/channel abstraction of
docs/ARCHITECTURE.md § 9 — reconciling that abstraction with these
credential emails is Stage 9 work. Always dispatched via ``.delay()`` from
the view layer, never called directly — an SMTP timeout must not become a
failed HTTP response.

Each task takes plain primitives (recipient address, the pre-built token /
uid, salon slug): the view mints the credential while tenant context is
bound and passes what it already holds, so the worker never re-derives it
through ``unscoped_objects`` and imports no model.
"""

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail

from core.urls import build_salon_frontend_url


@shared_task
def send_verification_email(recipient_email: str, token: str, salon_slug: str) -> None:
    link = build_salon_frontend_url(salon_slug, "/verify-email") + f"#token={token}"
    send_mail(
        subject="Confirm your email",
        message=f"Confirm your email by visiting: {link}\n\nThis link expires in 48 hours.",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient_email],
    )


@shared_task
def send_password_reset_email(recipient_email: str, uid: str, token: str, salon_slug: str) -> None:
    link = build_salon_frontend_url(salon_slug, "/reset-password") + f"#uid={uid}&token={token}"
    send_mail(
        subject="Reset your password",
        message=f"Reset your password by visiting: {link}\n\nThis link expires in 1 hour.",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient_email],
    )
