"""
reset_bella_demo: deletes the demo salon's (slug "bella-demo") appointments,
payments, guest tokens, notifications, reviews, customers and client
accounts, keeping the salon, its catalogue, its admin accounts, the platform
`User` rows, the daily assistant counters and the test account
item8-new@example.com with its Customer (docs/DECISIONS.md § "Stage 17
sub-step order", 17.8 "Demo salon commands"). Runs only with DEBUG on and
only with --yes.
"""

import datetime as dt
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from accounts.models import Account, AccountRole, Customer, User
from ai_assistant.models import DailyConversationCount
from booking.models import Appointment, AppointmentStatus, GuestAccessToken
from catalog.models import Service, ServiceCategory
from core.tenancy import tenant_context
from notifications.models import Notification, NotificationChannel, NotificationTrigger
from payments.models import Payment, PaymentStatus
from reviews.models import Review
from specialists.models import Specialist, SpecialistService
from tenants.demo import bella_demo as demo
from tenants.models import FAQItem, Salon

# Fixed, never compared with the real clock: the reset ignores dates.
VISIT = dt.datetime(2026, 9, 1, 10, 0, tzinfo=dt.UTC)

MODELS = [
    Customer,
    Account,
    Appointment,
    Payment,
    GuestAccessToken,
    Notification,
    Review,
    ServiceCategory,
    Service,
    Specialist,
    SpecialistService,
    FAQItem,
    DailyConversationCount,
]


@pytest.fixture(autouse=True)
def _debug_on(settings):
    settings.DEBUG = True


def _pks(model, salon_id):
    return set(model.unscoped_objects.filter(salon_id=salon_id).values_list("pk", flat=True))


def _snapshot(salon_id):
    return {model.__name__: _pks(model, salon_id) for model in MODELS}


class _Counter:
    value = 0

    @classmethod
    def next(cls):
        cls.value += 1
        return cls.value


def _visit(salon, customer, specialist, service, *, with_review):
    """A completed visit with a payment, a guest token, a notification and,
    when asked, a review."""
    n = _Counter.next()
    start = VISIT + dt.timedelta(days=n)
    end = start + dt.timedelta(minutes=service.duration_minutes)
    appointment = Appointment.objects.create(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start_datetime=start,
        end_datetime=end,
        blocked_until=end,
        service_price_at_booking=service.price,
        deposit_percentage_at_booking=salon.deposit_percentage,
        hold_expires_at=start,
        status=AppointmentStatus.COMPLETED,
    )
    Payment.objects.create(
        salon=salon,
        appointment=appointment,
        amount="10.00",
        currency=salon.currency,
        status=PaymentStatus.SUCCEEDED,
        provider_reference_id=f"ref-{n}",
    )
    GuestAccessToken.objects.create(
        salon=salon, appointment=appointment, token_hash=f"{n:064d}", expires_at=end
    )
    Notification.objects.create(
        salon=salon,
        appointment=appointment,
        customer=customer,
        channel=NotificationChannel.EMAIL,
        trigger_type=NotificationTrigger.BOOKING_CREATED,
        dedup_key=f"appointment:{appointment.pk}",
    )
    if with_review:
        Review.objects.create(
            salon=salon,
            appointment=appointment,
            customer=customer,
            specialist=specialist,
            rating=5,
        )
    return appointment


