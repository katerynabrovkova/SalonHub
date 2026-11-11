"""
Texts of the other booking emails: `BOOKING_CREATED` (guest and account),
`BOOKING_CANCELLED`, `BOOKING_EXPIRED` and `PAYMENT_FAILED`
(docs/DECISIONS.md "Notifications": "Texts of the other booking emails" and
the link bullet; register "In progress: booking emails", sub-step (a)).

- Subject and body compared whole with the recorded texts, lines joined by
  "\\n", with the formatting rules of `BOOKING_CONFIRMED` (full date, whole
  amounts without ".00", contact line and last line rules).
- The cancellation money line is decided from `Payment.refund_due`, never
  from the Payment status.
- The hold time is `hold_expires_at` in salon-local `HH:MM`.
- Links: `BOOKING_CREATED` by `booking_link_mode`; `PAYMENT_FAILED` by the
  guest token row (pay page or `/client`); `BOOKING_CANCELLED` and
  `BOOKING_EXPIRED` link to the salon's `/services` page only.

Messages are built with `_build_message` directly, like
test_notifications_booking_emails.py. `timezone.now` is frozen for every test.
"""

import datetime as dt
from decimal import Decimal

import pytest
from django.conf import settings
from django.utils import timezone

from accounts.models import Customer
from booking.guest_tokens import derive_guest_token, issue_guest_token
from booking.models import Appointment, AppointmentStatus
from catalog.models import Service, ServiceCategory
from core.tenancy import tenant_context
from notifications.models import Notification, NotificationTrigger
from notifications.services import _build_message
from payments.models import Payment, PaymentStatus
from specialists.models import Specialist
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

NOW = dt.datetime(2026, 11, 10, 9, 0, tzinfo=dt.UTC)
# Saturday 14 November 2026, 14:00 in Kyiv (EET, +02:00).
START = dt.datetime(2026, 11, 14, 12, 0, tzinfo=dt.UTC)
# 11:15 in Kyiv.
HOLD = NOW + dt.timedelta(minutes=15)

PHONE = "+380 44 123 4567"
ADDRESS = "вул. Хрещатик, 1, Київ"
SALON_EN = "Bella Demo Salon"
SALON_UK = "Белла Демо"

CREATED = NotificationTrigger.BOOKING_CREATED
CANCELLED = NotificationTrigger.BOOKING_CANCELLED
EXPIRED = NotificationTrigger.BOOKING_EXPIRED
FAILED = NotificationTrigger.PAYMENT_FAILED

UK_LAST_BOOKED = (
    f"Якщо ви не записувалися в {SALON_UK}, будь ласка, повідомте салон за номером вище."
)
EN_LAST_BOOKED = (
    f"If you did not book at {SALON_EN}, please let the salon know at the number above."
)
UK_LAST_CANCELLED = (
    "Якщо ви не скасовували цей запис, будь ласка, зверніться до салону за номером вище."
)
EN_LAST_CANCELLED = (
    "If you did not cancel this booking, please contact the salon at the number above."
)

UK_REFUND_OWED = (
    "Передоплату 24 UAH буде повернено на картку, з якої ви платили. "
    "Термін залежить від вашого банку."
)
EN_REFUND_OWED = (
    "The deposit of 24 UAH will be refunded to the card you paid with. "
    "How long it takes depends on your bank."
)
UK_DEPOSIT_KEPT = (
    "Запис скасовано менш ніж за 24 години до візиту, тому передоплата 24 UAH не повертається."
)
EN_DEPOSIT_KEPT = (
    "The booking was cancelled less than 24 hours before the visit, so the deposit of "
    "24 UAH is not refunded."
)


@pytest.fixture(autouse=True)
def _frozen_now(monkeypatch):
    monkeypatch.setattr(timezone, "now", lambda: NOW)


@pytest.fixture(autouse=True)
def _subdomain_frontend_url(settings):
    settings.FRONTEND_URL = "http://{slug}.testserver"


@pytest.fixture(autouse=True)
def _salon_identity(salon, service, specialist):
    """Bilingual names, a phone and an address on the shared fixtures, and a
    90-minute service so the duration has hours and minutes."""
    salon.name = {"en": SALON_EN, "uk": SALON_UK}
    salon.phone = PHONE
    salon.address = ADDRESS
    salon.save(update_fields=["name", "phone", "address"])
    with tenant_context(salon.id):
        service.name = {"en": "Manicure", "uk": "Манікюр"}
        service.duration_minutes = 90
        service.save(update_fields=["name", "duration_minutes"])
        specialist.name = {"en": "Jane", "uk": "Яна"}
        specialist.save(update_fields=["name"])


