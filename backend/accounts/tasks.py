"""
Auth email sending (docs/DECISIONS.md § Stage 3-R.D.3, § Stage 3-R.D.5).

Standalone Celery tasks, not the Notification/channel abstraction of
docs/ARCHITECTURE.md § 9 — reconciling that abstraction with these
credential emails is Stage 9 work. Always dispatched via ``.delay()`` from
the view layer, never called directly — an SMTP timeout must not become a
failed HTTP response.

Each task takes plain primitives (recipient address, the pre-built token /
uid, salon slug; the email-change letters take the pre-built link and the
already-resolved salon name instead): the view mints the credential while tenant context is
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
def send_email_change_confirmation_email(recipient_email: str, link: str, salon_name: str) -> None:
    """New address, free (docs/DECISIONS.md § "Item 8 decisions (change
    email)", email 1). The view builds ``link`` and resolves ``salon_name``."""
    send_mail(
        subject="Підтвердіть нову адресу пошти",
        message=(
            f"Для облікового запису в салоні {salon_name} надійшов запит на зміну "
            f"адреси пошти на цю адресу.\n\n"
            f"Щоб підтвердити нову адресу, перейдіть за посиланням: {link}\n\n"
            f"Посилання дійсне 24 години. Якщо ви не надсилали цей запит, "
            f"просто проігноруйте цей лист."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient_email],
    )


@shared_task
def send_email_change_unavailable_email(recipient_email: str, salon_name: str) -> None:
    """New address, taken (email 2). Deliberately carries no link."""
    send_mail(
        subject="Зміна адреси пошти",
        message=(
            f"Хтось надіслав запит змінити адресу пошти облікового запису в салоні "
            f"{salon_name} на цю адресу.\n\n"
            f"Цю адресу не можна використати."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient_email],
    )


@shared_task
def send_email_change_notice_email(recipient_email: str, new_email: str, salon_name: str) -> None:
    """Old address, sent after every correct-password request (email 3)."""
    send_mail(
        subject="Запит на зміну адреси пошти",
        message=(
            f"Для вашого облікового запису в салоні {salon_name} надійшов запит на "
            f"зміну адреси пошти на {new_email}.\n\n"
            f"Поточна адреса працює для входу, доки нову не буде підтверджено.\n\n"
            f"Якщо це були не ви, змініть пароль і зверніться до салону."
        ),
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


@shared_task
def send_password_changed_email(recipient_email: str, salon_name: str) -> None:
    """Notice after a password change (docs/DECISIONS.md § "Item 9 decisions
    (change password): ending sessions", "Endpoint and page, decided
    06.10.2026", point 6)."""
    send_mail(
        subject="Пароль змінено",
        message=(
            f"Вітаємо!\n\n"
            f"Пароль до вашого акаунта в салоні {salon_name} щойно змінено. "
            f"Ви вийшли з акаунта на всіх пристроях.\n\n"
            f"Якщо ви не змінювали пароль, відновіть доступ через «Забули пароль?» "
            f"на сторінці входу.\n\n"
            f"{salon_name}"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient_email],
    )
