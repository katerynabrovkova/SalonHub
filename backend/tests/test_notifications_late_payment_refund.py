"""
The late-payment refund email, `LATE_PAYMENT_REFUNDED` (docs/DECISIONS.md
"Notifications": the late-payment refund bullet, its texts and the last-line
rule; "Payments and refunds > Flow"; register "In progress: booking emails",
sub-step (b)).

- A succeeded payment for an appointment already `EXPIRED` or `CANCELLED` is
  refunded in full and the customer gets this email, recorded in the same
  transaction that sets `Payment.refund_due`; no `BOOKING_CONFIRMED`.
- Dedup is per Payment: one email per refunded late payment.
- The email carries no appointment link, only the salon's `/services` page;
  the refund amount is the Payment's own amount in the Payment's own
  currency.

Real-DB integration tests over PaymentWebhookView with its default
MockPaymentProvider (accepts any signature, no network).
CELERY_TASK_ALWAYS_EAGER is True in tests, so a dispatched send runs inline
once the captured on_commit callbacks execute. `timezone.now` is frozen for
every test.
"""

import datetime as dt
from decimal import Decimal

import psycopg
import pytest
from django.conf import settings
from django.core import mail
from django.db import IntegrityError
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Customer
from booking.guest_tokens import derive_guest_token, issue_guest_token
from booking.models import Appointment, AppointmentStatus
from catalog.models import Service, ServiceCategory
from core.tenancy import tenant_context
from notifications.models import (
    Notification,
    NotificationChannel,
    NotificationStatus,
    NotificationTrigger,
)
from payments.models import Payment, PaymentStatus, ProcessedWebhookEvent
from specialists.models import Specialist, SpecialistService
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

NOW = dt.datetime(2026, 11, 10, 9, 0, tzinfo=dt.UTC)
# Saturday 14 November 2026, 14:00 in Kyiv (EET, +02:00).
START = dt.datetime(2026, 11, 14, 12, 0, tzinfo=dt.UTC)

PHONE = "+380 44 123 4567"
ADDRESS = "вул. Хрещатик, 1, Київ"
SALON_EN = "Bella Demo Salon"
SALON_UK = "Белла Демо"

LATE_STATUSES = pytest.mark.parametrize(
    "status",
    [AppointmentStatus.EXPIRED, AppointmentStatus.CANCELLED],
    ids=["expired", "cancelled"],
)


def _late() -> str:
    # Read inside each test, not at import: until the trigger exists the
    # attribute lookup fails that test alone, not the collection of the file.
    return NotificationTrigger.LATE_PAYMENT_REFUNDED


@pytest.fixture(autouse=True)
def _frozen_now(monkeypatch):
    monkeypatch.setattr(timezone, "now", lambda: NOW)


@pytest.fixture(autouse=True)
def _subdomain_frontend_url(settings):
    settings.FRONTEND_URL = "http://{slug}.testserver"


@pytest.fixture(autouse=True)
def _salon_identity(salon, service, specialist):
    """Bilingual names, a phone and an address on the shared fixtures."""
    salon.name = {"en": SALON_EN, "uk": SALON_UK}
    salon.phone = PHONE
    salon.address = ADDRESS
    salon.save(update_fields=["name", "phone", "address"])
    with tenant_context(salon.id):
        service.name = {"en": "Manicure", "uk": "Манікюр"}
        service.save(update_fields=["name"])
        specialist.name = {"en": "Jane", "uk": "Яна"}
        specialist.save(update_fields=["name"])


def _customer(salon, *, lang: str = "uk", email: str = "olena@example.com") -> Customer:
    name = "Олена" if lang == "uk" else "Olena"
    with tenant_context(salon.id):
        return Customer.objects.create(
            salon=salon, name=name, email=email, phone="+380000000001", preferred_language=lang
        )