def _customer(salon, *, lang: str, email: str = "olena@example.com") -> Customer:
    name = "Олена" if lang == "uk" else "Olena"
    with tenant_context(salon.id):
        return Customer.objects.create(
            salon=salon, name=name, email=email, phone="+380000000001", preferred_language=lang
        )


def _appointment(
    salon,
    customer,
    specialist,
    service,
    *,
    status: str,
    guest: bool = True,
    payment_status: str | None = None,
    refund_due: bool = False,
    paid: str = "24.00",
    price: str = "120.00",
    hold: dt.datetime = HOLD,
) -> Appointment:
    """120.00 at the salon's default 20% deposit: deposit 24, 96 at the
    visit. `guest=True` issues the guest token row a guest booking has."""
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=status,
        hold_expires_at=hold,
    )
    with tenant_context(salon.id):
        Appointment.objects.filter(pk=appointment.pk).update(
            service_price_at_booking=Decimal(price)
        )
        appointment.refresh_from_db()
        if payment_status is not None:
            Payment.objects.create(
                salon=salon,
                appointment=appointment,
                amount=Decimal(paid),
                currency=salon.currency,
                status=payment_status,
                refund_due=refund_due,
                provider_reference_id=f"ref-{appointment.id}",
            )
        if guest:
            issue_guest_token(appointment)
    return appointment


def _build(salon, appointment, trigger, *, booking_link_mode=None) -> tuple[str, str]:
    with tenant_context(salon.id):
        notification = Notification.objects.create(
            salon=salon,
            customer=appointment.customer,
            appointment=appointment,
            trigger_type=trigger,
            channel="email",
            dedup_key=f"{trigger}:{appointment.id}",
            booking_link_mode=booking_link_mode,
        )
        return _build_message(notification, now=timezone.now())


def _base(salon) -> str:
    return settings.FRONTEND_URL.format(slug=salon.slug)


def _pay_link(salon, appointment) -> str:
    return (
        f"{_base(salon)}/booking/pay"
        f"#appointment_id={appointment.id}&token={derive_guest_token(appointment.id)}"
    )


def _client_link(salon) -> str:
    return f"{_base(salon)}/client"


def _services_link(salon) -> str:
    return f"{_base(salon)}/services"


def _lines(*lines: str) -> str:
    return "\n".join(lines)


def _uk_footer() -> list[str]:
    return [SALON_UK, f"{PHONE} · {ADDRESS}"]


def _en_footer() -> list[str]:
    return [SALON_EN, f"{PHONE} · {ADDRESS}"]


# --- BOOKING_CREATED -----------------------------------------------------------------


def test_booking_created_guest_in_ukrainian(salon, specialist, service):
    customer = _customer(salon, lang="uk")
    appointment = _appointment(
        salon, customer, specialist, service, status=AppointmentStatus.PENDING_PAYMENT
    )

    subject, body = _build(salon, appointment, CREATED, booking_link_mode="guest")

    assert subject == f"Ваш запис у {SALON_UK} створено, залишилось оплатити"
    assert body == _lines(
        "Вітаємо, Олена!",
        "Ваш запис створено:",
        "Послуга: Манікюр",
        "Майстер: Яна",
        "Коли: субота, 14 листопада 2026, 14:00",
        "Тривалість: 1 год 30 хв",
        "Передоплата: 24 UAH",
        "Оплата при візиті: 96 UAH",
        "Ми тримаємо цей час для вас до 11:15. Якщо не встигнете оплатити, запис скасується "
        "автоматично.",
        "Оплатити передоплату:",
        _pay_link(salon, appointment),
        *_uk_footer(),
        UK_LAST_BOOKED,
    )


