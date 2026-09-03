"""
Stage 9 — CheckConstraint enforcing non-emptiness (contact_email > "") on
Salon.contact_email at the database level (docs/DECISIONS.md § Stage 9
decisions). Tests only.

The empty-string case calls .objects.create() directly, never full_clean()
— this is itself the proof that enforcement is DB-level and does not
depend on a validator being invoked. The DECISIONS.md entry deliberately
adds no field-level validator, precisely because a validator is bypassed
by .create(), bulk_create(), and raw SQL — exactly the write paths this
constraint has to hold on. Mirrors test_currency_db_constraint.py.

Real-DB integration test (a CheckConstraint is a database-level
guarantee). The expected-failure case wraps the failing create() in its
own transaction.atomic() savepoint, inside pytest.raises(IntegrityError) —
because Postgres aborts the whole surrounding transaction after an
IntegrityError, and pytest-django wraps each test in one.
"""

import pytest
from django.db import IntegrityError, transaction

from tenants.models import Salon

pytestmark = pytest.mark.django_db


def test_salon_contact_email_valid_value_succeeds():
    salon = Salon.objects.create(
        name="Valid Contact Email Salon",
        slug="valid-contact-email-salon",
        currency="UAH",
        contact_email="owner@valid-contact-email-salon.example",
    )
    assert salon.contact_email == "owner@valid-contact-email-salon.example"


def test_salon_contact_email_empty_string_rejected():
    with pytest.raises(IntegrityError) as exc_info:
        with transaction.atomic():
            Salon.objects.create(
                name="Empty Contact Email Salon",
                slug="empty-contact-email-salon",
                currency="UAH",
                contact_email="",
            )
    # Disambiguate: the failure is the contact_email constraint, not some
    # other constraint on the row.
    assert "salon_contact_email_not_empty" in str(exc_info.value)
