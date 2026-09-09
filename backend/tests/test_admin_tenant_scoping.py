"""
Django admin's tenant-fix mechanism (core/admin.py, docs/DECISIONS.md §
Stage 3 sub-step 4 decisions). Admin requests never bind tenant context —
these prove the deliberate unscoped_objects bypass actually works, rather
than just reading correctly.
"""

import datetime as dt

import pytest
from django.contrib import admin
from django.test import RequestFactory

from booking.admin import AppointmentAdmin
from booking.guest_tokens import issue_guest_token
from booking.models import Appointment
from catalog.models import Service, ServiceCategory
from core.tenancy import tenant_context
from specialists.models import Specialist
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
        ServiceCategory.objects.create(salon=salon, name="Cat A")
    with tenant_context(other_salon.id):
        ServiceCategory.objects.create(salon=other_salon, name="Cat B")

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
