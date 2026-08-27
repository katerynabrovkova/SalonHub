"""
D.1 — AccountAdmin (docs/DECISIONS.md § Stage 3-R.D.1). First-admin
provisioning through Django `/admin/`: registering a tenant-scoped model
whose default manager raises without a bound tenant, plus a
password-hashing add form that guarantees the DB never stores a plaintext
literal (the security core of this step — mirrors
django.contrib.auth.forms.UserCreationForm).
"""

import pytest
from django.contrib import admin
from django.test import RequestFactory

from accounts.models import Account, AccountRole, Customer
from core.admin import SalonScopedAdmin
from core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

factory = RequestFactory()

STRONG_PASSWORD = "a-genuinely-strong-passw0rd!"


def _add_payload(salon, **overrides):
    data = {
        "salon": str(salon.id),
        "email": "new-admin@example.com",
        "role": AccountRole.ADMIN.value,
        "customer": "",
        "is_active": "on",
        "password1": STRONG_PASSWORD,
        "password2": STRONG_PASSWORD,
        "_save": "Save",
    }
    data.update(overrides)
    return data


def _account(email):
    return Account.unscoped_objects.get(email=email)


# --- 1. changelist renders across tenants, no bound tenant context ----------


def test_account_changelist_renders_with_no_bound_tenant(
    client, superuser, admin_account, other_salon_admin_account
):
    client.force_login(superuser)
    response = client.get("/admin/accounts/account/")

    assert response.status_code == 200
    assert admin_account.email.encode() in response.content
    assert other_salon_admin_account.email.encode() in response.content


# --- 2. get_queryset itself needs no bound tenant --------------------------


def test_account_changelist_get_queryset_does_not_require_bound_tenant(
    admin_account, other_salon_admin_account
):
    model_admin = admin.site._registry[Account]
    request = factory.get("/admin/accounts/account/")

    # Deliberately no tenant_context bound here — this is the point.
    assert model_admin.get_queryset(request).count() == 2


# --- 3. add form renders unbound (the customer FK dropdown) ---------------


def test_account_add_form_renders_with_no_bound_tenant(client, superuser):
    client.force_login(superuser)

    response = client.get("/admin/accounts/account/add/")

    assert response.status_code == 200


# --- 4. add POST stores a HASHED password, never the literal -------------


def test_account_add_post_stores_a_hashed_password(client, superuser, salon):
    client.force_login(superuser)

    response = client.post("/admin/accounts/account/add/", _add_payload(salon))

    assert response.status_code == 302
    account = _account("new-admin@example.com")
    assert account.check_password(STRONG_PASSWORD) is True
    assert account.password != STRONG_PASSWORD
    assert account.password.startswith("pbkdf2_")


# --- 5. selected role persists (admin AND client) ----------------------


def test_account_add_post_persists_selected_role(client, superuser, salon):
    client.force_login(superuser)

    client.post(
        "/admin/accounts/account/add/",
        _add_payload(salon, email="an-admin@example.com", role=AccountRole.ADMIN.value),
    )
    client.post(
        "/admin/accounts/account/add/",
        _add_payload(salon, email="a-client@example.com", role=AccountRole.CLIENT.value),
    )

    assert _account("an-admin@example.com").role == AccountRole.ADMIN
    assert _account("a-client@example.com").role == AccountRole.CLIENT


# --- 6. null customer allowed (first-admin, no visit history) ----------


def test_account_add_post_allows_null_customer(client, superuser, salon):
    client.force_login(superuser)

    response = client.post("/admin/accounts/account/add/", _add_payload(salon, customer=""))

    assert response.status_code == 302
    assert _account("new-admin@example.com").customer_id is None


# --- 7. customer dropdown is unscoped_objects-backed and nullable -----


def test_account_add_customer_dropdown_is_unscoped_and_nullable(superuser, customer, other_salon):
    with tenant_context(other_salon.id):
        Customer.objects.create(
            salon=other_salon, name="Bob", email="bob@example.com", phone="+10000000009"
        )

    model_admin = admin.site._registry[Account]
    request = factory.get("/admin/accounts/account/add/")
    request.user = superuser

    field = model_admin.get_form(request)().fields["customer"]

    assert field.queryset.count() == 2  # customer at `salon` + Bob at `other_salon`
    assert field.required is False


# --- 8. change form exposes NO editable password field --------------


def test_account_change_form_has_no_editable_password_field(client, superuser, admin_account):
    model_admin = admin.site._registry[Account]
    request = factory.get("/")
    request.user = superuser

    assert "password" in model_admin.get_readonly_fields(request, admin_account)

    client.force_login(superuser)
    response = client.get(f"/admin/accounts/account/{admin_account.id}/change/")

    assert response.status_code == 200
    assert b'name="password"' not in response.content
    assert b'name="password1"' not in response.content
    assert b'name="password2"' not in response.content


# --- 9. a hand-crafted plaintext POST cannot overwrite the hash -----


def test_account_change_post_cannot_overwrite_the_password_hash(client, superuser, admin_account):
    original = admin_account.password

    client.force_login(superuser)
    response = client.post(
        f"/admin/accounts/account/{admin_account.id}/change/",
        {
            "salon": str(admin_account.salon_id),
            "email": admin_account.email,
            "role": admin_account.role,
            "customer": "",
            "is_active": "on",
            "password": "totally-plaintext",  # smuggled — not a form field
            "_save": "Save",
        },
    )

    assert response.status_code in (200, 302)
    reloaded = _account(admin_account.email)
    assert reloaded.password == original
    assert reloaded.check_password("totally-plaintext") is False


# --- 10. mismatched password confirmation is rejected --------------


def test_account_add_post_rejects_mismatched_password_confirmation(client, superuser, salon):
    client.force_login(superuser)

    response = client.post(
        "/admin/accounts/account/add/",
        _add_payload(salon, password2="a-different-passw0rd!"),
    )

    assert response.status_code == 200
    assert not Account.unscoped_objects.filter(email="new-admin@example.com").exists()


# --- 11. a weak password is rejected (parity with the API register path) ---


def test_account_add_post_rejects_a_weak_password(client, superuser, salon):
    client.force_login(superuser)

    response = client.post(
        "/admin/accounts/account/add/",
        _add_payload(salon, password1="123", password2="123"),
    )

    assert response.status_code == 200
    assert not Account.unscoped_objects.filter(email="new-admin@example.com").exists()


# --- 12. email is lowercased (parity with create_account) --------


def test_account_add_post_lowercases_email(client, superuser, salon):
    client.force_login(superuser)

    client.post("/admin/accounts/account/add/", _add_payload(salon, email="Admin@Example.COM"))

    assert _account("admin@example.com")
    assert not Account.unscoped_objects.filter(email="Admin@Example.COM").exists()


# --- 13. add IS enabled (contrast with UserAdmin, where it is disabled) ---


def test_account_admin_add_is_enabled(superuser):
    model_admin = admin.site._registry[Account]
    request = factory.get("/admin/accounts/account/add/")
    request.user = superuser

    assert model_admin.has_add_permission(request) is True


# --- 14. AccountAdmin is SalonScopedAdmin-based (structural lock) ---


def test_account_admin_uses_salon_scoped_base():
    assert isinstance(admin.site._registry[Account], SalonScopedAdmin)
