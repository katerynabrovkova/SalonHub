"""
RED phase for Stage 3-R.A — `Account` doesn't exist yet (docs/DECISIONS.md §
Stage 3-R decisions, "Account model, settled shape"). Collection is expected
to fail with an ImportError until the model + `AccountManager.create_account`
land; that failure is the correct red, not a bug in this file.

`create_account` (not the inherited `.create()`) is the pinned creation path.
Its email normalization is stricter than `UserManager._create_user`'s: it
lowercases the WHOLE email (local part + domain), not just the domain, via
`self.normalize_email(email).lower()` — the product decision is that
"Alice@example.com" and "alice@example.com" are the same email at one salon,
so they must collide on (salon, email). Django's stock domain-only lowercase
would leave that isolation gap open. It must also refuse to persist a row
with no usable password.
"""

import pytest
from django.db import IntegrityError, transaction

from accounts.models import Account, AccountRole
from core.tenancy import TenantContextMissingError, tenant_context

PASSWORD = "hunter2"


# --- Creation / basic shape -------------------------------------------------


@pytest.mark.django_db
def test_account_can_be_created_with_required_fields(salon) -> None:
    with tenant_context(salon.id):
        account = Account.objects.create_account(
            salon=salon, email="alice@example.com", password=PASSWORD
        )

    assert account.pk is not None
    assert account.salon_id == salon.id
    assert account.email == "alice@example.com"


def test_create_account_requires_salon_argument() -> None:
    # `salon` is a required keyword argument with no default on
    # `create_account` — omitting it is a caller error (TypeError), not a
    # runtime validation outcome. Needs no tenant context or DB access: it
    # never gets far enough to touch either.
    with pytest.raises(TypeError):
        Account.objects.create_account(email="alice@example.com", password=PASSWORD)


@pytest.mark.django_db
def test_email_is_normalized_on_create(salon) -> None:
    # Whitespace stripped (via normalize_email) AND fully lowercased — local
    # part included, not just the domain.
    with tenant_context(salon.id):
        account = Account.objects.create_account(
            salon=salon, email=" Alice@EXAMPLE.COM ", password=PASSWORD
        )

    assert account.email == "alice@example.com"


@pytest.mark.django_db
def test_case_differing_emails_collide_same_salon(salon) -> None:
    # The isolation-critical case: full lowercasing means these two inputs
    # are the SAME email at this salon and must collide on the unique
    # constraint. test_email_is_normalized_on_create only proves single-row
    # storage is lowercased; this proves the collision the normalization
    # exists to guarantee.
    with tenant_context(salon.id):
        Account.objects.create_account(salon=salon, email="Alice@example.com", password=PASSWORD)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Account.objects.create_account(
                    salon=salon, email="alice@example.com", password=PASSWORD
                )


@pytest.mark.django_db
def test_account_requires_a_password(salon) -> None:
    with tenant_context(salon.id):
        with pytest.raises(ValueError):
            Account.objects.create_account(salon=salon, email="alice@example.com")
        assert not Account.objects.filter(email="alice@example.com").exists()

        with pytest.raises(ValueError):
            Account.objects.create_account(salon=salon, email="alice@example.com", password="")
        assert not Account.objects.filter(email="alice@example.com").exists()


# --- (salon, email) uniqueness / isolation ----------------------------------


@pytest.mark.django_db
def test_duplicate_email_same_salon_raises(salon) -> None:
    with tenant_context(salon.id):
        Account.objects.create_account(salon=salon, email="alice@example.com", password=PASSWORD)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Account.objects.create_account(
                    salon=salon, email="alice@example.com", password=PASSWORD
                )


@pytest.mark.django_db
def test_same_email_different_salons_allowed(salon, other_salon) -> None:
    with tenant_context(salon.id):
        Account.objects.create_account(salon=salon, email="alice@example.com", password=PASSWORD)
    with tenant_context(other_salon.id):
        Account.objects.create_account(
            salon=other_salon, email="alice@example.com", password=PASSWORD
        )

    assert Account.unscoped_objects.filter(email="alice@example.com").count() == 2


# --- Password hashing --------------------------------------------------------


@pytest.mark.django_db
def test_password_is_hashed_not_plaintext(salon) -> None:
    with tenant_context(salon.id):
        account = Account.objects.create_account(
            salon=salon, email="alice@example.com", password=PASSWORD
        )

    assert account.password != PASSWORD


