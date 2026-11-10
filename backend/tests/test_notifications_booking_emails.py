"""
`BOOKING_CONFIRMED` and `APPOINTMENT_REMINDER` emails: link choice and texts
(docs/DECISIONS.md "Notifications": the link bullet, the texts and footer
rules; "Booking > Guest token": links in emails; register "In progress: guest
manage page", sub-step (b)).

- Link: an appointment with a guest token row links to the manage page,
  `/appointments/<id>/manage#token=<token>`; one without links to `/client`.
  Never the old path form `/manage/<token>/`.
- Texts: subject and body compared whole with the recorded texts, lines
  joined by "\\n" exactly as recorded. Formatting choices decided
  10.11.2026: whole amounts without ".00" ("24 UAH"), others with cents
  ("50.50 EUR"); the day of the month without a leading zero; the weekday of
  the Ukrainian refund-deadline sentence in the genitive ("п'ятниці").

Messages are built with `_build_message` directly, like
test_notifications_send_service.py. `timezone.now` is frozen for every test.
"""

import datetime as dt
from decimal import Decimal

import pytest
from django.conf import settings
from django.utils import timezone

from accounts.models import Account, AccountRole, Customer
from booking.guest_tokens import derive_guest_token, issue_guest_token
from booking.models import Appointment
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

PHONE = "+380 44 123 4567"
ADDRESS = "вул. Хрещатик, 1, Київ"
SALON_EN = "Bella Demo Salon"
SALON_UK = "Белла Демо"

CONFIRMED = NotificationTrigger.BOOKING_CONFIRMED
REMINDER = NotificationTrigger.APPOINTMENT_REMINDER


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


def _customer(salon, *, name: str, lang: str, email: str = "olena@example.com") -> Customer:
    with tenant_context(salon.id):
        return Customer.objects.create(
            salon=salon, name=name, email=email, phone="+380000000001", preferred_language=lang
        )


def _booking(
    salon,
    customer,
    specialist,
    service,
    *,
    guest: bool = True,
    start: dt.datetime = START,
    price: str = "120.00",
    paid: str = "24.00",
) -> Appointment:
    """A confirmed appointment with a succeeded deposit. `guest=True` issues
    the guest token row a guest booking has; an account booking has none."""
    appointment = make_appointment(
        salon=salon, customer=customer, specialist=specialist, service=service, start=start
    )
    with tenant_context(salon.id):
        Appointment.objects.filter(pk=appointment.pk).update(
            service_price_at_booking=Decimal(price)
        )
        appointment.refresh_from_db()
        Payment.objects.create(
            salon=salon,
            appointment=appointment,
            amount=Decimal(paid),
            currency=salon.currency,
            status=PaymentStatus.SUCCEEDED,
            provider_reference_id=f"ref-{appointment.id}",
        )
        if guest:
            issue_guest_token(appointment)
    return appointment


def _build(salon, appointment, trigger) -> tuple[str, str]:
    with tenant_context(salon.id):
        notification = Notification.objects.create(
            salon=salon,
            customer=appointment.customer,
            appointment=appointment,
            trigger_type=trigger,
            channel="email",
            dedup_key=f"{trigger}:{appointment.id}",
        )
        # The build moment is the frozen clock (each test may freeze its own).
        return _build_message(notification, now=timezone.now())


def _manage_link(salon, appointment) -> str:
    base = settings.FRONTEND_URL.format(slug=salon.slug)
    return f"{base}/appointments/{appointment.id}/manage#token={derive_guest_token(appointment.id)}"


def _client_link(salon) -> str:
    return f"{settings.FRONTEND_URL.format(slug=salon.slug)}/client"


def _lines(*lines: str) -> str:
    return "\n".join(lines)


# --- 4-7. Which link -----------------------------------------------------------------


def _assert_manage_link(salon, specialist, service, trigger) -> None:
    customer = _customer(salon, name="Olena", lang="en")
    appointment = _booking(salon, customer, specialist, service, guest=True)

    _subject, body = _build(salon, appointment, trigger)

    assert _manage_link(salon, appointment) in body.splitlines()
    assert "/client" not in body


