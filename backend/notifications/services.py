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
from django.db import IntegrityError, transaction

from booking import amounts
from booking.guest_tokens import derive_guest_token
from booking.models import Appointment, AppointmentStatus, GuestAccessToken
from core.formatting import (
    format_amount,
    format_deadline_for_salon,
    format_duration,
    format_full_datetime_for_salon,
    format_time_for_salon,
)
from core.i18n import resolve_language_code, resolve_translation
from core.tenancy import tenant_context
from core.urls import build_salon_frontend_url
from notifications.channels.base import NotificationChannel
from notifications.models import (
    BookingLinkMode,
    Notification,
    NotificationStatus,
    NotificationTrigger,
)
from notifications.models import NotificationChannel as ChannelChoices
from payments.models import PaymentStatus
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
# (b); § Stage 11.5 "Notification message builder: language resolution").
# Inline text, no template system — but now per-language-keyed:
# {trigger: {lang_code: (subject, body)}}. When another language is added
# it gets a key here; the resolution logic does not change.
#
# Every booking email has no entry here: each is built from the
# notification's appointment in _build_message, with its texts in
# _BOOKING_EMAIL_TEXTS below — the link, the appointment's details and the
# money lines can't be fixed strings. PAYMENT_SUCCEEDED is no longer sent
# (docs/DECISIONS.md "Notifications") and has no text: building one raises.
_MESSAGES: dict[str, dict[str, tuple[str, str]]] = {
    NotificationTrigger.REVIEW_REQUEST.value: {
        "en": (
            "How was your visit?",
            "We would love your feedback on your recent visit.",
        ),
        "uk": (
            "Як пройшов ваш візит?",
            "Будемо вдячні за ваш відгук про нещодавній візит.",
        ),
    },
}


def _recipient_language(notification: Notification) -> str | None:
    """
    The recipient's language *preference* for rendering this notification
    (docs/DECISIONS.md § "Notification message builder: language
    resolution"): ``Customer.preferred_language`` when a customer is set,
    else None. A customer-less (salon-directed) notification, or a customer
    with an empty preference, falls back to English downstream — via
    ``resolve_language_code`` for the message template and
    ``resolve_translation`` for ``salon.name``.

    ``notification.customer`` is the canonical recipient FK (already used
    by ``_resolve_recipient``), not ``notification.appointment.customer``.
    """
    if notification.customer is not None:
        return notification.customer.preferred_language or None
    return None


def _build_message(notification: Notification, *, now: dt.datetime) -> tuple[str, str]:
    """
    Map a notification's trigger_type to its (subject, body), rendered in
    the recipient's language — the one place the wording lives.

    Every booking email (BOOKING_CREATED, BOOKING_CONFIRMED,
    BOOKING_CANCELLED, BOOKING_EXPIRED, PAYMENT_FAILED, APPOINTMENT_REMINDER,
    LATE_PAYMENT_REFUNDED) is built from the notification's appointment, with the literal fragments
    per-language-keyed in _BOOKING_EMAIL_TEXTS and the interpolated values
    resolved in the same language (docs/DECISIONS.md "Notifications"). Any
    other trigger is a per-language lookup in _MESSAGES. A trigger_type with
    no entry in _MESSAGES and no builder here (EMAIL_VERIFICATION, and
    PAYMENT_SUCCEEDED, which is no longer sent) raises rather than sending a
    blank email: a missing message is a bug, not a valid empty send.
    """
    preferred = _recipient_language(notification)
    lang_code = resolve_language_code(preferred)

    if notification.trigger_type in _APPOINTMENT_TRIGGERS:
        appointment = notification.appointment
        if appointment is None:
            raise ValueError(
                f"{notification.trigger_type} notification "
                f"{notification.pk!r} has no appointment to build its message from"
            )
        # The guest-token lookup goes through a tenant-scoped manager; bind
        # the appointment's own salon so the build also works where the
        # caller has no tenant bound (CLAUDE.md: code outside a request binds
        # tenant_context itself).
        with tenant_context(appointment.salon_id):
            if notification.trigger_type in (
                NotificationTrigger.BOOKING_CONFIRMED,
                NotificationTrigger.APPOINTMENT_REMINDER,
            ):
                return _build_booking_email(
                    appointment,
                    reminder=notification.trigger_type == NotificationTrigger.APPOINTMENT_REMINDER,
                    lang_code=lang_code,
                    preferred=preferred,
                    now=now,
                )
            return _build_status_email(
                notification, appointment, lang_code=lang_code, preferred=preferred
            )

    try:
        return _MESSAGES[str(notification.trigger_type)][lang_code]
    except KeyError:
        raise ValueError(
            f"no message builder for notification trigger_type {notification.trigger_type!r}"
        ) from None