def _late_booking(
    salon,
    customer,
    specialist,
    service,
    *,
    status: str,
    ref: str,
    paid: str = "24.50",
    guest: bool = True,
) -> Appointment:
    """An appointment already `status` whose deposit payment is still
    PENDING, so a payment_succeeded event for `ref` lands late. Price 120.00
    at the salon's default 20% gives a computed deposit of 24; the Payment's
    own amount (`paid`) differs on purpose, so the email shows the Payment's
    amount, not a recomputed one. `guest=True` issues the guest token row a
    guest booking has."""
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=status,
    )
    with tenant_context(salon.id):
        Appointment.objects.filter(pk=appointment.pk).update(
            service_price_at_booking=Decimal("120.00")
        )
        appointment.refresh_from_db()
        Payment.objects.create(
            salon=salon,
            appointment=appointment,
            amount=Decimal(paid),
            currency=salon.currency,
            status=PaymentStatus.PENDING,
            provider_reference_id=ref,
        )
        if guest:
            issue_guest_token(appointment)
    return appointment


def _post(event_id: str, ref: str, event_type: str = "payment_succeeded"):
    return APIClient().post(
        "/api/v1/webhooks/payments/",
        {"event_id": event_id, "event_type": event_type, "provider_reference_id": ref},
        format="json",
        HTTP_X_SIGNATURE="sig",
    )


def _notifications(salon, **filters) -> list[Notification]:
    with tenant_context(salon.id):
        return list(Notification.objects.filter(**filters).order_by("pk"))


def _payment(salon, appointment) -> Payment:
    with tenant_context(salon.id):
        return Payment.objects.get(appointment=appointment)


def _services_link(salon) -> str:
    return f"{settings.FRONTEND_URL.format(slug=salon.slug)}/services"


def _lines(*lines: str) -> str:
    return "\n".join(lines)


def _set_contacts(salon, *, phone: str, address: str) -> None:
    salon.phone = phone
    salon.address = address
    salon.save(update_fields=["phone", "address"])


# --- 1, 2. recorded on the late-payment branch ---------------------------------------


@LATE_STATUSES
def test_late_payment_records_one_refund_email_and_no_confirmation(
    salon, specialist, service, django_capture_on_commit_callbacks, status
):
    """EXPIRED: the time to pay ran out first. CANCELLED: cancelled while the
    payment was still pending. Either way the payment is refunded in full,
    never confirmed, and the customer gets exactly one refund email."""
    customer = _customer(salon)
    appointment = _late_booking(salon, customer, specialist, service, status=status, ref="r1")

    with django_capture_on_commit_callbacks(execute=True):
        response = _post("e1", "r1")

    assert response.status_code == 200
    rows = _notifications(salon)
    assert [row.trigger_type for row in rows] == [_late()]
    row = rows[0]
    assert row.channel == NotificationChannel.EMAIL
    assert row.customer_id == customer.id
    assert row.appointment_id == appointment.id
    assert row.status == NotificationStatus.SENT
    assert [m.to for m in mail.outbox] == [[customer.email]]

    payment = _payment(salon, appointment)
    assert payment.refund_due is True
    assert payment.status == PaymentStatus.REFUND_PENDING
    with tenant_context(salon.id):
        assert Appointment.objects.get(pk=appointment.pk).status == status


@LATE_STATUSES
def test_late_payment_refund_email_is_recorded_in_the_refund_due_transaction(
    salon, specialist, service, monkeypatch, django_capture_on_commit_callbacks, status
):
    """The notification insert is part of the transaction that sets
    `refund_due`: when that insert fails with an error the dedup tolerance
    does not cover (a composite tenant FK violation), the whole webhook
    transaction rolls back, so `refund_due` is not set, the Payment stays
    PENDING and the event is not marked processed. A notification recorded
    after that transaction committed would leave `refund_due` set here."""
    customer = _customer(salon)
    appointment = _late_booking(salon, customer, specialist, service, status=status, ref="r1")

    def _raise_fk_violation(*args, **kwargs):
        exc = IntegrityError("insert or update violates foreign key constraint")
        exc.__cause__ = psycopg.errors.ForeignKeyViolation("fk")
        raise exc

    monkeypatch.setattr(Notification.objects, "create", _raise_fk_violation)

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        response = _post("e1", "r1")

    assert response.status_code == 500
    assert callbacks == []
    payment = _payment(salon, appointment)
    assert payment.refund_due is False
    assert payment.status == PaymentStatus.PENDING
    assert ProcessedWebhookEvent.objects.filter(provider_event_id="e1").count() == 0
    assert mail.outbox == []