@pytest.mark.django_db
def test_check_password_round_trip(salon) -> None:
    with tenant_context(salon.id):
        account = Account.objects.create_account(
            salon=salon, email="alice@example.com", password=PASSWORD
        )

    assert account.check_password(PASSWORD) is True
    assert account.check_password("wrong") is False


# --- Role ---------------------------------------------------------------------


@pytest.mark.django_db
def test_role_defaults_to_client(salon) -> None:
    with tenant_context(salon.id):
        account = Account.objects.create_account(
            salon=salon, email="alice@example.com", password=PASSWORD
        )

    assert account.role == AccountRole.CLIENT


@pytest.mark.django_db
def test_role_can_be_set_to_admin_explicitly(salon) -> None:
    with tenant_context(salon.id):
        account = Account.objects.create_account(
            salon=salon, email="admin@example.com", password=PASSWORD, role=AccountRole.ADMIN
        )

    assert account.role == AccountRole.ADMIN


# --- `customer` link ----------------------------------------------------------


@pytest.mark.django_db
def test_customer_link_is_nullable(salon) -> None:
    with tenant_context(salon.id):
        account = Account.objects.create_account(
            salon=salon, email="admin@example.com", password=PASSWORD, role=AccountRole.ADMIN
        )

    assert account.customer is None


@pytest.mark.django_db
def test_customer_onetoone_rejects_second_account(salon, customer) -> None:
    with tenant_context(salon.id):
        Account.objects.create_account(
            salon=salon, email="alice@example.com", password=PASSWORD, customer=customer
        )
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Account.objects.create_account(
                    salon=salon,
                    email="alice-two@example.com",
                    password=PASSWORD,
                    customer=customer,
                )


@pytest.mark.django_db
def test_deleting_linked_customer_sets_account_customer_null(salon, customer) -> None:
    with tenant_context(salon.id):
        account = Account.objects.create_account(
            salon=salon, email="alice@example.com", password=PASSWORD, customer=customer
        )
        customer.delete()
        account.refresh_from_db()

    assert account.customer_id is None


# --- Other field defaults ------------------------------------------------------


@pytest.mark.django_db
def test_is_active_defaults_true(salon) -> None:
    with tenant_context(salon.id):
        account = Account.objects.create_account(
            salon=salon, email="alice@example.com", password=PASSWORD
        )

    assert account.is_active is True


@pytest.mark.django_db
def test_email_verified_at_defaults_null(salon) -> None:
    with tenant_context(salon.id):
        account = Account.objects.create_account(
            salon=salon, email="alice@example.com", password=PASSWORD
        )

    assert account.email_verified_at is None


# --- Auth-machinery duck-typing -------------------------------------------------


def test_username_field_is_email() -> None:
    assert Account.USERNAME_FIELD == "email"


def test_get_email_field_name_returns_email() -> None:
    assert Account.get_email_field_name() == "email"


# --- Tenant scoping --------------------------------------------------------------


@pytest.mark.django_db
def test_account_objects_raises_without_tenant_context() -> None:
    with pytest.raises(TenantContextMissingError):
        list(Account.objects.all())


@pytest.mark.django_db
def test_account_objects_scoped_to_bound_tenant(salon, other_salon) -> None:
    with tenant_context(salon.id):
        Account.objects.create_account(salon=salon, email="alice@example.com", password=PASSWORD)
    with tenant_context(other_salon.id):
        Account.objects.create_account(
            salon=other_salon, email="bob@example.com", password=PASSWORD
        )

    with tenant_context(salon.id):
        emails = list(Account.objects.values_list("email", flat=True))
    assert emails == ["alice@example.com"]


@pytest.mark.django_db
def test_account_unscoped_objects_bypasses_tenant_filter(salon, other_salon) -> None:
    with tenant_context(salon.id):
        Account.objects.create_account(salon=salon, email="alice@example.com", password=PASSWORD)
    with tenant_context(other_salon.id):
        Account.objects.create_account(
            salon=other_salon, email="bob@example.com", password=PASSWORD
        )

    emails = set(Account.unscoped_objects.values_list("email", flat=True))
    assert emails == {"alice@example.com", "bob@example.com"}


# --- Constraints existence ----------------------------------------------------


def test_id_salon_uniqueness_constraint_exists() -> None:
    expected_name = f"{Account._meta.app_label}_{Account._meta.model_name}_id_salon_uniq"
    constraint_names = {c.name for c in Account._meta.constraints}
    assert expected_name in constraint_names