_BOOKING_EMAIL_TEXTS: dict[str, dict[str, str]] = {
    "uk": {
        "confirmed_subject": "Ваш запис у {salon} підтверджено",
        "reminder_subject": "Нагадування: завтра о {time} у {salon}",
        "greeting": "Вітаємо, {name}!",
        "confirmed_intro": "Ваш запис підтверджено:",
        "reminder_intro": "Нагадуємо про ваш запис завтра:",
        "service": "Послуга: {value}",
        "specialist": "Майстер: {value}",
        "when": "Коли: {value}",
        "duration": "Тривалість: {value}",
        "deposit": "Передоплата: {value} (сплачено)",
        "amount_due": "Оплата при візиті: {value}",
        "deadline": (
            "Скасувати запис з поверненням передоплати можна не пізніше ніж за 24 години "
            "до візиту, тобто до {deadline}. Пізніше передоплата не повертається."
        ),
        "deadline_passed": (
            "До візиту менше 24 годин, тому при скасуванні передоплата не повертається."
        ),
        "confirmed_link": "Переглянути або скасувати запис:",
        "reminder_link": "Якщо плани змінились, скасувати запис можна за посиланням:",
        "last_with_phone": (
            "Якщо ви не записувалися в {salon}, будь ласка, повідомте салон за номером вище."
        ),
        "last_without_phone": "Якщо ви не записувалися в {salon}, будь ласка, повідомте салон.",
        "created_subject": "Ваш запис у {salon} створено, залишилось оплатити",
        "created_intro": "Ваш запис створено:",
        "deposit_due": "Передоплата: {value}",
        "created_hold": (
            "Ми тримаємо цей час для вас до {time}. Якщо не встигнете оплатити, запис "
            "скасується автоматично."
        ),
        "created_guest_link": "Оплатити передоплату:",
        "created_account_link": "Оплатити передоплату можна в особистому кабінеті:",
        "cancelled_subject": "Ваш запис у {salon} скасовано",
        "cancelled_intro": "Ваш запис скасовано:",
        "refund_owed": (
            "Передоплату {value} буде повернено на картку, з якої ви платили. "
            "Термін залежить від вашого банку."
        ),
        "deposit_kept": (
            "Запис скасовано менш ніж за 24 години до візиту, тому передоплата {value} "
            "не повертається."
        ),
        "book_again": "Записатися знову:",
        "cancelled_last_with_phone": (
            "Якщо ви не скасовували цей запис, будь ласка, зверніться до салону за номером вище."
        ),
        "cancelled_last_without_phone": (
            "Якщо ви не скасовували цей запис, будь ласка, зверніться до салону."
        ),
        "expired_subject": "Запис у {salon} не підтверджено",
        "expired_intro": "Час на оплату минув, тому запис не підтверджено і цей час звільнено:",
        "failed_subject": "Оплата в {salon} не пройшла",
        "failed_intro": "Не вдалося провести передоплату за запис:",
        "failed_hold": "Ми ще тримаємо цей час для вас до {time}. Спробувати оплатити ще раз:",
        "late_subject": "Повернення передоплати в {salon}",
        "late_intro": (
            "Ваша оплата надійшла, коли запис уже був недійсний (час на оплату минув або "
            "запис скасовано), тому ми повертаємо передоплату повністю:"
        ),
        "refund_amount": "Сума повернення: {value}",
        "late_refund_info": (
            "Гроші повернуться на картку, з якої ви платили. Термін залежить від вашого банку."
        ),
        "late_book_again": "Щоб записатися знову, перейдіть на сайт салону:",
    },
    "en": {
        "confirmed_subject": "Your booking at {salon} is confirmed",
        "reminder_subject": "Reminder: tomorrow at {time} at {salon}",
        "greeting": "Hello, {name}!",
        "confirmed_intro": "Your booking is confirmed:",
        "reminder_intro": "This is a reminder of your booking tomorrow:",
        "service": "Service: {value}",
        "specialist": "Specialist: {value}",
        "when": "When: {value}",
        "duration": "Duration: {value}",
        "deposit": "Deposit: {value} (paid)",
        "amount_due": "Pay at the visit: {value}",
        "deadline": (
            "You can cancel with a refund of the deposit no later than 24 hours before the "
            "visit, that is until {deadline}. After that the deposit is not refunded."
        ),
        "deadline_passed": (
            "There are less than 24 hours before the visit, so the deposit is not refunded "
            "if you cancel."
        ),
        "confirmed_link": "View or cancel your booking:",
        "reminder_link": "If your plans have changed, you can cancel your booking here:",
        "last_with_phone": (
            "If you did not book at {salon}, please let the salon know at the number above."
        ),
        "last_without_phone": "If you did not book at {salon}, please let the salon know.",
        "created_subject": "Your booking at {salon} is created, the deposit is still to be paid",
        "created_intro": "Your booking is created:",
        "deposit_due": "Deposit: {value}",
        "created_hold": (
            "We are holding this time for you until {time}. If you do not pay in time, the "
            "booking will be cancelled automatically."
        ),
        "created_guest_link": "Pay the deposit:",
        "created_account_link": "You can pay the deposit in your account:",
        "cancelled_subject": "Your booking at {salon} is cancelled",
        "cancelled_intro": "Your booking is cancelled:",
        "refund_owed": (
            "The deposit of {value} will be refunded to the card you paid with. "
            "How long it takes depends on your bank."
        ),
        "deposit_kept": (
            "The booking was cancelled less than 24 hours before the visit, so the deposit "
            "of {value} is not refunded."
        ),
        "book_again": "Book again:",
        "cancelled_last_with_phone": (
            "If you did not cancel this booking, please contact the salon at the number above."
        ),
        "cancelled_last_without_phone": (
            "If you did not cancel this booking, please contact the salon."
        ),
        "expired_subject": "Your booking at {salon} was not confirmed",
        "expired_intro": (
            "The time to pay has run out, so the booking was not confirmed and this time "
            "has been released:"
        ),
        "failed_subject": "Your payment at {salon} did not go through",
        "failed_intro": "We could not process the deposit payment for your booking:",
        "failed_hold": "We are still holding this time for you until {time}. Try to pay again:",
        "late_subject": "Deposit refund from {salon}",
        "late_intro": (
            "Your payment arrived when the booking was no longer valid (the time to pay had "
            "run out or the booking had been cancelled), so we are refunding the deposit in "
            "full:"
        ),
        "refund_amount": "Refund amount: {value}",
        "late_refund_info": (
            "The money will return to the card you paid with. How long it takes depends on "
            "your bank."
        ),
        "late_book_again": "To book again, go to the salon's website:",
    },
}

