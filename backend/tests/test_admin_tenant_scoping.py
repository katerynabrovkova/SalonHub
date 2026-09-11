"""
Django admin's tenant-fix mechanism (core/admin.py, docs/DECISIONS.md §
Stage 3 sub-step 4 decisions). Admin requests never bind tenant context —
these prove the deliberate unscoped_objects bypass actually works, rather
than just reading correctly.
"""

import datetime as dt
import json

import pytest
from django.contrib import admin
from django.test import RequestFactory

from booking.admin import AppointmentAdmin
from booking.guest_tokens import issue_guest_token
from booking.models import Appointment
from catalog.models import Service, ServiceCategory
from core.tenancy import tenant_context
from specialists.models import Specialist
from tenants.models import Salon
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

factory = RequestFactory()


def test_admin_changelist_reaches_across_tenants(client, superuser, salon, other_salon):
    with tenant_context(salon.id):
        ServiceCategory.objects.create(
            salon=salon, name={"en": "Salon A Category", "uk": "Категорія А"}
        )
    with tenant_context(other_salon.id):
        ServiceCategory.objects.create(salon=other_salon, name={"en": "Salon B Category"})

    client.force_login(superuser)
    response = client.get("/admin/catalog/servicecategory/")

    assert response.status_code == 200
    # cross-tenant reach: both salons' rows render
    assert b"Salon A Category" in response.content
    assert b"Salon B Category" in response.content
    # the `name` column resolves to a plain string (no `?lang=` -> English),
    # not the raw {lang: value} dict — the unrequested language must not leak
    assert "Категорія А".encode() not in response.content


def test_service_changelist_name_column_resolves_not_raw_dict(
    client, superuser, salon, service_category
):
    with tenant_context(salon.id):
        Service.objects.create(
            salon=salon,
            category=service_category,
            name={"en": "Manicure", "uk": "Манікюр"},
            duration_minutes=60,
            price="500.00",
            buffer_minutes=15,
        )

    client.force_login(superuser)
    response = client.get("/admin/catalog/service/")

    assert response.status_code == 200
    assert b"Manicure" in response.content
    assert "Манікюр".encode() not in response.content


def test_specialist_changelist_name_column_resolves_not_raw_dict(client, superuser, salon):
    with tenant_context(salon.id):
        Specialist.objects.create(salon=salon, name={"en": "Jane", "uk": "Джейн"})

    client.force_login(superuser)
    response = client.get("/admin/specialists/specialist/")

    assert response.status_code == 200
    assert b"Jane" in response.content
    assert "Джейн".encode() not in response.content


def test_salon_changelist_name_column_resolves_not_raw_dict(client, superuser, salon):
    salon.name = {"en": "Bella Demo Salon", "uk": "Белла Демо Салон"}
    salon.save(update_fields=["name"])

    client.force_login(superuser)
    response = client.get("/admin/tenants/salon/")

    assert response.status_code == 200
    assert b"Bella Demo Salon" in response.content
    assert "Белла Демо Салон".encode() not in response.content


def test_salon_str_resolves_populated_translatable_name(db):
    salon = Salon.objects.create(
        name={"en": "Brows Bar"},
        slug="brows-bar",
        currency="UAH",
        contact_email="owner@brows-bar.example",
    )
    assert str(salon) == "Brows Bar"


def test_salon_str_falls_back_to_model_and_pk_when_all_languages_empty(db):
    salon = Salon.objects.create(
        name={},
        slug="empty-name-salon",
        currency="UAH",
        contact_email="owner@empty-name-salon.example",
    )
    assert str(salon) == f"Salon #{salon.pk}"


def test_str_resolves_populated_translatable_name(salon):
    with tenant_context(salon.id):
        cat = ServiceCategory.objects.create(salon=salon, name={"en": "Brows"})
        svc = Service.objects.create(
            salon=salon,
            category=cat,
            name={"en": "Lamination"},
            duration_minutes=30,
            price="100.00",
            buffer_minutes=0,
        )
        spec = Specialist.objects.create(salon=salon, name={"en": "Jane"})

    assert str(cat) == "Brows"
    assert str(svc) == "Lamination"
    assert str(spec) == "Jane"


def test_str_falls_back_to_model_and_pk_when_all_languages_empty(salon):
    with tenant_context(salon.id):
        cat = ServiceCategory.objects.create(salon=salon, name={})
        svc = Service.objects.create(
            salon=salon,
            category=cat,
            name={},
            duration_minutes=30,
            price="100.00",
            buffer_minutes=0,
        )
        spec = Specialist.objects.create(salon=salon, name={})

    assert str(cat) == f"ServiceCategory #{cat.pk}"
    assert str(svc) == f"Service #{svc.pk}"
    assert str(spec) == f"Specialist #{spec.pk}"