def test_booking_created_guest_in_english(salon, specialist, service):
    customer = _customer(salon, lang="en")
    appointment = _appointment(
        salon, customer, specialist, service, status=AppointmentStatus.PENDING_PAYMENT
    )

    subject, body = _build(salon, appointment, CREATED, booking_link_mode="guest")

    assert subject == f"Your booking at {SALON_EN} is created, the deposit is still to be paid"
    assert body == _lines(
        "Hello, Olena!",
        "Your booking is created:",
        "Service: Manicure",
        "Specialist: Jane",
        "When: Saturday, 14 November 2026, 14:00",
        "Duration: 1 h 30 min",
        "Deposit: 24 UAH",
        "Pay at the visit: 96 UAH",
        "We are holding this time for you until 11:15. If you do not pay in time, the booking "
        "will be cancelled automatically.",
        "Pay the deposit:",
        _pay_link(salon, appointment),
        *_en_footer(),
        EN_LAST_BOOKED,
    )


def test_booking_created_account_in_ukrainian(salon, specialist, service):
    customer = _customer(salon, lang="uk")
    appointment = _appointment(
        salon, customer, specialist, service, status=AppointmentStatus.PENDING_PAYMENT, guest=False
    )

    subject, body = _build(salon, appointment, CREATED, booking_link_mode="account")

    assert subject == f"Ваш запис у {SALON_UK} створено, залишилось оплатити"
    assert body == _lines(
        "Вітаємо, Олена!",
        "Ваш запис створено:",
        "Послуга: Манікюр",
        "Майстер: Яна",
        "Коли: субота, 14 листопада 2026, 14:00",
        "Тривалість: 1 год 30 хв",
        "Передоплата: 24 UAH",
        "Оплата при візиті: 96 UAH",
        "Ми тримаємо цей час для вас до 11:15. Якщо не встигнете оплатити, запис скасується "
        "автоматично.",
        "Оплатити передоплату можна в особистому кабінеті:",
        _client_link(salon),
        *_uk_footer(),
        UK_LAST_BOOKED,
    )


def test_booking_created_account_in_english(salon, specialist, service):
    customer = _customer(salon, lang="en")
    appointment = _appointment(
        salon, customer, specialist, service, status=AppointmentStatus.PENDING_PAYMENT, guest=False
    )

    subject, body = _build(salon, appointment, CREATED, booking_link_mode="account")

    assert subject == f"Your booking at {SALON_EN} is created, the deposit is still to be paid"
    assert body == _lines(
        "Hello, Olena!",
        "Your booking is created:",
        "Service: Manicure",
        "Specialist: Jane",
        "When: Saturday, 14 November 2026, 14:00",
        "Duration: 1 h 30 min",
        "Deposit: 24 UAH",
        "Pay at the visit: 96 UAH",
        "We are holding this time for you until 11:15. If you do not pay in time, the booking "
        "will be cancelled automatically.",
        "You can pay the deposit in your account:",
        _client_link(salon),
        *_en_footer(),
        EN_LAST_BOOKED,
    )


# --- BOOKING_CANCELLED ---------------------------------------------------------------


def _cancelled_uk(salon, money_line: list[str]) -> str:
    return _lines(
        "Вітаємо, Олена!",
        "Ваш запис скасовано:",
        "Послуга: Манікюр",
        "Майстер: Яна",
        "Коли: субота, 14 листопада 2026, 14:00",
        *money_line,
        "Записатися знову:",
        _services_link(salon),
        *_uk_footer(),
        UK_LAST_CANCELLED,
    )


def _cancelled_en(salon, money_line: list[str]) -> str:
    return _lines(
        "Hello, Olena!",
        "Your booking is cancelled:",
        "Service: Manicure",
        "Specialist: Jane",
        "When: Saturday, 14 November 2026, 14:00",
        *money_line,
        "Book again:",
        _services_link(salon),
        *_en_footer(),
        EN_LAST_CANCELLED,
    )


def test_booking_cancelled_refund_owed_in_ukrainian(salon, specialist, service):
    customer = _customer(salon, lang="uk")
    appointment = _appointment(
        salon,
        customer,
        specialist,
        service,
        status=AppointmentStatus.CANCELLED,
        payment_status=PaymentStatus.SUCCEEDED,
        refund_due=True,
    )

    subject, body = _build(salon, appointment, CANCELLED)

    assert subject == f"Ваш запис у {SALON_UK} скасовано"
    assert body == _cancelled_uk(salon, [UK_REFUND_OWED])


def test_booking_cancelled_refund_owed_in_english(salon, specialist, service):
    customer = _customer(salon, lang="en")
    appointment = _appointment(
        salon,
        customer,
        specialist,
        service,
        status=AppointmentStatus.CANCELLED,
        payment_status=PaymentStatus.SUCCEEDED,
        refund_due=True,
    )

    subject, body = _build(salon, appointment, CANCELLED)

    assert subject == f"Your booking at {SALON_EN} is cancelled"
    assert body == _cancelled_en(salon, [EN_REFUND_OWED])