# The booking emails, all built from the notification's appointment.
_APPOINTMENT_TRIGGERS = frozenset(
    trigger.value
    for trigger in (
        NotificationTrigger.BOOKING_CREATED,
        NotificationTrigger.BOOKING_CONFIRMED,
        NotificationTrigger.BOOKING_CANCELLED,
        NotificationTrigger.BOOKING_EXPIRED,
        NotificationTrigger.PAYMENT_FAILED,
        NotificationTrigger.APPOINTMENT_REMINDER,
        NotificationTrigger.LATE_PAYMENT_REFUNDED,
    )
)


def _booking_email_link(appointment: Appointment) -> str:
    """
    The link in BOOKING_CONFIRMED and APPOINTMENT_REMINDER (docs/DECISIONS.md
    "Notifications"): the guest manage page when the appointment has a guest
    token row, /client otherwise. These emails are sent by the payment
    webhook and the reminder sweep, which do not know how the booking was
    made; a token row exists only for guest bookings, so it is the record of
    that. Never inferred from whether the Customer has an Account. The token
    sits in the fragment, never the path (a path reaches server logs).
    """
    salon = appointment.salon
    if GuestAccessToken.objects.filter(appointment=appointment).exists():
        token = derive_guest_token(appointment.id)
        return build_salon_frontend_url(
            salon.slug, f"/appointments/{appointment.id}/manage#token={token}"
        )
    return build_salon_frontend_url(salon.slug, "/client")


def _pay_link(appointment: Appointment) -> str:
    """The guest pay page with the re-derived token in the fragment, never
    the path (docs/DECISIONS.md "Booking > Guest token")."""
    token = derive_guest_token(appointment.id)
    return (
        build_salon_frontend_url(appointment.salon.slug, "/booking/pay")
        + f"#appointment_id={appointment.id}&token={token}"
    )