def test_tenant_scoped_admin_get_queryset_does_not_require_bound_tenant_context(salon, other_salon):
    with tenant_context(salon.id):
        ServiceCategory.objects.create(salon=salon, name={"en": "Cat A"})
    with tenant_context(other_salon.id):
        ServiceCategory.objects.create(salon=other_salon, name={"en": "Cat B"})

    model_admin = admin.site._registry[ServiceCategory]
    request = factory.get("/admin/catalog/servicecategory/")

    # Deliberately no tenant_context bound here — this is the point.
    qs = model_admin.get_queryset(request)

    assert qs.count() == 2


def test_appointment_admin_is_read_only():
    model_admin = admin.site._registry[Appointment]
    assert isinstance(model_admin, AppointmentAdmin)
    request = factory.get("/admin/booking/appointment/")
    assert model_admin.has_change_permission(request) is False
    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_delete_permission(request) is False


def test_service_add_form_renders_without_a_bound_tenant(client, superuser):
    """
    Without SalonScopedAdmin.formfield_for_foreignkey, this 500s: Django
    would populate the `category` dropdown from ServiceCategory.objects
    (tenant-scoped), which raises with no tenant bound in an admin request.
    """
    client.force_login(superuser)

    response = client.get("/admin/catalog/service/add/")

    assert response.status_code == 200


def test_guest_access_token_change_view_does_not_render_token_hash(
    client, superuser, salon, customer, specialist, service
):
    """
    token_hash isn't in list_display, but with no fields/exclude set,
    Django's default ModelAdmin form includes every model field on the
    change/detail page regardless — read-only (has_change_permission is
    False) doesn't mean removed. GuestAccessTokenAdmin excludes it
    explicitly; this guards against that regressing silently.
    """
    with tenant_context(salon.id):
        appointment = make_appointment(
            salon=salon,
            customer=customer,
            specialist=specialist,
            service=service,
            start=dt.datetime(2026, 12, 1, 10, 0, tzinfo=dt.UTC),
        )
        _raw_token, token_row = issue_guest_token(appointment)

    client.force_login(superuser)
    response = client.get(f"/admin/booking/guestaccesstoken/{token_row.id}/change/")

    assert response.status_code == 200
    assert token_row.token_hash not in response.content.decode()
    assert b"token_hash" not in response.content.lower()


# --- admin add POST: TenantContextMissingError on validate_unique --------
# (docs/DECISIONS.md § "Fix: TenantContextMissingError on admin save for
# TenantScopedModel" — currently only decided, not implemented. These prove
# the bug exists for every TenantScopedModel admin, not just Account, which
# accounts/admin.py's _TenantBoundModelForm already happens to fix.)


def test_service_category_admin_add_post_succeeds(client, superuser, salon):
    client.force_login(superuser)

    payload = {
        "salon": str(salon.id),
        "name": json.dumps({"en": "Nails"}),
        "ordering": "0",
        "is_active": "on",
        "_save": "Save",
    }

    response = client.post("/admin/catalog/servicecategory/add/", payload)

    assert response.status_code == 302
    with tenant_context(salon.id):
        assert ServiceCategory.objects.filter(name__en="Nails").exists()


def test_service_admin_add_post_succeeds(client, superuser, salon, service_category):
    client.force_login(superuser)

    payload = {
        "salon": str(salon.id),
        "category": str(service_category.id),
        "name": json.dumps({"en": "Manicure"}),
        "duration_minutes": "60",
        "price": "500.00",
        "buffer_minutes": "0",
        "ordering": "0",
        "is_active": "on",
        "_save": "Save",
    }

    response = client.post("/admin/catalog/service/add/", payload)

    assert response.status_code == 302
    with tenant_context(salon.id):
        assert Service.objects.filter(name__en="Manicure").exists()


def test_specialist_admin_add_post_succeeds(client, superuser, salon):
    client.force_login(superuser)

    payload = {
        "salon": str(salon.id),
        "name": json.dumps({"en": "Jane"}),
        "is_active": "on",
        "_save": "Save",
    }

    response = client.post("/admin/specialists/specialist/add/", payload)

    assert response.status_code == 302
    with tenant_context(salon.id):
        assert Specialist.objects.filter(name__en="Jane").exists()


def test_user_admin_pages_render_with_no_username_field_anywhere(client, superuser):
    """
    Confirms the admin still works after email became USERNAME_FIELD
    (docs/DECISIONS.md § Stage 3 decisions) — no page here should reference
    a `username` field that no longer exists on the model.
    """
    client.force_login(superuser)

    changelist = client.get("/admin/accounts/user/")
    change = client.get(f"/admin/accounts/user/{superuser.id}/change/")

    assert changelist.status_code == 200
    assert change.status_code == 200
    assert b"username" not in changelist.content.lower()
    assert b"username" not in change.content.lower()
