from typing import Any

from django import forms
from django.contrib import admin
from django.contrib.auth.password_validation import validate_password
from django.http import HttpRequest

from accounts.models import Account, Customer, User
from core.admin import SalonScopedAdmin
from core.tenancy import tenant_context


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    """
    Bespoke, not django.contrib.auth.admin.UserAdmin — that base class's
    stock forms/fieldsets assume a `username` field, which this project's
    User doesn't have (docs/DECISIONS.md § Stage 3 decisions). Platform
    `User` accounts are created via `createsuperuser` or the Django shell,
    never here (`/api/v1/auth/register/` was removed in Stage 3-R.D.2):
    add is disabled, and `password` is read-only rather than editable —
    Django's default form widget for a plain CharField would let an
    operator overwrite it with a literal string instead of hashing it, a
    well-known admin footgun this sidesteps entirely rather than building a
    full custom creation form for this narrow sub-step.
    """

    list_display = ("email", "is_staff", "is_superuser", "is_active", "email_verified_at")
    list_filter = ("is_staff", "is_superuser", "is_active")
    search_fields = ("email",)
    readonly_fields = ("password", "email_verified_at", "last_login", "date_joined")
    fields = (
        "email",
        "password",
        "is_staff",
        "is_superuser",
        "is_active",
        "email_verified_at",
        "last_login",
        "date_joined",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False


@admin.register(Customer)
class CustomerAdmin(SalonScopedAdmin):
    list_display = ("salon", "name", "email", "phone", "user")


class _TenantBoundModelForm(forms.ModelForm):
    """
    Binds tenant context from the submitted (or existing) `salon` for the
    duration of `_post_clean`, so model constraint validation — which runs
    through the tenant-scoped `_default_manager` (e.g. Account's
    `(salon, email)` UniqueConstraint) — does not raise
    TenantContextMissingError inside an admin request, which never binds a
    tenant. The DB constraint stays the real guarantee; this just lets the
    form-level check run correctly scoped instead of blowing up.
    """

    def _post_clean(self) -> None:
        salon = self.cleaned_data.get("salon")
        salon_id = salon.id if salon is not None else self.instance.salon_id
        if salon_id is not None:
            with tenant_context(salon_id):
                super()._post_clean()  # type: ignore[misc]  # private Django API, untyped in stubs
        else:
            super()._post_clean()  # type: ignore[misc]  # private Django API, untyped in stubs


class AccountAdminAddForm(_TenantBoundModelForm):
    """
    First-admin provisioning form (docs/DECISIONS.md § Stage 3-R.D.1).
    Mirrors django.contrib.auth.forms.UserCreationForm: `password1` /
    `password2` are declared form fields, deliberately NOT in Meta.fields,
    so ModelForm's construct_instance never writes the raw value onto
    `Account.password`. The only assignment to `account.password` is
    `set_password()` in save() below — that is what guarantees the row
    stores a hash, never the literal.

    Deliberately does not route through AccountManager.create_account: the
    admin add lifecycle needs save(commit=False) semantics and instance/pk
    propagation into LogEntry / the post-add redirect, and Django's own
    UserAdmin hashes in the form rather than the manager. Parity with
    create_account (full-lowercase email, validate_password) is kept
    explicitly here instead.

    `customer` is a declared field, not a Meta.fields entry: the metaclass
    would otherwise build it from Customer._default_manager (tenant-scoped)
    at class-definition time and raise TenantContextMissingError — the same
    root cause core.admin.SalonScopedAdmin.formfield_for_foreignkey works
    around for admin-built forms, which does not reach a standalone
    ModelForm subclass. It is persisted explicitly in save().
    """

    password1 = forms.CharField(
        label="Password", widget=forms.PasswordInput, validators=[validate_password]
    )
    password2 = forms.CharField(label="Password confirmation", widget=forms.PasswordInput)
    customer = forms.ModelChoiceField(queryset=Customer.unscoped_objects.all(), required=False)

    class Meta:
        model = Account
        fields = ("salon", "email", "role", "is_active")

    def clean_email(self) -> str:
        # Full lowercase, matching AccountManager.create_account so an
        # admin-created and an API-registered account collide identically
        # at (salon, email).
        return self.cleaned_data["email"].strip().lower()

    def clean_password2(self) -> Any:
        password1 = self.cleaned_data.get("password1")
        password2 = self.cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError("The two password fields didn't match.")
        return password2

    def save(self, commit: bool = True) -> Account:
        account = super().save(commit=False)
        account.set_password(self.cleaned_data["password1"])
        account.customer = self.cleaned_data.get("customer")
        if commit:
            account.save()
        return account


@admin.register(Account)
class AccountAdmin(SalonScopedAdmin):
    """
    Per-salon login (docs/DECISIONS.md § Stage 3-R.D.1). SalonScopedAdmin,
    not plain ModelAdmin: `Account.objects` (AccountManager) raises
    TenantContextMissingError with no tenant bound, which every admin
    request is — the base class routes the changelist and FK dropdowns
    through `unscoped_objects` instead.

    Add is enabled (unlike UserAdmin): creating a salon's first admin
    Account here is the whole point of this step. `password` is read-only
    on the change form, so no admin path can overwrite a stored hash with
    a plaintext literal; the add form (AccountAdminAddForm) is the only
    creation path and always hashes.
    """

    add_form = AccountAdminAddForm
    form = _TenantBoundModelForm

    list_display = ("email", "salon", "role", "is_active", "email_verified_at")
    list_filter = ("salon", "role", "is_active")
    search_fields = ("email",)
    readonly_fields = ("password", "last_login", "email_verified_at", "created_at", "updated_at")

    add_fieldsets = (
        (
            None,
            {
                "fields": (
                    "salon",
                    "email",
                    "role",
                    "customer",
                    "is_active",
                    "password1",
                    "password2",
                )
            },
        ),
    )
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "salon",
                    "email",
                    "role",
                    "customer",
                    "is_active",
                    "email_verified_at",
                    "password",
                    "last_login",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    def get_form(
        self,
        request: HttpRequest,
        obj: Account | None = None,
        change: bool = False,
        **kwargs: Any,
    ) -> Any:
        if obj is None:
            kwargs["form"] = self.add_form
        return super().get_form(request, obj, change=change, **kwargs)

    def get_fieldsets(self, request: HttpRequest, obj: Account | None = None) -> Any:
        if obj is None:
            return self.add_fieldsets
        return super().get_fieldsets(request, obj)
