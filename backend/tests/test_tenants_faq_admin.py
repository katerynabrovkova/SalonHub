"""
FAQItem Django admin (docs/DECISIONS.md § Stage 16 planning, "Sub-step 2
details"): a SalonScopedAdmin, so it lists every salon's items with no
tenant bound, and its add form runs FAQItem.clean() (the active-item cap).
"""

import pytest
from django.core.exceptions import NON_FIELD_ERRORS

from tenants.models import FAQ_ITEMS_MAX_ACTIVE, FAQItem
from tests.conftest import make_active_faq_items

pytestmark = pytest.mark.django_db

CHANGELIST_URL = "/admin/tenants/faqitem/"
ADD_URL = "/admin/tenants/faqitem/add/"


def _add_payload(salon, **overrides):
    data = {
        "salon": str(salon.id),
        "question": "Is there parking?",
        "answer": "Yes, free parking behind the building.",
        "ordering": "0",
        "is_active": "on",
        "_save": "Save",
    }
    data.update(overrides)
    return data


def test_faq_changelist_lists_items_from_every_salon(client, superuser, salon, other_salon):
    FAQItem.unscoped_objects.create(salon=salon, question="Salon A question?", answer="A.")
    FAQItem.unscoped_objects.create(salon=other_salon, question="Salon B question?", answer="B.")
    client.force_login(superuser)

    response = client.get(CHANGELIST_URL)

    assert response.status_code == 200
    assert b"Salon A question?" in response.content
    assert b"Salon B question?" in response.content


def test_faq_admin_add_post_creates_item_for_chosen_salon(client, superuser, salon, other_salon):
    client.force_login(superuser)

    response = client.post(ADD_URL, _add_payload(other_salon))

    assert response.status_code == 302
    item = FAQItem.unscoped_objects.get(question="Is there parking?")
    assert item.salon_id == other_salon.id


def test_faq_admin_add_post_rejects_item_over_the_cap(client, superuser, salon):
    make_active_faq_items(salon)
    client.force_login(superuser)

    response = client.post(ADD_URL, _add_payload(salon, question="One too many?"))

    assert response.status_code == 200
    form = response.context["adminform"].form
    assert NON_FIELD_ERRORS in form.errors
    assert str(FAQ_ITEMS_MAX_ACTIVE) in " ".join(form.errors[NON_FIELD_ERRORS])
    assert not FAQItem.unscoped_objects.filter(question="One too many?").exists()
    assert FAQItem.unscoped_objects.filter(salon=salon).count() == FAQ_ITEMS_MAX_ACTIVE


def test_faq_changelist_filters_by_is_active(client, superuser, salon):
    FAQItem.unscoped_objects.create(salon=salon, question="Shown question?", answer="A.")
    FAQItem.unscoped_objects.create(
        salon=salon, question="Hidden question?", answer="B.", is_active=False
    )
    client.force_login(superuser)

    response = client.get(CHANGELIST_URL, {"is_active__exact": "0"})

    assert response.status_code == 200
    assert b"Hidden question?" in response.content
    assert b"Shown question?" not in response.content
    # The URL filter above works for any local field even without list_filter
    # (ModelAdmin.lookup_allowed), so also check the sidebar offers it.
    filter_fields = [
        getattr(spec, "field_path", None) for spec in response.context["cl"].filter_specs
    ]
    assert "is_active" in filter_fields