def _build_salon_data(salon, *, kept_email=True):
    """Everything the reset deals with, in one salon: guest and registered
    customers with visits, an admin account linked to its own Customer, the
    kept test account (when `kept_email`) with its own visits and a
    customer-only notification, and the catalogue the reset must leave."""
    with tenant_context(salon.id):
        category = ServiceCategory.objects.create(salon=salon, name={"en": "Nails"})
        service = Service.objects.create(
            salon=salon,
            category=category,
            name={"en": "Manicure"},
            duration_minutes=60,
            price="20.00",
        )
        specialist = Specialist.objects.create(salon=salon, name={"en": "Jane"})
        SpecialistService.objects.create(salon=salon, specialist=specialist, service=service)
        FAQItem.objects.create(salon=salon, question="Питання?", answer="Відповідь.")
        DailyConversationCount.objects.create(salon=salon, date=VISIT.date(), count=7)

        guest = Customer.objects.create(
            salon=salon, name="Guest", email="guest@example.com", phone="+10000000001"
        )
        _visit(salon, guest, specialist, service, with_review=True)
        _visit(salon, guest, specialist, service, with_review=False)

        registered = Customer.objects.create(
            salon=salon, name="Registered", email="client@example.com", phone="+10000000002"
        )
        Account.objects.create_account(
            salon=salon,
            email="client@example.com",
            password="a-strong-passw0rd!",
            customer=registered,
        )
        _visit(salon, registered, specialist, service, with_review=True)

        staff_customer = Customer.objects.create(
            salon=salon, name="Staff", email="staff@example.com", phone="+10000000003"
        )
        admin = Account.objects.create_account(
            salon=salon,
            email="staff@example.com",
            password="a-strong-passw0rd!",
            role=AccountRole.ADMIN,
            customer=staff_customer,
        )

        kept = kept_customer = customer_only_notice = None
        if kept_email:
            kept_customer = Customer.objects.create(
                salon=salon, name="Item 8", email=demo.KEPT_ACCOUNT_EMAIL, phone="+10000000004"
            )
            kept = Account.objects.create_account(
                salon=salon,
                email=demo.KEPT_ACCOUNT_EMAIL,
                password="a-strong-passw0rd!",
                customer=kept_customer,
            )
            _visit(salon, kept_customer, specialist, service, with_review=True)
            _visit(salon, kept_customer, specialist, service, with_review=False)
            customer_only_notice = Notification.objects.create(
                salon=salon,
                customer=kept_customer,
                channel=NotificationChannel.EMAIL,
                trigger_type=NotificationTrigger.EMAIL_VERIFICATION,
                dedup_key=f"customer:{kept_customer.pk}:{salon.pk}",
            )
    return {
        "admin": admin,
        "staff_customer": staff_customer,
        "kept": kept,
        "kept_customer": kept_customer,
        "customer_only_notice": customer_only_notice,
    }


@pytest.fixture
def platform_user(db):
    return User.objects.create_superuser(
        email=demo.KEPT_ACCOUNT_EMAIL, password="a-strong-passw0rd!"
    )


@pytest.fixture
def two_salons(salon, other_salon, platform_user):
    """The other salon is filled first, so its rows have the lower pks, and
    it has its own item8-new@example.com account: the kept-email match must
    be per salon."""
    other = _build_salon_data(other_salon)
    bella = _build_salon_data(salon)
    return {"bella": bella, "other": other}


def _reset(**kwargs):
    out = StringIO()
    call_command("reset_bella_demo", stdout=out, **kwargs)
    return out.getvalue()


# --- guards -------------------------------------------------------------------


def test_refuses_when_debug_is_off(settings, salon, two_salons):
    settings.DEBUG = False
    before = _snapshot(salon.id)

    with pytest.raises(CommandError, match="DEBUG"):
        _reset(yes=True)

    assert _snapshot(salon.id) == before


def test_without_yes_only_prints_the_plan(salon, two_salons):
    before = _snapshot(salon.id)

    out = _reset()

    assert _snapshot(salon.id) == before
    assert "--yes" in out
    assert "appointments" in out


def test_refuses_when_there_is_no_bella_demo_salon(other_salon):
    _build_salon_data(other_salon)
    before = _snapshot(other_salon.id)

    with pytest.raises(CommandError, match="bella-demo"):
        _reset(yes=True)

    assert _snapshot(other_salon.id) == before


def test_refuses_when_the_kept_account_is_missing(salon, other_salon):
    """Another salon's item8-new@example.com account does not count."""
    _build_salon_data(other_salon)
    _build_salon_data(salon, kept_email=False)
    before = _snapshot(salon.id)

    with pytest.raises(CommandError, match=demo.KEPT_ACCOUNT_EMAIL):
        _reset(yes=True)

    assert _snapshot(salon.id) == before


