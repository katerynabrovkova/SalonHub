"""
FAQItem model tests (docs/DECISIONS.md § Stage 16 planning, "Sub-step 2
details"): defaults, field length limits, the per-salon cap on active items
enforced in clean(), and the ["ordering", "id"] ordering.

The cap is a non-field error (NON_FIELD_ERRORS): it is a property of the
salon's whole active list, not of one field on this row.
"""

import pytest
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError

from core.tenancy import tenant_context
from tenants.models import FAQItem
from tests.conftest import make_active_faq_items

pytestmark = pytest.mark.django_db


@pytest.fixture
def full_salon_items(salon) -> list[FAQItem]:
    """`salon` already holds FAQ_ITEMS_MAX_ACTIVE active items."""
    return make_active_faq_items(salon)


def _full_clean(item: FAQItem) -> None:
    # Model validation checks the base (id, salon) UniqueConstraint through
    # the tenant-scoped default manager, so a tenant must be bound.
    with tenant_context(item.salon_id):
        item.full_clean()


def test_new_item_defaults(salon):
    item = FAQItem.unscoped_objects.create(
        salon=salon, question="Is there parking?", answer="Yes, free."
    )
    item.refresh_from_db()

    assert item.ordering == 0
    assert item.is_active is True


def test_full_clean_rejects_question_over_200_and_answer_over_1000(salon):
    long_question = FAQItem(salon=salon, question="q" * 201, answer="Fine.")
    with pytest.raises(ValidationError) as question_exc:
        _full_clean(long_question)
    assert "question" in question_exc.value.message_dict

    long_answer = FAQItem(salon=salon, question="Fine?", answer="a" * 1001)
    with pytest.raises(ValidationError) as answer_exc:
        _full_clean(long_answer)
    assert "answer" in answer_exc.value.message_dict


def test_full_clean_accepts_question_of_200_and_answer_of_1000(salon):
    item = FAQItem(salon=salon, question="q" * 200, answer="a" * 1000)

    _full_clean(item)


def test_full_clean_rejects_new_active_item_over_the_cap(salon, full_salon_items):
    item = FAQItem(salon=salon, question="One more?", answer="No.")

    with pytest.raises(ValidationError) as exc_info:
        _full_clean(item)

    assert NON_FIELD_ERRORS in exc_info.value.message_dict


def test_full_clean_accepts_new_inactive_item_at_the_cap(salon, full_salon_items):
    item = FAQItem(salon=salon, question="One more?", answer="Hidden.", is_active=False)

    _full_clean(item)


def test_full_clean_rejects_activating_an_inactive_item_at_the_cap(salon, full_salon_items):
    item = FAQItem.unscoped_objects.create(
        salon=salon, question="Dormant?", answer="Yes.", is_active=False
    )
    item.is_active = True

    with pytest.raises(ValidationError) as exc_info:
        _full_clean(item)

    assert NON_FIELD_ERRORS in exc_info.value.message_dict


def test_cap_is_per_salon(salon, other_salon):
    make_active_faq_items(other_salon)
    item = FAQItem(salon=salon, question="Is there parking?", answer="Yes.")

    _full_clean(item)


def test_editing_an_active_item_at_the_cap_does_not_count_itself(salon, full_salon_items):
    item = full_salon_items[-1]
    item.answer = "Updated answer."

    _full_clean(item)


def test_items_are_ordered_by_ordering_then_id(salon):
    # Checked on the SQL, not on returned rows: Postgres may hand back rows in
    # id order even without the tiebreak, so a data check can't prove it.
    with tenant_context(salon.id):
        sql = str(FAQItem.objects.all().query)

    order_by = sql.split(" ORDER BY ", 1)[1]
    assert order_by == '"tenants_faqitem"."ordering" ASC, "tenants_faqitem"."id" ASC'