# --- 3. a second delivery records nothing new ----------------------------------------


@pytest.mark.parametrize(
    "second_event_id", ["e1", "e1-redelivered"], ids=["same_event_id", "fresh_event_id"]
)
def test_second_delivery_of_the_late_payment_event_records_nothing_new(
    salon, specialist, service, django_capture_on_commit_callbacks, second_event_id
):
    """The same event delivered again (the ledger short-circuits it), or the
    same payment reported again under a fresh event id (the Payment is no
    longer PENDING): one email in total."""
    customer = _customer(salon)
    _late_booking(salon, customer, specialist, service, status=AppointmentStatus.EXPIRED, ref="r1")

    with django_capture_on_commit_callbacks(execute=True):
        _post("e1", "r1")
    with django_capture_on_commit_callbacks(execute=True) as second_callbacks:
        response = _post(second_event_id, "r1")

    assert response.status_code == 200
    assert second_callbacks == []
    assert [row.trigger_type for row in _notifications(salon)] == [_late()]
    assert len(mail.outbox) == 1


# --- 4. dedup per Payment ------------------------------------------------------------


def test_two_late_payments_record_one_refund_email_each(
    salon, specialist, service, django_capture_on_commit_callbacks
):
    """Dedup is per Payment. `Payment.appointment` is one-to-one, so two
    late payments mean two appointments (here the same customer's): two
    Payment rows, two emails, each on its own appointment with its own
    amount."""
    customer = _customer(salon)
    first = _late_booking(
        salon, customer, specialist, service, status=AppointmentStatus.EXPIRED, ref="r1"
    )
    second = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START + dt.timedelta(days=1),
        status=AppointmentStatus.CANCELLED,
    )
    with tenant_context(salon.id):
        Payment.objects.create(
            salon=salon,
            appointment=second,
            amount=Decimal("30.00"),
            currency=salon.currency,
            status=PaymentStatus.PENDING,
            provider_reference_id="r2",
        )

    with django_capture_on_commit_callbacks(execute=True):
        _post("e1", "r1")
    with django_capture_on_commit_callbacks(execute=True):
        _post("e2", "r2")

    rows = _notifications(salon, trigger_type=_late())
    assert sorted(row.appointment_id for row in rows) == sorted([first.id, second.id])
    assert len({row.dedup_key for row in rows}) == 2
    assert len(mail.outbox) == 2


def test_late_payment_email_uses_its_own_payment_when_the_salon_has_two(
    salon, specialist, service, django_capture_on_commit_callbacks
):
    """Two-row isolation in one salon: only the higher-pk late payment's
    webhook arrives; its email is on its own appointment and shows its own
    amount, not the other Payment's."""
    customer = _customer(salon)
    _late_booking(
        salon,
        customer,
        specialist,
        service,
        status=AppointmentStatus.EXPIRED,
        ref="r1",
        paid="24.50",
    )
    later = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START + dt.timedelta(days=1),
        status=AppointmentStatus.EXPIRED,
    )
    with tenant_context(salon.id):
        Payment.objects.create(
            salon=salon,
            appointment=later,
            amount=Decimal("30.00"),
            currency=salon.currency,
            status=PaymentStatus.PENDING,
            provider_reference_id="r2",
        )

    with django_capture_on_commit_callbacks(execute=True):
        _post("e2", "r2")

    rows = _notifications(salon, trigger_type=_late())
    assert [row.appointment_id for row in rows] == [later.id]
    body_lines = mail.outbox[0].body.splitlines()
    assert "Сума повернення: 30 UAH" in body_lines
    assert "Сума повернення: 24.50 UAH" not in body_lines


# --- 5. an on-time payment ------------------------------------------------------------


def test_on_time_payment_records_no_late_payment_refund_email(
    salon, specialist, service, django_capture_on_commit_callbacks
):
    customer = _customer(salon)
    _late_booking(
        salon,
        customer,
        specialist,
        service,
        status=AppointmentStatus.PENDING_PAYMENT,
        ref="r1",
    )

    with django_capture_on_commit_callbacks(execute=True):
        response = _post("e1", "r1")

    assert response.status_code == 200
    assert [row.trigger_type for row in _notifications(salon)] == [
        NotificationTrigger.BOOKING_CONFIRMED
    ]
    assert _late() not in {row.trigger_type for row in _notifications(salon)}