def test_refuses_when_the_kept_account_has_no_customer(salon, two_salons):
    kept = two_salons["bella"]["kept"]
    Account.unscoped_objects.filter(pk=kept.pk).update(customer=None)
    before = _snapshot(salon.id)

    with pytest.raises(CommandError, match=demo.KEPT_ACCOUNT_EMAIL):
        _reset(yes=True)

    assert _snapshot(salon.id) == before


def test_refuses_when_the_kept_account_is_not_a_client(salon, two_salons):
    kept = two_salons["bella"]["kept"]
    Account.unscoped_objects.filter(pk=kept.pk).update(role=AccountRole.ADMIN)
    before = _snapshot(salon.id)

    with pytest.raises(CommandError, match=demo.KEPT_ACCOUNT_EMAIL):
        _reset(yes=True)

    assert _snapshot(salon.id) == before


# --- what is deleted and what is kept -----------------------------------------


def test_never_touches_another_salon_or_platform_users(salon, other_salon, two_salons):
    other_before = _snapshot(other_salon.id)
    users_before = set(User.objects.values_list("pk", "email", "password"))

    _reset(yes=True)

    assert _snapshot(other_salon.id) == other_before
    assert set(User.objects.values_list("pk", "email", "password")) == users_before


def test_keeps_the_test_account_and_its_customer(salon, two_salons):
    kept = two_salons["bella"]["kept"]
    kept_customer = two_salons["bella"]["kept_customer"]
    account_before = Account.unscoped_objects.filter(pk=kept.pk).values().get()
    customer_before = Customer.unscoped_objects.filter(pk=kept_customer.pk).values().get()

    _reset(yes=True)

    assert Account.unscoped_objects.filter(pk=kept.pk).values().get() == account_before
    assert Customer.unscoped_objects.filter(pk=kept_customer.pk).values().get() == customer_before
    for model in (Appointment, Review):
        assert not model.unscoped_objects.filter(customer_id=kept_customer.pk).exists()
    assert not Payment.unscoped_objects.filter(appointment__customer_id=kept_customer.pk).exists()
    assert not GuestAccessToken.unscoped_objects.filter(
        appointment__customer_id=kept_customer.pk
    ).exists()
    notices = Notification.unscoped_objects.filter(customer_id=kept_customer.pk)
    assert list(notices.values_list("pk", flat=True)) == [
        two_salons["bella"]["customer_only_notice"].pk
    ]


def test_deletes_the_rest_of_the_customer_data(salon, two_salons):
    bella = two_salons["bella"]

    _reset(yes=True)

    for model in (Appointment, Payment, GuestAccessToken, Review):
        assert _pks(model, salon.id) == set(), model.__name__
    assert _pks(Notification, salon.id) == {bella["customer_only_notice"].pk}
    assert _pks(Account, salon.id) == {bella["kept"].pk, bella["admin"].pk}
    assert _pks(Customer, salon.id) == {bella["kept_customer"].pk, bella["staff_customer"].pk}


def test_keeps_the_salon_catalogue_and_daily_counters(salon, two_salons):
    kept_models = [
        ServiceCategory,
        Service,
        Specialist,
        SpecialistService,
        FAQItem,
        DailyConversationCount,
    ]
    before = {model.__name__: _pks(model, salon.id) for model in kept_models}
    salon_before = Salon.objects.filter(pk=salon.pk).values().get()

    _reset(yes=True)

    assert {model.__name__: _pks(model, salon.id) for model in kept_models} == before
    assert Salon.objects.filter(pk=salon.pk).values().get() == salon_before


def test_reports_what_it_deleted(salon, two_salons):
    out = _reset(yes=True)

    # bella-demo: three plain visits plus the kept customer's two.
    assert "appointments: 5" in out
    assert "payments: 5" in out
    assert "reviews: 3" in out
    assert "accounts: 1" in out
    assert "customers: 2" in out


def test_second_run_deletes_nothing(salon, two_salons):
    _reset(yes=True)
    after_first = _snapshot(salon.id)

    out = _reset(yes=True)

    assert _snapshot(salon.id) == after_first
    assert "appointments: 0" in out