def _detail_lines(
    appointment: Appointment,
    texts: dict[str, str],
    *,
    lang_code: str,
    preferred: str | None,
    specialist: bool = True,
) -> list[str]:
    """The service, specialist and full-date lines every booking email shares."""
    lines = [
        texts["service"].format(value=resolve_translation(appointment.service.name, preferred))
    ]
    if specialist:
        lines.append(
            texts["specialist"].format(
                value=resolve_translation(appointment.specialist.name, preferred)
            )
        )
    lines.append(
        texts["when"].format(
            value=format_full_datetime_for_salon(
                appointment.start_datetime, appointment.salon.timezone, lang=lang_code
            )
        )
    )
    return lines


def _duration_line(appointment: Appointment, texts: dict[str, str], *, lang_code: str) -> str:
    minutes = int((appointment.end_datetime - appointment.start_datetime).total_seconds() // 60)
    return texts["duration"].format(value=format_duration(minutes, lang=lang_code))


def _footer_lines(
    salon: Salon, salon_name: str, texts: dict[str, str], *, last_prefix: str | None = "last"
) -> list[str]:
    """The salon name, the `<phone> · <address>` line (an empty contact left
    out, the line omitted when both are empty) and the last line, whose "at
    the number above" variant needs a phone. `last_prefix` picks the last
    line's pair of texts; None leaves the last line out (BOOKING_EXPIRED,
    PAYMENT_FAILED and LATE_PAYMENT_REFUNDED say nothing came of the booking,
    so there is nothing to report, and BOOKING_CREATED already carried the
    line; docs/DECISIONS.md "Notifications")."""
    lines = [salon_name]
    contacts = " · ".join(value for value in (salon.phone, salon.address) if value)
    if contacts:
        lines.append(contacts)
    if last_prefix is not None:
        variant = "with_phone" if salon.phone else "without_phone"
        lines.append(texts[f"{last_prefix}_{variant}"].format(salon=salon_name))
    return lines


def _build_status_email(
    notification: Notification,
    appointment: Appointment,
    *,
    lang_code: str,
    preferred: str | None,
) -> tuple[str, str]:
    """
    BOOKING_CREATED, BOOKING_CANCELLED, BOOKING_EXPIRED, PAYMENT_FAILED and
    LATE_PAYMENT_REFUNDED texts, as recorded in docs/DECISIONS.md "Notifications" ("Texts of the
    other booking emails"). Money values come from booking.amounts. The hold
    time is `hold_expires_at` in the salon's time zone. The cancellation
    money line is decided from `Payment.refund_due`, never from the Payment
    status: by the time this runs the refund may already be REFUND_PENDING
    or REFUNDED.
    """
    texts = _BOOKING_EMAIL_TEXTS[lang_code]
    salon = appointment.salon
    salon_name = resolve_translation(salon.name, preferred)
    tz = salon.timezone
    money_currency = amounts.currency(appointment)
    trigger = notification.trigger_type
    hold_time = format_time_for_salon(appointment.hold_expires_at, tz)
    last_prefix: str | None = "last"

    lines = [texts["greeting"].format(name=appointment.customer.name)]

    if trigger == NotificationTrigger.BOOKING_CREATED:
        # booking_link_mode is set by record_and_dispatch_notification at
        # record time (docs/DECISIONS.md "Notifications", the link bullet)
        # and read back here, not re-derived or inferred -- explicit checks,
        # no fall-through else, so a future third mode (or a bug that leaves
        # the field unset) fails loudly instead of silently becoming a guest
        # link.
        if notification.booking_link_mode == BookingLinkMode.GUEST:
            link_lines = [texts["created_guest_link"], _pay_link(appointment)]
        elif notification.booking_link_mode == BookingLinkMode.ACCOUNT:
            link_lines = [
                texts["created_account_link"],
                build_salon_frontend_url(salon.slug, "/client"),
            ]
        else:
            raise ValueError(
                "BOOKING_CREATED notification "
                f"{notification.pk!r} has booking_link_mode="
                f"{notification.booking_link_mode!r}, expected "
                f"{BookingLinkMode.GUEST!r} or {BookingLinkMode.ACCOUNT!r}"
            )
        subject_key = "created_subject"
        lines.append(texts["created_intro"])
        lines.extend(_detail_lines(appointment, texts, lang_code=lang_code, preferred=preferred))
        lines.append(_duration_line(appointment, texts, lang_code=lang_code))
        deposit = amounts.deposit_amount(appointment)
        if deposit is not None:
            lines.append(texts["deposit_due"].format(value=format_amount(deposit, money_currency)))
        lines.append(
            texts["amount_due"].format(
                value=format_amount(amounts.amount_due_after_deposit(appointment), money_currency)
            )
        )
        lines.append(texts["created_hold"].format(time=hold_time))
        lines.extend(link_lines)

    elif trigger == NotificationTrigger.BOOKING_CANCELLED:
        subject_key = "cancelled_subject"
        last_prefix = "cancelled_last"
        lines.append(texts["cancelled_intro"])
        lines.extend(_detail_lines(appointment, texts, lang_code=lang_code, preferred=preferred))
        payment = amounts.payment_of(appointment)
        deposit = amounts.deposit_amount(appointment)
        if payment is not None and deposit is not None:
            value = format_amount(deposit, money_currency)
            if payment.refund_due:
                lines.append(texts["refund_owed"].format(value=value))
            elif payment.status == PaymentStatus.SUCCEEDED:
                lines.append(texts["deposit_kept"].format(value=value))
        lines.append(texts["book_again"])
        lines.append(build_salon_frontend_url(salon.slug, "/services"))

    elif trigger == NotificationTrigger.BOOKING_EXPIRED:
        subject_key = "expired_subject"
        last_prefix = None
        lines.append(texts["expired_intro"])
        lines.extend(
            _detail_lines(
                appointment, texts, lang_code=lang_code, preferred=preferred, specialist=False
            )
        )
        lines.append(texts["book_again"])
        lines.append(build_salon_frontend_url(salon.slug, "/services"))

    elif trigger == NotificationTrigger.PAYMENT_FAILED:
        subject_key = "failed_subject"
        last_prefix = None
        lines.append(texts["failed_intro"])
        lines.extend(
            _detail_lines(
                appointment, texts, lang_code=lang_code, preferred=preferred, specialist=False
            )
        )
        deposit = amounts.deposit_amount(appointment)
        if deposit is not None:
            lines.append(texts["deposit_due"].format(value=format_amount(deposit, money_currency)))
        lines.append(texts["failed_hold"].format(time=hold_time))
        # Sent by the payment webhook, which does not know how the booking
        # was made: the guest token row is the record of a guest booking
        # (the same rule as _booking_email_link), linking to the pay page.
        if GuestAccessToken.objects.filter(appointment=appointment).exists():
            lines.append(_pay_link(appointment))
        else:
            lines.append(build_salon_frontend_url(salon.slug, "/client"))

    elif trigger == NotificationTrigger.LATE_PAYMENT_REFUNDED:
        # The refund is the Payment's own amount in its own currency, never
        # recomputed from the appointment snapshots: the full deposit that was
        # actually charged is what goes back. Recorded only by the webhook's
        # late-payment branch, which has just settled that Payment, so a
        # missing one is a bug, not a blank line.
        payment = amounts.payment_of(appointment)
        if payment is None:
            raise ValueError(
                f"LATE_PAYMENT_REFUNDED notification {notification.pk!r} has no payment "
                "to take the refund amount from"
            )
        subject_key = "late_subject"
        last_prefix = None
        lines.append(texts["late_intro"])
        lines.extend(
            _detail_lines(
                appointment, texts, lang_code=lang_code, preferred=preferred, specialist=False
            )
        )
        lines.append(
            texts["refund_amount"].format(value=format_amount(payment.amount, payment.currency))
        )
        lines.append(texts["late_refund_info"])
        lines.append(texts["late_book_again"])
        lines.append(build_salon_frontend_url(salon.slug, "/services"))

    else:
        raise ValueError(f"no status-email builder for trigger_type {trigger!r}")

    lines.extend(_footer_lines(salon, salon_name, texts, last_prefix=last_prefix))
    return texts[subject_key].format(salon=salon_name), "\n".join(lines)


def _build_booking_email(
    appointment: Appointment,
    *,
    reminder: bool,
    lang_code: str,
    preferred: str | None,
    now: dt.datetime,
) -> tuple[str, str]:
    """
    BOOKING_CONFIRMED (`reminder=False`) and APPOINTMENT_REMINDER texts, as
    recorded in docs/DECISIONS.md "Notifications". Money values come from
    booking.amounts (the same code as the guest and account serializers), the
    refund deadline from booking.services.refund_deadline through
    amounts.refund_deadline_or_none. Without a succeeded Payment the money
    lines and the deadline sentence are left out; this never raises for it,
    because a raising build fails the send task and the email is never sent.
    `now` is the build moment (send_notification's own `now`): once the
    refund deadline is past, the deadline sentence becomes "no refund".
    """
    texts = _BOOKING_EMAIL_TEXTS[lang_code]
    salon = appointment.salon
    salon_name = resolve_translation(salon.name, preferred)
    tz = salon.timezone
    money_currency = amounts.currency(appointment)
    paid = amounts.has_succeeded_payment(appointment)
    deposit = amounts.deposit_amount(appointment) if paid else None
    amount_due = amounts.amount_due_at_visit(appointment) if paid else None

    lines = [
        texts["greeting"].format(name=appointment.customer.name),
        texts["reminder_intro" if reminder else "confirmed_intro"],
        *_detail_lines(appointment, texts, lang_code=lang_code, preferred=preferred),
    ]
    if not reminder:
        lines.append(_duration_line(appointment, texts, lang_code=lang_code))
        if deposit is not None:
            lines.append(texts["deposit"].format(value=format_amount(deposit, money_currency)))
    if amount_due is not None:
        lines.append(texts["amount_due"].format(value=format_amount(amount_due, money_currency)))
    if not reminder:
        # The reminder never mentions the deadline: it arrives 24-25 hours
        # before the visit, when the deadline is about to pass.
        # A booking made less than 24 hours ahead must not promise a refund
        # until a moment that has already passed: decided at build time,
        # with the cancel flow's own comparison.
        deadline = amounts.refund_deadline_or_none(appointment)
        if deadline is not None and amounts.cancellation_refunds_at(appointment, now):
            lines.append(
                texts["deadline"].format(
                    deadline=format_deadline_for_salon(deadline, tz, lang=lang_code)
                )
            )
        elif deadline is not None:
            lines.append(texts["deadline_passed"])
    lines.append(texts["reminder_link" if reminder else "confirmed_link"])
    lines.append(_booking_email_link(appointment))
    lines.extend(_footer_lines(salon, salon_name, texts))

    if reminder:
        subject = texts["reminder_subject"].format(
            time=format_time_for_salon(appointment.start_datetime, tz), salon=salon_name
        )
    else:
        subject = texts["confirmed_subject"].format(salon=salon_name)
    return subject, "\n".join(lines)


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
        # of=("self",): customer is a nullable FK, so select_related joins it
        # as a LEFT OUTER JOIN, and Postgres refuses FOR UPDATE over the
        # nullable side of an outer join. Locking only the notification row is
        # also all we want — the recipient's row is read, never mutated here.
        notification = (
            Notification.objects.select_for_update(of=("self",))
            .select_related("customer")
            .get(salon=salon, pk=notification_id)
        )
        if notification.status != NotificationStatus.PENDING:
            return

        recipient = _resolve_recipient(notification)
        subject, body = _build_message(notification, now=now)
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
    booking_link_mode: str | None = None,
) -> None:
    """
    Record a PENDING Notification journal row for a trigger event and
    schedule its delivery for after the surrounding transaction commits
    (docs/DECISIONS.md § Stage 9 step (c)). Called from inside a caller's
    own atomic() block, right after a status save — currently
    PaymentWebhookView.post (payments/views.py) and
    booking.services.cancel_appointment.

    `booking_link_mode` is meaningful only for trigger_type=BOOKING_CREATED
    (docs/DECISIONS.md § Stage 15 planning, item 5, Cycle C) — the caller's
    explicit choice of which link the confirmation email carries, never
    inferred from whether the appointment's Customer has a linked Account.
    Validated up front, before any DB write, dedup lookup, or token
    derivation, so a caller that forgets it (or passes a typo'd value) fails
    loudly at the call site rather than silently defaulting to the guest
    link. The validated value is persisted on the row (not just checked and
    discarded) because the actual send happens later, in a Celery task that
    may run in a different process — _build_message reads it back off the
    row rather than the value handed to this call ever reaching it directly.
    Every other trigger_type ignores the parameter (it's stored as None,
    which _build_message never reads for a non-BOOKING_CREATED trigger).

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
    if trigger_type == NotificationTrigger.BOOKING_CREATED and booking_link_mode not in (
        BookingLinkMode.GUEST,
        BookingLinkMode.ACCOUNT,
    ):
        raise ValueError(
            "record_and_dispatch_notification: BOOKING_CREATED requires an "
            f"explicit booking_link_mode of {BookingLinkMode.GUEST!r} or "
            f"{BookingLinkMode.ACCOUNT!r}, got {booking_link_mode!r}"
        )

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
                booking_link_mode=booking_link_mode,
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