# --- 6. the texts ------------------------------------------------------------------


def _uk_body(salon, *, contacts: str) -> str:
    return _lines(
        "Вітаємо, Олена!",
        "Ваша оплата надійшла, коли запис уже був недійсний (час на оплату минув або запис "
        "скасовано), тому ми повертаємо передоплату повністю:",
        "Послуга: Манікюр",
        "Коли: субота, 14 листопада 2026, 14:00",
        "Сума повернення: 24.50 UAH",
        "Гроші повернуться на картку, з якої ви платили. Термін залежить від вашого банку.",
        "Щоб записатися знову, перейдіть на сайт салону:",
        _services_link(salon),
        SALON_UK,
        contacts,
    )


def _en_body(salon, *, contacts: str) -> str:
    return _lines(
        "Hello, Olena!",
        "Your payment arrived when the booking was no longer valid (the time to pay had run "
        "out or the booking had been cancelled), so we are refunding the deposit in full:",
        "Service: Manicure",
        "When: Saturday, 14 November 2026, 14:00",
        "Refund amount: 24.50 UAH",
        "The money will return to the card you paid with. How long it takes depends on your bank.",
        "To book again, go to the salon's website:",
        _services_link(salon),
        SALON_EN,
        contacts,
    )


# Both variants of the "did not book" last line, which this email never has.
_LAST_LINES = {
    "uk": (
        f"Якщо ви не записувалися в {SALON_UK}, будь ласка, повідомте салон за номером вище.",
        f"Якщо ви не записувалися в {SALON_UK}, будь ласка, повідомте салон.",
    ),
    "en": (
        f"If you did not book at {SALON_EN}, please let the salon know at the number above.",
        f"If you did not book at {SALON_EN}, please let the salon know.",
    ),
}


@pytest.mark.parametrize(
    ("lang", "phone", "subject", "expected_body"),
    [
        (
            "uk",
            PHONE,
            f"Повернення передоплати в {SALON_UK}",
            lambda salon: _uk_body(salon, contacts=f"{PHONE} · {ADDRESS}"),
        ),
        (
            "uk",
            "",
            f"Повернення передоплати в {SALON_UK}",
            lambda salon: _uk_body(salon, contacts=ADDRESS),
        ),
        (
            "en",
            PHONE,
            f"Deposit refund from {SALON_EN}",
            lambda salon: _en_body(salon, contacts=f"{PHONE} · {ADDRESS}"),
        ),
        (
            "en",
            "",
            f"Deposit refund from {SALON_EN}",
            lambda salon: _en_body(salon, contacts=ADDRESS),
        ),
    ],
    ids=["uk_with_phone", "uk_without_phone", "en_with_phone", "en_without_phone"],
)
def test_late_payment_refund_email_text(
    salon,
    specialist,
    service,
    django_capture_on_commit_callbacks,
    lang,
    phone,
    subject,
    expected_body,
):
    """The whole subject and body, compared with the recorded texts. The
    refund amount is the Payment's own 24.50 (the appointment's computed
    deposit is 24) in the Payment's own UAH, after the salon switched to
    EUR. No "did not book" last line, with or without a phone: the booking
    no longer exists and BOOKING_CREATED already carried the line."""
    _set_contacts(salon, phone=phone, address=ADDRESS)
    customer = _customer(salon, lang=lang)
    _late_booking(salon, customer, specialist, service, status=AppointmentStatus.EXPIRED, ref="r1")
    salon.currency = "EUR"
    salon.save(update_fields=["currency"])

    with django_capture_on_commit_callbacks(execute=True):
        _post("e1", "r1")

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.subject == subject
    assert message.body == expected_body(salon)
    for last_line in _LAST_LINES[lang]:
        assert last_line not in message.body


# --- 7. no guest token, manage or pay link -------------------------------------------


