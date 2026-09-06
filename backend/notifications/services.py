"""
Stage 9 step (b.4.1) — the notification send service (docs/ARCHITECTURE.md
§ 9; docs/DECISIONS.md § Stage 9 decisions, step (b)).

Two domain transitions on a Notification row — send_notification
(PENDING -> SENT) and mark_notification_failed (PENDING -> FAILED) — plus
the recipient resolver and the message builder they use.

Requires tenant context to already be bound (core.tenancy.tenant_context);
these functions do not bind it themselves, the same convention as
payments.services.flag_stuck_refunds and
booking.services.expire_overdue_appointments. The Celery task that owns the
cross-salon loop and the tenant binding is step b.4.2; tests bind it
directly.

`now` is an explicit parameter with no default (system-clock-is-I/O).
"""

import datetime as dt

import psycopg
from django.conf import settings
from django.db import IntegrityError, transaction

from booking.guest_tokens import derive_guest_token
from booking.models import Appointment, AppointmentStatus
from core.formatting import format_datetime_for_salon
from notifications.channels.base import NotificationChannel
from notifications.models import Notification, NotificationStatus, NotificationTrigger
from notifications.models import NotificationChannel as ChannelChoices
from tenants.models import Salon


def _resolve_recipient(notification: Notification) -> str:
    """
    The recipient resolution the dumb-pipe NotificationChannel deliberately
    does not do (see notifications/channels/base.py).

    `salon` is always present (Notification is a TenantScopedModel);
    `customer` is nullable. customer populated -> the notification is for
    that client, delivered to Customer.email. customer null -> the
    notification is an operational alert for the salon itself, delivered to
    Salon.contact_email (docs/DECISIONS.md § Stage 9 decisions).
    """
    customer = notification.customer
    if customer is not None:
        return customer.email
    return notification.salon.contact_email


# The single builder seam (docs/DECISIONS.md § Stage 9 decisions, step
# (b)). Inline text, one language, no template system. When Stage 11.5
# localization lands, only _build_message's internals change.
#
# BOOKING_CONFIRMED and APPOINTMENT_REMINDER have no entry here: each is
# built from the notification's appointment in _build_message instead of
# being a static lookup (see docs/DECISIONS.md § Step (d) decisions and
# § Step (e) decisions) — the manage-link URL and the human-readable
# appointment time can't be fixed strings.
_MESSAGES: dict[str, tuple[str, str]] = {
    NotificationTrigger.BOOKING_CANCELLED.value: (
        "Your booking was cancelled",
        "Your booking has been cancelled.",
    ),
    NotificationTrigger.PAYMENT_SUCCEEDED.value: (
        "Payment received",
        "We have received your deposit payment.",
    ),
    NotificationTrigger.PAYMENT_FAILED.value: (
        "Payment failed",
        "Your deposit payment could not be processed.",
    ),
    NotificationTrigger.REVIEW_REQUEST.value: (
        "How was your visit?",
        "We would love your feedback on your recent visit.",
    ),
}


def _build_message(notification: Notification) -> tuple[str, str]:
    """
    Map a notification's trigger_type to its (subject, body) — the one
    place the wording lives, so Stage 11.5 localization is a swap of this
    function's internals and nothing else on the send path.

    Every trigger but BOOKING_CONFIRMED and APPOINTMENT_REMINDER is a fixed
    string from _MESSAGES. Those two are built from the notification's
    appointment instead (salon name, human-readable local time, the
    re-derived guest-access manage link) — see docs/DECISIONS.md § Step (d)
    decisions and § Step (e) decisions. A trigger_type with no entry in
    _MESSAGES and no special case here (EMAIL_VERIFICATION today) raises
    rather than sending a blank email: a missing message is a bug, not a
    valid empty send.
    """
    if notification.trigger_type == NotificationTrigger.APPOINTMENT_REMINDER:
        appointment = notification.appointment
        if appointment is None:
            raise ValueError(
                "APPOINTMENT_REMINDER notification "
                f"{notification.pk!r} has no appointment to build its message from"
            )
        salon = appointment.salon
        when = format_datetime_for_salon(appointment.start_datetime, salon.timezone)
        token = derive_guest_token(appointment.id)
        link = (
            f"{settings.FRONTEND_URL}/salons/{salon.slug}/appointments/{appointment.id}"
            f"/manage/{token}/"
        )
        subject = "Reminder: your appointment tomorrow"
        body = (
            f"This is a reminder of your appointment at {salon.name} on {when}.\n\n"
            f"View or cancel your booking: {link}"
        )
        return subject, body

    if notification.trigger_type == NotificationTrigger.BOOKING_CONFIRMED:
        appointment = notification.appointment
        if appointment is None:
            raise ValueError(
                "BOOKING_CONFIRMED notification "
                f"{notification.pk!r} has no appointment to build its message from"
            )
        salon = appointment.salon
        when = format_datetime_for_salon(appointment.start_datetime, salon.timezone)
        token = derive_guest_token(appointment.id)
        link = (
            f"{settings.FRONTEND_URL}/salons/{salon.slug}/appointments/{appointment.id}"
            f"/manage/{token}/"
        )
        subject = "Your booking is confirmed"
        body = (
            f"Your booking at {salon.name} on {when} is confirmed.\n\n"
            f"View or cancel your booking: {link}"
        )
        return subject, body

    try:
        return _MESSAGES[str(notification.trigger_type)]
    except KeyError:
        raise ValueError(
            f"no message builder for notification trigger_type {notification.trigger_type!r}"
        ) from None


