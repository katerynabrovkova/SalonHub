from django.contrib import admin
from django.http import HttpRequest

from accounts.models import Customer, User
from core.admin import SalonScopedAdmin


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    """
    Bespoke, not django.contrib.auth.admin.UserAdmin — that base class's
    stock forms/fieldsets assume a `username` field, which this project's
    User doesn't have (docs/DECISIONS.md § Stage 3 decisions). Accounts are
    created via /api/v1/auth/register/ or `createsuperuser`, never here:
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