def test_booking_cancelled_deposit_kept_in_ukrainian(salon, specialist, service):
    customer = _customer(salon, lang="uk")
    appointment = _appointment(
        salon,
        customer,
        specialist,
        service,
        status=AppointmentStatus.CANCELLED,
        payment_status=PaymentStatus.SUCCEEDED,
        refund_due=False,
    )

    subject, body = _build(salon, appointment, CANCELLED)

    assert subject == f"Ваш запис у {SALON_UK} скасовано"
    assert body == _cancelled_uk(salon, [UK_DEPOSIT_KEPT])


def test_booking_cancelled_deposit_kept_in_english(salon, specialist, service):
    customer = _customer(salon, lang="en")
    appointment = _appointment(
        salon,
        customer,
        specialist,
        service,
        status=AppointmentStatus.CANCELLED,
        payment_status=PaymentStatus.SUCCEEDED,
        refund_due=False,
    )

    subject, body = _build(salon, appointment, CANCELLED)

    assert subject == f"Your booking at {SALON_EN} is cancelled"
    assert body == _cancelled_en(salon, [EN_DEPOSIT_KEPT])


@pytest.mark.parametrize(
    "payment_status",
    [None, PaymentStatus.PENDING, PaymentStatus.FAILED],
    ids=["no_payment", "pending", "failed"],
)
def test_booking_cancelled_nothing_paid_in_ukrainian_has_no_money_line(
    salon, specialist, service, payment_status
):
    customer = _customer(salon, lang="uk")
    appointment = _appointment(
        salon,
        customer,
        specialist,
        service,
        status=AppointmentStatus.CANCELLED,
        payment_status=payment_status,
    )

    subject, body = _build(salon, appointment, CANCELLED)

    assert subject == f"Ваш запис у {SALON_UK} скасовано"
    assert body == _cancelled_uk(salon, [])


def test_booking_cancelled_nothing_paid_in_english_has_no_money_line(salon, specialist, service):
    customer = _customer(salon, lang="en")
    appointment = _appointment(
        salon, customer, specialist, service, status=AppointmentStatus.CANCELLED
    )

    subject, body = _build(salon, appointment, CANCELLED)

    assert subject == f"Your booking at {SALON_EN} is cancelled"
    assert body == _cancelled_en(salon, [])


@pytest.mark.parametrize(
    ("payment_status", "lang", "expected", "not_expected"),
    [
        (PaymentStatus.REFUNDED, "uk", UK_REFUND_OWED, UK_DEPOSIT_KEPT),
        (PaymentStatus.REFUNDED, "en", EN_REFUND_OWED, EN_DEPOSIT_KEPT),
        (PaymentStatus.REFUND_PENDING, "uk", UK_REFUND_OWED, UK_DEPOSIT_KEPT),
    ],
    ids=["refunded_uk", "refunded_en", "refund_pending_uk"],
)
def test_booking_cancelled_money_line_reads_refund_due_whatever_the_payment_status(
    salon, specialist, service, payment_status, lang, expected, not_expected
):
    """The email is built after the cancel commits, by which time the refund
    may already be REFUND_PENDING or REFUNDED: `refund_due` decides the line,
    so a refunded deposit is still "refund on the way", never "nothing paid"."""
    customer = _customer(salon, lang=lang)
    appointment = _appointment(
        salon,
        customer,
        specialist,
        service,
        status=AppointmentStatus.CANCELLED,
        payment_status=payment_status,
        refund_due=True,
    )

    _subject, body = _build(salon, appointment, CANCELLED)

    lines = body.splitlines()
    assert expected in lines
    assert not_expected not in lines


def _set_contacts(salon, *, phone: str, address: str) -> None:
    salon.phone = phone
    salon.address = address
    salon.save(update_fields=["phone", "address"])


@pytest.mark.parametrize(
    ("lang", "salon_name", "last_line"),
    [
        (
            "uk",
            SALON_UK,
            "Якщо ви не скасовували цей запис, будь ласка, зверніться до салону.",
        ),
        ("en", SALON_EN, "If you did not cancel this booking, please contact the salon."),
    ],
    ids=["uk", "en"],
)
def test_booking_cancelled_without_phone_has_its_own_short_last_line(
    salon, specialist, service, lang, salon_name, last_line
):
    _set_contacts(salon, phone="", address=ADDRESS)
    customer = _customer(salon, lang=lang)
    appointment = _appointment(
        salon, customer, specialist, service, status=AppointmentStatus.CANCELLED
    )

    _subject, body = _build(salon, appointment, CANCELLED)

    assert body.splitlines()[-3:] == [salon_name, ADDRESS, last_line]