@LATE_STATUSES
def test_late_payment_refund_email_carries_no_token_and_no_appointment_link(
    salon, specialist, service, django_capture_on_commit_callbacks, status
):
    """A guest booking (with its guest token row), so the token and the
    manage/pay links are all at hand; none of them may appear."""
    customer = _customer(salon)
    appointment = _late_booking(
        salon, customer, specialist, service, status=status, ref="r1", guest=True
    )

    with django_capture_on_commit_callbacks(execute=True):
        _post("e1", "r1")

    assert len(mail.outbox) == 1
    text = f"{mail.outbox[0].subject}\n{mail.outbox[0].body}"
    assert derive_guest_token(appointment.id) not in text
    assert "token" not in text
    assert "/manage" not in text
    assert "/booking/pay" not in text
    assert "/client" not in text
    assert _services_link(salon) in text.splitlines()


# --- 8. two salons -----------------------------------------------------------------


def test_late_payment_refund_email_of_salon_b_carries_only_salon_b(
    salon, other_salon, specialist, service, django_capture_on_commit_callbacks
):
    other_salon.name = {"en": "Other Salon", "uk": "Інший салон"}
    other_salon.phone = "+380 32 765 4321"
    other_salon.address = "пл. Ринок, 2, Львів"
    other_salon.save(update_fields=["name", "phone", "address"])
    with tenant_context(other_salon.id):
        category_b = ServiceCategory.objects.create(salon=other_salon, name={"en": "Hair"})
        service_b = Service.objects.create(
            salon=other_salon,
            category=category_b,
            name={"en": "Haircut", "uk": "Стрижка"},
            duration_minutes=60,
            price="500.00",
            buffer_minutes=15,
        )
        specialist_b = Specialist.objects.create(salon=other_salon, name={"en": "Ann"})
        SpecialistService.objects.create(
            salon=other_salon, specialist=specialist_b, service=service_b
        )

    customer_a = _customer(salon, email="a@example.com")
    customer_b = _customer(other_salon, email="b@example.com")
    _late_booking(
        salon, customer_a, specialist, service, status=AppointmentStatus.EXPIRED, ref="ra"
    )
    appointment_b = _late_booking(
        other_salon,
        customer_b,
        specialist_b,
        service_b,
        status=AppointmentStatus.EXPIRED,
        ref="rb",
    )

    with django_capture_on_commit_callbacks(execute=True):
        _post("ea", "ra")
    with django_capture_on_commit_callbacks(execute=True):
        _post("eb", "rb")

    assert [row.appointment_id for row in _notifications(other_salon, trigger_type=_late())] == [
        appointment_b.id
    ]
    emails_b = [m for m in mail.outbox if m.to == [customer_b.email]]
    assert len(emails_b) == 1
    text_b = f"{emails_b[0].subject}\n{emails_b[0].body}"
    assert emails_b[0].subject == "Повернення передоплати в Інший салон"
    lines_b = text_b.splitlines()
    assert "Інший салон" in lines_b
    assert "+380 32 765 4321 · пл. Ринок, 2, Львів" in lines_b
    assert _services_link(other_salon) in lines_b
    for salon_a_value in (SALON_UK, SALON_EN, PHONE, ADDRESS, salon.slug):
        assert salon_a_value not in text_b


# --- 9. recipient and language -----------------------------------------------------


@pytest.mark.parametrize(
    ("lang", "subject"),
    [("uk", f"Повернення передоплати в {SALON_UK}"), ("en", f"Deposit refund from {SALON_EN}")],
    ids=["uk", "en"],
)
def test_late_payment_refund_email_goes_to_the_customer_in_their_language(
    salon, specialist, service, django_capture_on_commit_callbacks, lang, subject
):
    """Two customers in the salon; the late payment is the higher-pk
    customer's. The email goes to that customer (not the salon's contact
    address, not the other customer), in that customer's preferred
    language, whatever the other customer prefers."""
    other_lang = "en" if lang == "uk" else "uk"
    _customer(salon, lang=other_lang, email="other@example.com")
    customer = _customer(salon, lang=lang, email="payer@example.com")
    _late_booking(salon, customer, specialist, service, status=AppointmentStatus.EXPIRED, ref="r1")

    with django_capture_on_commit_callbacks(execute=True):
        _post("e1", "r1")

    rows = _notifications(salon, trigger_type=_late())
    assert [row.customer_id for row in rows] == [customer.id]
    assert [m.to for m in mail.outbox] == [["payer@example.com"]]
    assert mail.outbox[0].subject == subject