def _assert_client_link(salon, specialist, service, trigger) -> None:
    """No guest token row, so the link is /client and no token appears anywhere."""
    customer = _customer(salon, name="Olena", lang="en")
    appointment = _booking(salon, customer, specialist, service, guest=False)

    _subject, body = _build(salon, appointment, trigger)

    assert _client_link(salon) in body.splitlines()
    assert "/manage" not in body
    assert derive_guest_token(appointment.id) not in body


def test_booking_confirmed_for_a_guest_booking_links_to_the_manage_page(salon, specialist, service):
    _assert_manage_link(salon, specialist, service, CONFIRMED)


def test_booking_confirmed_for_an_account_booking_links_to_client(salon, specialist, service):
    _assert_client_link(salon, specialist, service, CONFIRMED)


def test_appointment_reminder_for_a_guest_booking_links_to_the_manage_page(
    salon, specialist, service
):
    _assert_manage_link(salon, specialist, service, REMINDER)


def test_appointment_reminder_for_an_account_booking_links_to_client(salon, specialist, service):
    _assert_client_link(salon, specialist, service, REMINDER)


@pytest.mark.parametrize("trigger", [CONFIRMED, REMINDER], ids=["confirmed", "reminder"])
def test_guest_booking_still_links_to_the_manage_page_after_the_customer_gets_an_account(
    salon, specialist, service, trigger
):
    """The link follows the guest token row, never whether the Customer has
    an Account (docs/DECISIONS.md "Notifications"): a guest booking whose
    Customer later registers and links an Account keeps its manage link."""
    customer = _customer(salon, name="Olena", lang="en")
    appointment = _booking(salon, customer, specialist, service, guest=True)
    with tenant_context(salon.id):
        Account.objects.create_account(
            salon=salon,
            email=customer.email,
            password="a-strong-passw0rd!",
            role=AccountRole.CLIENT,
            customer=customer,
        )

    _subject, body = _build(salon, appointment, trigger)

    assert _manage_link(salon, appointment) in body.splitlines()
    assert "/client" not in body


# --- 8. Old path form never appears --------------------------------------------------


@pytest.mark.parametrize("trigger", [CONFIRMED, REMINDER], ids=["confirmed", "reminder"])
def test_no_email_contains_the_old_path_form_of_the_manage_link(
    salon, specialist, service, trigger
):
    customer = _customer(salon, name="Olena", lang="en")
    appointment = _booking(salon, customer, specialist, service, guest=True)
    token = derive_guest_token(appointment.id)

    _subject, body = _build(salon, appointment, trigger)

    assert f"/manage/{token}" not in body
    assert "/manage/" not in body


# --- 9-12. Whole texts ---------------------------------------------------------------


def test_booking_confirmed_in_ukrainian(salon, specialist, service):
    customer = _customer(salon, name="Олена", lang="uk")
    appointment = _booking(salon, customer, specialist, service)

    subject, body = _build(salon, appointment, CONFIRMED)

    assert subject == f"Ваш запис у {SALON_UK} підтверджено"
    assert body == _lines(
        "Вітаємо, Олена!",
        "Ваш запис підтверджено:",
        "Послуга: Манікюр",
        "Майстер: Яна",
        "Коли: субота, 14 листопада 2026, 14:00",
        "Тривалість: 1 год 30 хв",
        "Передоплата: 24 UAH (сплачено)",
        "Оплата при візиті: 96 UAH",
        "Скасувати запис з поверненням передоплати можна не пізніше ніж за 24 години до "
        "візиту, тобто до 14:00 п'ятниці, 13 листопада. Пізніше передоплата не повертається.",
        "Переглянути або скасувати запис:",
        _manage_link(salon, appointment),
        SALON_UK,
        f"{PHONE} · {ADDRESS}",
        f"Якщо ви не записувалися в {SALON_UK}, будь ласка, повідомте салон за номером вище.",
    )


