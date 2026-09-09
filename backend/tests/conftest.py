import datetime as dt

import pytest
from django.core.cache import cache
from django.utils import timezone

from accounts.models import Account, AccountRole, Customer, User
from booking.constants import SLOT_HOLD_DURATION
from booking.models import Appointment, AppointmentStatus
from catalog.models import Service, ServiceCategory
from core.tenancy import tenant_context
from specialists.models import Specialist, TimeOff, WorkingHours
from tenants.models import Salon


@pytest.fixture(autouse=True)
def _celery_eager(settings):
    """
    Runs @shared_task calls inline instead of dispatching to a real worker —
    .delay()/.apply_async() still go through Celery's own machinery, so this
    doesn't hide a view that forgot to enqueue at all (docs/DECISIONS.md §
    Stage 3 decisions). Combined with pytest-django's automatic EMAIL_BACKEND
    override to locmem, sent mail lands in django.core.mail.outbox.
    """
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


@pytest.fixture(autouse=True)
def _clear_cache():
    """DRF throttling counts requests in the Django cache (real Redis here,
    same as dev/prod) — clear it so one test's throttle counter can't leak
    into the next."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def salon(db):
    return Salon.objects.create(
        name={"en": "Bella Demo Salon"},
        slug="bella-demo",
        currency="UAH",
        contact_email="owner@bella-demo.example",
    )


@pytest.fixture
def other_salon(db):
    return Salon.objects.create(
        name={"en": "Other Salon"},
        slug="other-salon",
        currency="UAH",
        contact_email="owner@other-salon.example",
    )


@pytest.fixture
def service_category(salon):
    with tenant_context(salon.id):
        return ServiceCategory.objects.create(salon=salon, name={"en": "Nails"})


@pytest.fixture
def service(salon, service_category):
    with tenant_context(salon.id):
        return Service.objects.create(
            salon=salon,
            category=service_category,
            name={"en": "Manicure"},
            duration_minutes=60,
            price="500.00",
            buffer_minutes=15,
        )


@pytest.fixture
def specialist(salon):
    with tenant_context(salon.id):
        return Specialist.objects.create(salon=salon, name={"en": "Jane"})


@pytest.fixture
def customer(salon):
    with tenant_context(salon.id):
        return Customer.objects.create(
            salon=salon, name="Alice", email="alice@example.com", phone="+10000000000"
        )


@pytest.fixture
def admin_account(salon):
    """A per-salon back-office login: an Account with role=admin at `salon`
    (docs/DECISIONS.md § Stage 3-R decisions). Replaces the pre-3-R
    User + SalonStaff pairing that IsSalonStaff used to check."""
    with tenant_context(salon.id):
        return Account.objects.create_account(
            salon=salon,
            email="admin@example.com",
            password="a-strong-passw0rd!",
            role=AccountRole.ADMIN,
        )


@pytest.fixture
def other_salon_admin_account(other_salon):
    with tenant_context(other_salon.id):
        return Account.objects.create_account(
            salon=other_salon,
            email="other-admin@example.com",
            password="a-strong-passw0rd!",
            role=AccountRole.ADMIN,
        )


@pytest.fixture
def superuser(db):
    """Platform-operator Django `/admin/` login (is_staff/is_superuser). The
    cross-tenant exception (docs/DECISIONS.md § Stage 3-R decisions,
    "deliberate exception") — distinct from a per-salon `admin_account`."""
    return User.objects.create_superuser(
        email="superuser@example.com", password="a-strong-passw0rd!"
    )


def make_working_hours(
    *,
    salon: Salon,
    specialist: Specialist,
    day_of_week: int,
    start_time: dt.time,
    end_time: dt.time,
) -> WorkingHours:
    with tenant_context(salon.id):
        return WorkingHours.objects.create(
            salon=salon,
            specialist=specialist,
            day_of_week=day_of_week,
            start_time=start_time,
            end_time=end_time,
        )


def make_time_off(
    *,
    salon: Salon,
    specialist: Specialist,
    start_datetime: dt.datetime,
    end_datetime: dt.datetime,
    reason: str = "",
) -> TimeOff:
    with tenant_context(salon.id):
        return TimeOff.objects.create(
            salon=salon,
            specialist=specialist,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            reason=reason,
        )


def make_appointment(
    *,
    salon: Salon,
    customer: Customer,
    specialist: Specialist,
    service: Service,
    start: dt.datetime,
    status: str = AppointmentStatus.CONFIRMED,
    hold_expires_at: dt.datetime | None = None,
) -> Appointment:
    end = start + dt.timedelta(minutes=service.duration_minutes)
    blocked_until = end + dt.timedelta(minutes=service.buffer_minutes)
    # hold_expires_at is NOT NULL on every row regardless of status — a
    # CONFIRMED appointment still carries the value it got at birth in
    # PENDING_PAYMENT, just stale and unread past that point.
    if hold_expires_at is None:
        hold_expires_at = timezone.now() + SLOT_HOLD_DURATION
    with tenant_context(salon.id):
        return Appointment.objects.create(
            salon=salon,
            customer=customer,
            specialist=specialist,
            service=service,
            start_datetime=start,
            end_datetime=end,
            blocked_until=blocked_until,
            service_price_at_booking=service.price,
            deposit_percentage_at_booking=salon.deposit_percentage,
            hold_expires_at=hold_expires_at,
            status=status,
        )
