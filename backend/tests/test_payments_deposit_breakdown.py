"""
Stage 16 sub-step 4a — `compute_deposit_breakdown` (docs/DECISIONS.md §
Stage 16 planning, "Sub-step 4 details", 4a): the deposit and the amount due
at the visit from a price and a percentage, without an Appointment. Same
half-up rounding to whole units as `compute_deposit_amount`, which must agree
with it.

Amounts are compared as strings so the two-decimal-place exponent is checked
too, not only the numeric value (Decimal("8") == Decimal("8.00")).
"""

import datetime as dt
from decimal import Decimal

import pytest

from catalog.models import Service
from core.tenancy import tenant_context
from payments.services import DepositBreakdown, compute_deposit_amount, compute_deposit_breakdown
from tests.conftest import make_appointment

START = dt.datetime(2026, 8, 21, 10, 0, tzinfo=dt.UTC)


def _assert_breakdown(breakdown: DepositBreakdown, *, deposit: str, due_at_visit: str) -> None:
    assert isinstance(breakdown.deposit, Decimal)
    assert isinstance(breakdown.due_at_visit, Decimal)
    assert str(breakdown.deposit) == deposit
    assert str(breakdown.due_at_visit) == due_at_visit


def test_deposit_and_due_at_visit_for_a_whole_amount():
    breakdown = compute_deposit_breakdown(price=Decimal("40.00"), percentage=Decimal("20"))

    _assert_breakdown(breakdown, deposit="8.00", due_at_visit="32.00")


def test_deposit_rounds_half_up_to_whole_units():
    # 20% of 42.50 is 8.50 (rounds up); 20% of 41.00 is 8.20 (rounds down).
    up = compute_deposit_breakdown(price=Decimal("42.50"), percentage=Decimal("20"))
    down = compute_deposit_breakdown(price=Decimal("41.00"), percentage=Decimal("20"))

    _assert_breakdown(up, deposit="9.00", due_at_visit="33.50")
    _assert_breakdown(down, deposit="8.00", due_at_visit="33.00")


def test_zero_percentage_means_no_deposit_and_full_price_at_visit():
    breakdown = compute_deposit_breakdown(price=Decimal("40.00"), percentage=Decimal("0"))

    _assert_breakdown(breakdown, deposit="0.00", due_at_visit="40.00")


def test_deposit_that_rounds_to_zero_leaves_full_price_at_visit():
    breakdown = compute_deposit_breakdown(price=Decimal("2.00"), percentage=Decimal("20"))

    _assert_breakdown(breakdown, deposit="0.00", due_at_visit="2.00")


@pytest.mark.django_db
def test_breakdown_deposit_matches_compute_deposit_amount(
    salon, service_category, specialist, customer
):
    with tenant_context(salon.id):
        service = Service.objects.create(
            salon=salon,
            category=service_category,
            name={"en": "Brows"},
            duration_minutes=30,
            price="42.50",
        )
    appointment = make_appointment(
        salon=salon, customer=customer, specialist=specialist, service=service, start=START
    )
    # Reload so the snapshots are Decimals as stored, not the "42.50" string
    # the Service was created with.
    with tenant_context(salon.id):
        appointment.refresh_from_db()

    breakdown = compute_deposit_breakdown(
        price=appointment.service_price_at_booking,
        percentage=appointment.deposit_percentage_at_booking,
    )

    assert str(breakdown.deposit) == str(compute_deposit_amount(appointment))