def test_booking_confirmed_in_english(salon, specialist, service):
    customer = _customer(salon, name="Olena", lang="en")
    appointment = _booking(salon, customer, specialist, service)

    subject, body = _build(salon, appointment, CONFIRMED)

    assert subject == f"Your booking at {SALON_EN} is confirmed"
    assert body == _lines(
        "Hello, Olena!",
        "Your booking is confirmed:",
        "Service: Manicure",
        "Specialist: Jane",
        "When: Saturday, 14 November 2026, 14:00",
        "Duration: 1 h 30 min",
        "Deposit: 24 UAH (paid)",
        "Pay at the visit: 96 UAH",
        "You can cancel with a refund of the deposit no later than 24 hours before the "
        "visit, that is until 14:00 on Friday, 13 November. After that the deposit is not "
        "refunded.",
        "View or cancel your booking:",
        _manage_link(salon, appointment),
        SALON_EN,
        f"{PHONE} · {ADDRESS}",
        f"If you did not book at {SALON_EN}, please let the salon know at the number above.",
    )


def test_appointment_reminder_in_ukrainian(salon, specialist, service):
    customer = _customer(salon, name="Олена", lang="uk")
    appointment = _booking(salon, customer, specialist, service)

    subject, body = _build(salon, appointment, REMINDER)

    assert subject == f"Нагадування: завтра о 14:00 у {SALON_UK}"
    assert body == _lines(
        "Вітаємо, Олена!",
        "Нагадуємо про ваш запис завтра:",
        "Послуга: Манікюр",
        "Майстер: Яна",
        "Коли: субота, 14 листопада 2026, 14:00",
        "Оплата при візиті: 96 UAH",
        "Якщо плани змінились, скасувати запис можна за посиланням:",
        _manage_link(salon, appointment),
        SALON_UK,
        f"{PHONE} · {ADDRESS}",
        f"Якщо ви не записувалися в {SALON_UK}, будь ласка, повідомте салон за номером вище.",
    )


def test_appointment_reminder_in_english(salon, specialist, service):
    customer = _customer(salon, name="Olena", lang="en")
    appointment = _booking(salon, customer, specialist, service)

    subject, body = _build(salon, appointment, REMINDER)

    assert subject == f"Reminder: tomorrow at 14:00 at {SALON_EN}"
    assert body == _lines(
        "Hello, Olena!",
        "This is a reminder of your booking tomorrow:",
        "Service: Manicure",
        "Specialist: Jane",
        "When: Saturday, 14 November 2026, 14:00",
        "Pay at the visit: 96 UAH",
        "If your plans have changed, you can cancel your booking here:",
        _manage_link(salon, appointment),
        SALON_EN,
        f"{PHONE} · {ADDRESS}",
        f"If you did not book at {SALON_EN}, please let the salon know at the number above.",
    )


# --- 13-15. Footer variants ----------------------------------------------------------


def _set_contacts(salon, *, phone: str, address: str) -> None:
    salon.phone = phone
    salon.address = address
    salon.save(update_fields=["phone", "address"])


def test_salon_without_phone_shows_only_the_address_and_the_short_last_line(
    salon, specialist, service
):
    _set_contacts(salon, phone="", address=ADDRESS)
    customer = _customer(salon, name="Олена", lang="uk")
    appointment = _booking(salon, customer, specialist, service)

    _subject, body = _build(salon, appointment, CONFIRMED)

    assert body.splitlines()[-3:] == [
        SALON_UK,
        ADDRESS,
        f"Якщо ви не записувалися в {SALON_UK}, будь ласка, повідомте салон.",
    ]


def test_salon_with_neither_phone_nor_address_has_no_contact_line(salon, specialist, service):
    _set_contacts(salon, phone="", address="")
    customer = _customer(salon, name="Олена", lang="uk")
    appointment = _booking(salon, customer, specialist, service)

    _subject, body = _build(salon, appointment, CONFIRMED)

    assert body.splitlines()[-3:] == [
        _manage_link(salon, appointment),
        SALON_UK,
        f"Якщо ви не записувалися в {SALON_UK}, будь ласка, повідомте салон.",
    ]
    assert "·" not in body


def test_salon_with_only_a_phone_shows_the_phone_without_a_separator(salon, specialist, service):
    _set_contacts(salon, phone=PHONE, address="")
    customer = _customer(salon, name="Олена", lang="uk")
    appointment = _booking(salon, customer, specialist, service)

    _subject, body = _build(salon, appointment, CONFIRMED)

    assert body.splitlines()[-3:] == [
        SALON_UK,
        PHONE,
        f"Якщо ви не записувалися в {SALON_UK}, будь ласка, повідомте салон за номером вище.",
    ]
    assert "·" not in body