def test_booking_cancelled_without_phone_or_address_has_no_contact_line(salon, specialist, service):
    _set_contacts(salon, phone="", address="")
    customer = _customer(salon, lang="uk")
    appointment = _appointment(
        salon, customer, specialist, service, status=AppointmentStatus.CANCELLED
    )

    _subject, body = _build(salon, appointment, CANCELLED)

    assert body.splitlines()[-3:] == [
        _services_link(salon),
        SALON_UK,
        "Якщо ви не скасовували цей запис, будь ласка, зверніться до салону.",
    ]
    assert "·" not in body


# --- BOOKING_EXPIRED -----------------------------------------------------------------


@pytest.mark.parametrize(
    "payment_status", [None, PaymentStatus.PENDING], ids=["no_payment", "pending"]
)
def test_booking_expired_in_ukrainian(salon, specialist, service, payment_status):
    customer = _customer(salon, lang="uk")
    appointment = _appointment(
        salon,
        customer,
        specialist,
        service,
        status=AppointmentStatus.EXPIRED,
        payment_status=payment_status,
    )

    subject, body = _build(salon, appointment, EXPIRED)

    assert subject == f"Запис у {SALON_UK} не підтверджено"
    assert body == _lines(
        "Вітаємо, Олена!",
        "Час на оплату минув, тому запис не підтверджено і цей час звільнено:",
        "Послуга: Манікюр",
        "Коли: субота, 14 листопада 2026, 14:00",
        "Записатися знову:",
        _services_link(salon),
        *_uk_footer(),
    )


def test_booking_expired_in_english(salon, specialist, service):
    customer = _customer(salon, lang="en")
    appointment = _appointment(
        salon, customer, specialist, service, status=AppointmentStatus.EXPIRED
    )

    subject, body = _build(salon, appointment, EXPIRED)

    assert subject == f"Your booking at {SALON_EN} was not confirmed"
    assert body == _lines(
        "Hello, Olena!",
        "The time to pay has run out, so the booking was not confirmed and this time has been "
        "released:",
        "Service: Manicure",
        "When: Saturday, 14 November 2026, 14:00",
        "Book again:",
        _services_link(salon),
        *_en_footer(),
    )


@pytest.mark.parametrize(
    ("trigger", "status", "payment_status"),
    [
        (EXPIRED, AppointmentStatus.EXPIRED, None),
        (FAILED, AppointmentStatus.PENDING_PAYMENT, PaymentStatus.FAILED),
    ],
    ids=["expired", "payment_failed"],
)
@pytest.mark.parametrize(
    ("lang", "salon_name", "phone", "address", "expected_tail"),
    [
        ("uk", SALON_UK, "", ADDRESS, [SALON_UK, ADDRESS]),
        ("en", SALON_EN, "", ADDRESS, [SALON_EN, ADDRESS]),
        ("uk", SALON_UK, "", "", [SALON_UK]),
        ("en", SALON_EN, PHONE, "", [SALON_EN, PHONE]),
    ],
    ids=["no_phone_uk", "no_phone_en", "no_contacts_uk", "phone_only_en"],
)
def test_nothing_came_of_the_booking_emails_have_no_last_line_with_or_without_phone(
    salon,
    specialist,
    service,
    trigger,
    status,
    payment_status,
    lang,
    salon_name,
    phone,
    address,
    expected_tail,
):
    """BOOKING_EXPIRED and PAYMENT_FAILED say nothing came of the booking:
    the customer has nothing to report and BOOKING_CREATED already carried
    the "did not book" line, so the email ends with the salon footer
    (docs/DECISIONS.md "Notifications", texts of the other booking emails)."""
    _set_contacts(salon, phone=phone, address=address)
    customer = _customer(salon, lang=lang)
    appointment = _appointment(
        salon, customer, specialist, service, status=status, payment_status=payment_status
    )
    link = _services_link(salon) if trigger == EXPIRED else _pay_link(salon, appointment)

    _subject, body = _build(salon, appointment, trigger)

    lines = body.splitlines()
    assert lines[-(len(expected_tail) + 1) :] == [link, *expected_tail]
    assert "записувалися" not in body
    assert "did not book" not in body
    assert salon_name in lines


