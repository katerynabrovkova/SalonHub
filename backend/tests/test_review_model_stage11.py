"""
Stage 11 Part 2 — model changes to Review (reviews/models.py). RED phase:
these tests are written before the model change or its migration exists, and
are expected to fail with a "field doesn't exist yet" class of error
(django.core.exceptions.FieldDoesNotExist from the shared
``_require_specialist_field`` guard), or, for the hidden_at removal check, a
bare ``Failed: DID NOT RAISE`` — never a 500 or a fixture setup error.

Changes under test (not yet implemented):

1. ``Review.specialist`` — non-nullable FK to ``specialists.models.Specialist``,
   ``on_delete=PROTECT`` (matching ``appointment`` / ``customer``). Denormalized
   snapshot of who performed the visit at review-creation time.
2. ``Review.hidden_at`` removed entirely (no moderation/hide mechanism).
3. ``Review.text`` length-capped at ~2000 chars.

Enforcement level of the text cap: a DB-level Meta ``CheckConstraint``,
matching the existing ``rating`` 1-5 bound (``review_rating_between_1_and_5``)
rather than a field-level ``validators=[...]``. The boundary tests below
therefore assert against the database: the over-limit case must raise
``IntegrityError`` inside a ``transaction.atomic()`` savepoint (mirroring
``test_review_one_per_appointment.py`` / ``test_currency_db_constraint.py``),
and the at-limit case must ``create()`` cleanly.
"""

import datetime as dt

import pytest
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from booking.models import AppointmentStatus
from core.tenancy import tenant_context
from reviews.models import Review
from specialists.models import Specialist
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

REVIEW_TEXT_MAX_LENGTH = 2000


def _require_specialist_field() -> models.Field:
    """
    RED-phase guard: raises FieldDoesNotExist until Review.specialist exists,
    so every specialist-dependent test below fails for the one correct
    reason (field not added yet) rather than a misleading downstream error.
    """
    return Review._meta.get_field("specialist")


def _make_completed_appointment(salon, customer, specialist, service):
    return make_appointment(
        salon=salon,
        customer=customer,
        specialist=specialist,
        service=service,
        start=timezone.now() - dt.timedelta(days=1),
        status=AppointmentStatus.COMPLETED,
    )


# --- 1. specialist FK -------------------------------------------------------


def test_review_specialist_field_is_protected_non_nullable_fk_to_specialist(
    salon, customer, specialist, service
):
    field = _require_specialist_field()

    assert isinstance(field, models.ForeignKey)
    assert field.remote_field.model is Specialist
    assert field.remote_field.on_delete is models.PROTECT
    assert field.null is False


def test_review_cannot_be_created_without_a_specialist(salon, customer, specialist, service):
    _require_specialist_field()
    appointment = _make_completed_appointment(salon, customer, specialist, service)

    with tenant_context(salon.id):
        review = Review(salon=salon, appointment=appointment, customer=customer, rating=5)
        # NOT NULL is a DB-level guarantee; full_clean surfaces it as a
        # ValidationError on the missing field before the INSERT.
        with pytest.raises(ValidationError):
            review.full_clean()


def test_review_specialist_from_a_different_salon_raises_integrity_error(
    salon, other_salon, customer, specialist, service
):
    """
    Cross-tenant composite-FK guard, mirroring
    test_booking_create_appointment.py's
    test_create_appointment_cross_tenant_salon_mismatch_raises_integrity_error:
    the specialist belongs to `other_salon`, the Review to `salon`. Only
    core/db.py's composite_tenant_fk (specialist_id, salon_id) rejects this,
    surfacing as a raw IntegrityError. tenant_context is bound to `salon.id`,
    the tenant that owns the Review — so only the specialist is cross-tenant.
    """
    _require_specialist_field()
    appointment = _make_completed_appointment(salon, customer, specialist, service)
    with tenant_context(other_salon.id):
        foreign_specialist = Specialist.objects.create(salon=other_salon, name="Outsider")

    with tenant_context(salon.id), pytest.raises(IntegrityError):
        with transaction.atomic():
            Review.objects.create(
                salon=salon,
                appointment=appointment,
                customer=customer,
                specialist=foreign_specialist,
                rating=5,
            )


def test_review_specialist_fk_is_protect_on_delete(salon, customer, specialist, service):
    _require_specialist_field()
    appointment = _make_completed_appointment(salon, customer, specialist, service)

    with tenant_context(salon.id):
        Review.objects.create(
            salon=salon,
            appointment=appointment,
            customer=customer,
            specialist=specialist,
            rating=5,
        )
        with pytest.raises(ProtectedError):
            specialist.delete()


# --- 2. hidden_at removed -------------------------------------------------


def test_review_hidden_at_field_no_longer_exists():
    with pytest.raises(FieldDoesNotExist):
        Review._meta.get_field("hidden_at")


# --- 3. text length cap --------------------------------------------------


def test_review_text_exactly_at_limit_is_accepted_by_the_db(salon, customer, specialist, service):
    _require_specialist_field()
    appointment = _make_completed_appointment(salon, customer, specialist, service)

    with tenant_context(salon.id):
        review = Review.objects.create(
            salon=salon,
            appointment=appointment,
            customer=customer,
            specialist=specialist,
            rating=5,
            text="x" * REVIEW_TEXT_MAX_LENGTH,
        )
        assert len(review.text) == REVIEW_TEXT_MAX_LENGTH


def test_review_text_one_over_limit_is_rejected_by_the_db(salon, customer, specialist, service):
    _require_specialist_field()
    appointment = _make_completed_appointment(salon, customer, specialist, service)

    with tenant_context(salon.id):
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Review.objects.create(
                    salon=salon,
                    appointment=appointment,
                    customer=customer,
                    specialist=specialist,
                    rating=5,
                    text="x" * (REVIEW_TEXT_MAX_LENGTH + 1),
                )