def send_notification(
    *,
    notification_id: int,
    salon: Salon,
    channel: NotificationChannel,
    now: dt.datetime,
) -> None:
    """
    The PENDING -> SENT transition (docs/ARCHITECTURE.md § 9). Mirrors the
    lock-and-recheck shape of payments.services.flag_stuck_refunds.

    channel.send() is called INSIDE the transaction.atomic() block, while
    the select_for_update row lock is held. This is deliberate and differs
    from Stage 8's initiate_payment / initiate_refund, which keep the
    provider call outside the transaction:

      - ARCHITECTURE § 9 requires the PENDING -> SENT transition to happen
        "under that row's lock" so a retried task or a duplicate trigger
        "cannot produce two emails for the same event". The lock has to
        span the send for that to hold.
      - Stage 8 keeps the network call outside because its locked rows (the
        appointment slot especially) are genuinely contended and a
        duplicate refund/charge is irreversible. Neither is true here: the
        only thing that ever contends for one Notification row is a
        duplicate delivery of that same event — exactly what we want to
        serialize — and a duplicate email is "a minor annoyance", the
        explicit mirror-image of the refund asymmetry (docs/DECISIONS.md §
        Stage 9 decisions, step (b)).
      - Failure mode: if channel.send() succeeds but COMMIT then fails, the
        email went out and the row stays PENDING, so a retry re-sends once.
        Tolerable by the recorded decision. Sending outside the lock would
        instead make a double-send possible under ordinary concurrency, not
        just a rare crash — strictly worse.
      - This runs in a Celery worker (b.4.2), one task per process, so
        holding one connection + one uncontended row lock across the send
        has no request-path connection-pool risk. Prod should still set a
        finite EMAIL_TIMEOUT so a hung SMTP server can't hold the lock
        forever — a settings/ops concern, not this code.

    An already-SENT or already-FAILED row is a silent no-op (the recheck
    below), so a redelivered task never re-sends. channel.send() raising is
    NOT caught: it propagates to the caller, which owns the retry policy
    (step b.4.2), and the row is left PENDING.
    """
    with transaction.atomic():
        notification = Notification.objects.select_for_update().get(salon=salon, pk=notification_id)
        if notification.status != NotificationStatus.PENDING:
            return

        recipient = _resolve_recipient(notification)
        subject, body = _build_message(notification)
        channel.send(recipient=recipient, subject=subject, body=body)

        notification.status = NotificationStatus.SENT
        notification.sent_at = now
        notification.save(update_fields=["status", "sent_at"])


def mark_notification_failed(*, notification_id: int, salon: Salon) -> None:
    """
    The PENDING -> FAILED terminal transition (docs/DECISIONS.md § Stage 9
    decisions, step (b)) — written by the task once send retries are
    exhausted.

    The recheck guards against overwriting a SENT row: a late failure
    signal that arrives after a retry actually succeeded must not flip the
    row back to FAILED. Anything other than PENDING is a no-op.
    """
    with transaction.atomic():
        notification = Notification.objects.select_for_update().get(salon=salon, pk=notification_id)
        if notification.status != NotificationStatus.PENDING:
            return
        notification.status = NotificationStatus.FAILED
        notification.save(update_fields=["status"])