@pytest.mark.parametrize(
    ("trigger", "status"),
    [(CANCELLED, AppointmentStatus.CANCELLED), (EXPIRED, AppointmentStatus.EXPIRED)],
    ids=["cancelled", "expired"],
)
def test_cancelled_and_expired_emails_of_a_guest_booking_carry_no_token(
    salon, specialist, service, trigger, status
):
    """Only the salon's /services page, the same for guest and account
    bookings: a guest booking's token never appears."""
    customer = _customer(salon, lang="en")
    appointment = _appointment(salon, customer, specialist, service, status=status, guest=True)

    _subject, body = _build(salon, appointment, trigger)

    assert _services_link(salon) in body.splitlines()
    assert derive_guest_token(appointment.id) not in body
    assert "/client" not in body
    assert "/booking/pay" not in body
    assert "/manage" not in body


# --- PAYMENT_FAILED ------------------------------------------------------------------


def _failed_uk(link: str) -> str:
    return _lines(
        "Вітаємо, Олена!",
        "Не вдалося провести передоплату за запис:",
        "Послуга: Манікюр",
        "Коли: субота, 14 листопада 2026, 14:00",
        "Передоплата: 24 UAH",
        "Ми ще тримаємо цей час для вас до 11:15. Спробувати оплатити ще раз:",
        link,
        *_uk_footer(),
    )


def _failed_en(link: str) -> str:
    return _lines(
        "Hello, Olena!",
        "We could not process the deposit payment for your booking:",
        "Service: Manicure",
        "When: Saturday, 14 November 2026, 14:00",
        "Deposit: 24 UAH",
        "We are still holding this time for you until 11:15. Try to pay again:",
        link,
        *_en_footer(),
    )


def _failed_appointment(salon, customer, specialist, service, *, guest: bool) -> Appointment:
    return _appointment(
        salon,
        customer,
        specialist,
        service,
        status=AppointmentStatus.PENDING_PAYMENT,
        payment_status=PaymentStatus.FAILED,
        guest=guest,
    )


def test_payment_failed_guest_in_ukrainian_links_to_the_pay_page(salon, specialist, service):
    customer = _customer(salon, lang="uk")
    appointment = _failed_appointment(salon, customer, specialist, service, guest=True)

    subject, body = _build(salon, appointment, FAILED)

    assert subject == f"Оплата в {SALON_UK} не пройшла"
    assert body == _failed_uk(_pay_link(salon, appointment))


def test_payment_failed_guest_in_english_links_to_the_pay_page(salon, specialist, service):
    customer = _customer(salon, lang="en")
    appointment = _failed_appointment(salon, customer, specialist, service, guest=True)

    subject, body = _build(salon, appointment, FAILED)

    assert subject == f"Your payment at {SALON_EN} did not go through"
    assert body == _failed_en(_pay_link(salon, appointment))


def test_payment_failed_account_in_ukrainian_links_to_client(salon, specialist, service):
    customer = _customer(salon, lang="uk")
    appointment = _failed_appointment(salon, customer, specialist, service, guest=False)

    subject, body = _build(salon, appointment, FAILED)

    assert subject == f"Оплата в {SALON_UK} не пройшла"
    assert body == _failed_uk(_client_link(salon))
    assert derive_guest_token(appointment.id) not in body


def test_payment_failed_account_in_english_links_to_client(salon, specialist, service):
    customer = _customer(salon, lang="en")
    appointment = _failed_appointment(salon, customer, specialist, service, guest=False)

    subject, body = _build(salon, appointment, FAILED)

    assert subject == f"Your payment at {SALON_EN} did not go through"
    assert body == _failed_en(_client_link(salon))
    assert derive_guest_token(appointment.id) not in body


# --- Hold time is salon-local --------------------------------------------------------

