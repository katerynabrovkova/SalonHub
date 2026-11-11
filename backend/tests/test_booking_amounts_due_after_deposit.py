"""
`booking.amounts.amount_due_after_deposit`: what is left to pay at the visit
once the deposit is paid, for the `BOOKING_CREATED` email, before any
Payment has succeeded (docs/DECISIONS.md "Notifications", "Texts of the other
booking emails"). Computed from the same breakdown as
`payments.services.compute_deposit_amount` (no second formula), from the
appointment's own booking-time snapshots. `amount_due_at_visit` keeps its
meaning (a succeeded payment only) for the dashboard.
"""

import datetime as dt
from decimal import Decimal

import pytest

from booking import amounts
from booking.models import Appointment, AppointmentStatus
from core.tenancy import tenant_context
from payments.models import Payment, PaymentStatus
from payments.services import compute_deposit_breakdown
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

START = dt.datetime(2026, 11, 14, 12, 0, tzinfo=dt.UTC)


def _pending(salon, customer, specialist, service, *, price: str, percentage: str) -> Appointment:
    appointment = make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=START,
        status=AppointmentStatus.PENDING_PAYMENT,
    )
    with tenant_context(salon.id):
        Appointment.objects.filter(pk=appointment.pk).update(
            service_price_at_booking=Decimal(price),
            deposit_percentage_at_booking=Decimal(percentage),
        )
        appointment.refresh_from_db()
    return appointment


def test_amount_due_after_deposit_for_a_pending_booking_without_payment(
    salon, customer, specialist, service
):
    appointment = _pending(salon, customer, specialist, service, price="120.00", percentage="20")

    with tenant_context(salon.id):
        assert amounts.amount_due_after_deposit(appointment) == Decimal("96.00")


def test_amount_due_after_deposit_uses_the_deposit_breakdown_rounding(
    salon, customer, specialist, service
):
    """125.00 at 30% is 37.50, rounded half up to a whole 38: 87.00 at the
    visit, exactly the breakdown's own due_at_visit."""
    appointment = _pending(salon, customer, specialist, service, price="125.00", percentage="30")

    with tenant_context(salon.id):
        result = amounts.amount_due_after_deposit(appointment)

    assert result == Decimal("87.00")
    assert (
        result
        == compute_deposit_breakdown(price=Decimal("125.00"), percentage=Decimal("30")).due_at_visit
    )


def test_amount_due_after_deposit_uses_the_booking_time_percentage(
    salon, customer, specialist, service
):
    appointment = _pending(salon, customer, specialist, service, price="120.00", percentage="20")
    salon.deposit_percentage = Decimal("50")
    salon.save(update_fields=["deposit_percentage"])

    with tenant_context(salon.id):
        appointment.refresh_from_db()
        assert amounts.amount_due_after_deposit(appointment) == Decimal("96.00")


@pytest.mark.parametrize(
    "payment_status", [PaymentStatus.PENDING, PaymentStatus.FAILED], ids=["pending", "failed"]
)
def test_amount_due_after_deposit_does_not_need_a_succeeded_payment(
    salon, customer, specialist, service, payment_status
):
    appointment = _pending(salon, customer, specialist, service, price="120.00", percentage="20")
    with tenant_context(salon.id):
        Payment.objects.create(
            salon=salon,
            appointment=appointment,
            amount=Decimal("24.00"),
            currency=salon.currency,
            status=payment_status,
            provider_reference_id=f"ref-{appointment.id}",
        )
        appointment.refresh_from_db()

        assert amounts.amount_due_after_deposit(appointment) == Decimal("96.00")


def test_amount_due_at_visit_keeps_its_meaning_for_a_pending_booking(
    salon, customer, specialist, service
):
    """The dashboard's value is still None until a payment has succeeded."""
    appointment = _pending(salon, customer, specialist, service, price="120.00", percentage="20")

    with tenant_context(salon.id):
        assert amounts.amount_due_at_visit(appointment) is None