def record_and_dispatch_notification(
    *,
    salon: Salon,
    trigger_type: str,
    appointment: Appointment,
    dedup_key: str,
) -> None:
    """
    Record a PENDING Notification journal row for a trigger event and
    schedule its delivery for after the surrounding transaction commits
    (docs/DECISIONS.md § Stage 9 step (c)). Called from inside a caller's
    own atomic() block, right after a status save — currently
    PaymentWebhookView.post (payments/views.py) and
    booking.services.cancel_appointment.

    Own nested atomic() savepoint, one per call: a redelivered or re-run
    trigger reproduces the same dedup_key, so the INSERT can violate
    notification_trigger_channel_dedup_uniq. That one specific unique
    violation means the event's notification is already journalled — a
    no-op, and no second send is dispatched. The savepoint keeps that
    rollback from touching the outer transaction or a sibling call's row.
    Any other IntegrityError — a composite tenant FK violation especially —
    is re-raised and surfaces as a 500, the same __cause__ narrowing as
    booking.services.create_appointment and core.exceptions.exception_handler.

    In practice each caller's own transition guard (a status is only
    transitioned from its expected starting state) already stops a
    redelivery or re-run before it reaches this INSERT; the unique-violation
    branch is defence in depth.
    """
    # Imported here, not at module top: notifications.tasks imports
    # send_notification/mark_notification_failed from this module, so a
    # top-level import the other way would be a circular import.
    from notifications.tasks import send_notification_task

    try:
        with transaction.atomic():
            notification = Notification.objects.create(
                salon=salon,
                trigger_type=trigger_type,
                channel=ChannelChoices.EMAIL,
                appointment=appointment,
                customer_id=appointment.customer_id,
                dedup_key=dedup_key,
            )
    except IntegrityError as exc:
        cause = exc.__cause__
        if (
            isinstance(cause, psycopg.errors.UniqueViolation)
            and cause.diag.constraint_name == "notification_trigger_channel_dedup_uniq"
        ):
            return
        raise

    # Bound into locals (not read off `notification`/`salon` when the hook
    # fires): each call to this helper has its own frame, so the two hooks
    # the confirming path registers stay independent.
    notification_id = notification.id
    salon_id = salon.id
    transaction.on_commit(lambda: send_notification_task.delay(notification_id, salon_id))


def send_due_appointment_reminders(*, salon: Salon, now: dt.datetime) -> int:
    """
    docs/DECISIONS.md § Step (e) decisions — the day-before appointment
    reminder sweep. Requires tenant context to already be bound
    (core.tenancy.tenant_context); this function does not bind it itself,
    the same convention as payments.services.flag_stuck_refunds and
    booking.services.expire_overdue_appointments. Called once per salon by
    the Celery task that owns the cross-salon loop and the tenant binding.

    Candidate ids are found with an unlocked query, then each row is locked
    and rechecked independently under its own transaction.atomic() — not one
    lock over the whole batch — mirroring flag_stuck_refunds.

    Window: start_datetime in (now + 24h, now + 25h] — lower bound strict,
    upper bound inclusive. An appointment booked less than 24h before its
    own start is always closer than 24h away, so it can never enter this
    window on any run: short-notice bookings get no reminder by
    construction, with no explicit check.

    Status: CONFIRMED only (not ACTIVE_APPOINTMENT_STATUSES, which also
    includes unpaid PENDING_PAYMENT holds).

    Dedup: notification_trigger_channel_dedup_uniq remains the real guard
    against a concurrent run double-sending. The explicit .exists() check
    below only keeps the returned count honest for sequential runs —
    record_and_dispatch_notification returns None whether it inserted or
    swallowed the UniqueViolation, so without the pre-check this function
    could not tell a fresh reminder from an already-recorded one. This is
    the same "guard stops it, unique-violation branch is defence in depth"
    split already documented on record_and_dispatch_notification.
    """
    candidate_ids = Appointment.objects.filter(
        salon=salon,
        status=AppointmentStatus.CONFIRMED,
        start_datetime__gt=now + dt.timedelta(hours=24),
        start_datetime__lte=now + dt.timedelta(hours=25),
    ).values_list("id", flat=True)

    count = 0
    for appointment_id in candidate_ids:
        with transaction.atomic():
            appointment = Appointment.objects.select_for_update().get(
                salon=salon, pk=appointment_id
            )
            if appointment.status != AppointmentStatus.CONFIRMED:
                continue
            dedup_key = f"appointment_reminder:appointment:{appointment.pk}"
            already_reminded = Notification.objects.filter(
                trigger_type=NotificationTrigger.APPOINTMENT_REMINDER,
                channel=ChannelChoices.EMAIL,
                dedup_key=dedup_key,
            ).exists()
            if already_reminded:
                continue
            record_and_dispatch_notification(
                salon=salon,
                trigger_type=NotificationTrigger.APPOINTMENT_REMINDER,
                appointment=appointment,
                dedup_key=dedup_key,
            )
            count += 1
    return count