# --- 16. Refund deadline -------------------------------------------------------------


# Sunday 15 Nov 00:30 in Kyiv is still Saturday 14 Nov 22:30 in UTC: the
# deadline is Saturday 14 Nov 00:30 local (Friday 22:30 in UTC).
_AFTER_LOCAL_MIDNIGHT = dt.datetime(2026, 11, 14, 22, 30, tzinfo=dt.UTC)
# Across the end of summer time (Sunday 25 Oct 2026): start Sunday 14:00 EET;
# minus 24 hours is Saturday 15:00 EEST, the moment the refund rule uses.
_AFTER_DST_END = dt.datetime(2026, 10, 25, 12, 0, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    ("start", "lang", "expected"),
    [
        (_AFTER_LOCAL_MIDNIGHT, "uk", "тобто до 00:30 суботи, 14 листопада."),
        (_AFTER_LOCAL_MIDNIGHT, "en", "that is until 00:30 on Saturday, 14 November."),
        (_AFTER_DST_END, "uk", "тобто до 15:00 суботи, 24 жовтня."),
        (_AFTER_DST_END, "en", "that is until 15:00 on Saturday, 24 October."),
    ],
    ids=["midnight_uk", "midnight_en", "dst_end_uk", "dst_end_en"],
)
def test_refund_deadline_sentence_is_start_minus_24_hours_in_the_salon_time_zone(
    monkeypatch, salon, specialist, service, start, lang, expected
):
    # Built two days before the start, so the deadline is still ahead and the
    # deadline sentence (not the "no refund" one) is shown.
    monkeypatch.setattr(timezone, "now", lambda: start - dt.timedelta(days=2))
    customer = _customer(salon, name="Olena", lang=lang)
    appointment = _booking(salon, customer, specialist, service, start=start)

    _subject, body = _build(salon, appointment, CONFIRMED)

    assert expected in body


# --- 16b. Deadline already past when the email is built --------------------------------

_UK_DEADLINE = (
    "Скасувати запис з поверненням передоплати можна не пізніше ніж за 24 години до "
    "візиту, тобто до 14:00 п'ятниці, 13 листопада. Пізніше передоплата не повертається."
)
_EN_DEADLINE = (
    "You can cancel with a refund of the deposit no later than 24 hours before the "
    "visit, that is until 14:00 on Friday, 13 November. After that the deposit is not "
    "refunded."
)
_UK_TOO_LATE = "До візиту менше 24 годин, тому при скасуванні передоплата не повертається."
_EN_TOO_LATE = (
    "There are less than 24 hours before the visit, so the deposit is not refunded if you cancel."
)
# START is Saturday 14 Nov 12:00 UTC, so the refund deadline is 13 Nov 12:00 UTC.
_DEADLINE_UTC = START - dt.timedelta(hours=24)


@pytest.mark.parametrize(
    ("now", "lang", "expected", "not_expected"),
    [
        (_DEADLINE_UTC + dt.timedelta(minutes=1), "uk", _UK_TOO_LATE, _UK_DEADLINE),
        (_DEADLINE_UTC + dt.timedelta(minutes=1), "en", _EN_TOO_LATE, _EN_DEADLINE),
        # At the deadline itself a cancellation still refunds (now <= deadline,
        # the cancel flow's comparison), so the deadline sentence stays.
        (_DEADLINE_UTC, "uk", _UK_DEADLINE, _UK_TOO_LATE),
        (_DEADLINE_UTC - dt.timedelta(hours=1), "en", _EN_DEADLINE, _EN_TOO_LATE),
    ],
    ids=["after_uk", "after_en", "at_deadline_uk", "before_en"],
)
def test_confirmed_email_built_after_the_refund_deadline_says_the_deposit_is_not_refunded(
    monkeypatch, salon, specialist, service, now, lang, expected, not_expected
):
    """docs/DECISIONS.md "Notifications", texts: a booking made less than
    24 hours before the visit must not promise a refund until a time that has
    already passed. The decision is made at the moment the email is built."""
    monkeypatch.setattr(timezone, "now", lambda: now)
    customer = _customer(salon, name="Olena", lang=lang)
    appointment = _booking(salon, customer, specialist, service)

    _subject, body = _build(salon, appointment, CONFIRMED)

    lines = body.splitlines()
    assert expected in lines
    assert not_expected not in lines