# 22:50 UTC on Tuesday 10 November is 00:50 on Wednesday 11 November in Kyiv.
_HOLD_AFTER_LOCAL_MIDNIGHT = dt.datetime(2026, 11, 10, 22, 50, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    ("trigger", "payment_status", "booking_link_mode", "expected"),
    [
        (
            CREATED,
            None,
            "guest",
            "Ми тримаємо цей час для вас до 00:50. Якщо не встигнете оплатити, запис скасується "
            "автоматично.",
        ),
        (
            FAILED,
            PaymentStatus.FAILED,
            None,
            "Ми ще тримаємо цей час для вас до 00:50. Спробувати оплатити ще раз:",
        ),
    ],
    ids=["created", "payment_failed"],
)
def test_hold_time_is_shown_in_the_salon_time_zone(
    monkeypatch, salon, specialist, service, trigger, payment_status, booking_link_mode, expected
):
    monkeypatch.setattr(
        timezone, "now", lambda: _HOLD_AFTER_LOCAL_MIDNIGHT - dt.timedelta(minutes=15)
    )
    customer = _customer(salon, lang="uk")
    appointment = _appointment(
        salon,
        customer,
        specialist,
        service,
        status=AppointmentStatus.PENDING_PAYMENT,
        payment_status=payment_status,
        hold=_HOLD_AFTER_LOCAL_MIDNIGHT,
    )

    _subject, body = _build(salon, appointment, trigger, booking_link_mode=booking_link_mode)

    assert expected in body.splitlines()
    assert "22:50" not in body


# --- Two salons ----------------------------------------------------------------------


def _salon_b_booking(other_salon, *, status, payment_status, refund_due=False):
    other_salon.name = {"en": "Studio B", "uk": "Студія Б"}
    other_salon.phone = "+995 32 200 0000"
    other_salon.address = "Rustaveli Ave 1, Tbilisi"
    other_salon.currency = "EUR"
    other_salon.save(update_fields=["name", "phone", "address", "currency"])
    with tenant_context(other_salon.id):
        category = ServiceCategory.objects.create(salon=other_salon, name={"en": "Hair"})
        b_service = Service.objects.create(
            salon=other_salon,
            category=category,
            name={"en": "Cut", "uk": "Стрижка"},
            duration_minutes=45,
            price="200.00",
            buffer_minutes=0,
        )
        b_specialist = Specialist.objects.create(salon=other_salon, name={"en": "Sam"})
    b_customer = _customer(other_salon, lang="uk", email="nino@example.com")
    # 200.00 at 20%: deposit 40, 160 at the visit.
    return _appointment(
        other_salon,
        b_customer,
        b_specialist,
        b_service,
        status=status,
        payment_status=payment_status,
        refund_due=refund_due,
        paid="40.00",
        price="200.00",
    )


@pytest.mark.parametrize(
    ("trigger", "status", "payment_status", "refund_due", "booking_link_mode", "money"),
    [
        (CREATED, AppointmentStatus.PENDING_PAYMENT, None, False, "guest", "40 EUR"),
        (CANCELLED, AppointmentStatus.CANCELLED, PaymentStatus.SUCCEEDED, True, None, "40 EUR"),
        (EXPIRED, AppointmentStatus.EXPIRED, None, False, None, None),
        (
            FAILED,
            AppointmentStatus.PENDING_PAYMENT,
            PaymentStatus.FAILED,
            False,
            None,
            "40 EUR",
        ),
    ],
    ids=["created", "cancelled", "expired", "payment_failed"],
)
def test_email_for_salon_b_carries_only_salon_b_data(
    salon,
    other_salon,
    specialist,
    service,
    trigger,
    status,
    payment_status,
    refund_due,
    booking_link_mode,
    money,
):
    # Salon A has a booking in the same state, so B's email has a real
    # neighbour to leak from.
    a_customer = _customer(salon, lang="uk")
    _appointment(
        salon,
        a_customer,
        specialist,
        service,
        status=status,
        payment_status=payment_status,
        refund_due=refund_due,
    )
    b_appointment = _salon_b_booking(
        other_salon, status=status, payment_status=payment_status, refund_due=refund_due
    )

    subject, body = _build(other_salon, b_appointment, trigger, booking_link_mode=booking_link_mode)

    assert "Студія Б" in subject
    lines = body.splitlines()
    assert "Студія Б" in lines
    assert "+995 32 200 0000 · Rustaveli Ave 1, Tbilisi" in lines
    assert "Послуга: Стрижка" in lines
    assert "http://other-salon.testserver/" in body
    if money is not None:
        assert money in body
    for leaked in (SALON_UK, SALON_EN, PHONE, ADDRESS, "UAH", "Манікюр", "bella-demo"):
        assert leaked not in subject
        assert leaked not in body