# --- 17. Greeting uses the name as entered ------------------------------------------


def test_greeting_uses_the_customer_name_exactly_as_entered(salon, specialist, service):
    customer = _customer(salon, name="олена", lang="uk")
    appointment = _booking(salon, customer, specialist, service)

    _subject, body = _build(salon, appointment, CONFIRMED)

    assert body.splitlines()[0] == "Вітаємо, олена!"


# --- 18. Two salons ------------------------------------------------------------------


def test_email_for_salon_b_carries_only_salon_b_identity_and_currency(
    salon, other_salon, specialist, service
):
    """Salon B in EUR with its own name, phone and address; a deposit with
    cents (50.50 of 200.00) so both amount rules are exercised. Salon A's
    identity (fixtures above, UAH) must not appear in B's email."""
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
    # Salon A has a booking too, so B's email has a real neighbour to leak from.
    a_customer = _customer(salon, name="Олена", lang="uk")
    _booking(salon, a_customer, specialist, service)
    b_customer = _customer(other_salon, name="Ніно", lang="uk", email="nino@example.com")
    b_appointment = _booking(
        other_salon, b_customer, b_specialist, b_service, price="200.00", paid="50.50"
    )

    subject, body = _build(other_salon, b_appointment, CONFIRMED)

    assert subject == "Ваш запис у Студія Б підтверджено"
    lines = body.splitlines()
    assert "Передоплата: 50.50 EUR (сплачено)" in lines
    assert "Оплата при візиті: 149.50 EUR" in lines
    assert "+995 32 200 0000 · Rustaveli Ave 1, Tbilisi" in lines
    assert "Студія Б" in lines
    for leaked in (SALON_UK, SALON_EN, PHONE, ADDRESS, "UAH"):
        assert leaked not in subject
        assert leaked not in body


# --- 19. No succeeded Payment: money lines omitted, never raises ---------------------


def _guest_booking_without_payment(salon, customer, specialist, service) -> Appointment:
    appointment = make_appointment(
        salon=salon, customer=customer, specialist=specialist, service=service, start=START
    )
    with tenant_context(salon.id):
        issue_guest_token(appointment)
    return appointment


@pytest.mark.parametrize(
    ("trigger", "expected_middle"),
    [
        (
            CONFIRMED,
            [
                "Your booking is confirmed:",
                "Service: Manicure",
                "Specialist: Jane",
                "When: Saturday, 14 November 2026, 14:00",
                "Duration: 1 h 30 min",
                "View or cancel your booking:",
            ],
        ),
        (
            REMINDER,
            [
                "This is a reminder of your booking tomorrow:",
                "Service: Manicure",
                "Specialist: Jane",
                "When: Saturday, 14 November 2026, 14:00",
                "If your plans have changed, you can cancel your booking here:",
            ],
        ),
    ],
    ids=["confirmed", "reminder"],
)
@pytest.mark.parametrize(
    "payment_status",
    [None, PaymentStatus.PENDING, PaymentStatus.FAILED],
    ids=["no_payment", "pending", "failed"],
)
def test_without_a_succeeded_payment_the_money_lines_and_deadline_are_omitted(
    salon, specialist, service, trigger, expected_middle, payment_status
):
    """docs/DECISIONS.md "Notifications", texts: with no succeeded Payment
    the builder leaves out the deposit and amount-due lines and the
    refund-deadline sentence, and never raises (a raising build fails the
    send task and the email is never sent)."""
    customer = _customer(salon, name="Olena", lang="en")
    appointment = _guest_booking_without_payment(salon, customer, specialist, service)
    if payment_status is not None:
        with tenant_context(salon.id):
            Payment.objects.create(
                salon=salon,
                appointment=appointment,
                amount=Decimal("24.00"),
                currency=salon.currency,
                status=payment_status,
                provider_reference_id=f"ref-{appointment.id}",
            )

    _subject, body = _build(salon, appointment, trigger)

    assert body == _lines(
        "Hello, Olena!",
        *expected_middle,
        _manage_link(salon, appointment),
        SALON_EN,
        f"{PHONE} · {ADDRESS}",
        f"If you did not book at {SALON_EN}, please let the salon know at the number above.",
    )
