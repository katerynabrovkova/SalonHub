"""
Base ModelAdmin classes for the Django admin site (docs/ARCHITECTURE.md § 5).

This is a deliberate, blanket cross-tenant tool for platform operators
(Django is_staff/is_superuser — docs/ARCHITECTURE.md § 4's "Platform
superuser" role), not a per-salon back office — every salon's data is
visible together here by design. The real per-salon back office is Stages
18-21, built on the JWT + IsSalonStaff API surface, not this.

Admin requests never bind tenant context: TenantResolutionMiddleware only
matches /api/v1/salons/<slug>/... paths, never /admin/.... So every
TenantScopedModel's default manager (`objects`) would raise
TenantContextMissingError the moment an admin page queried it. The fix is
`Model.unscoped_objects`, exactly as docs/ARCHITECTURE.md § 5 already names
it as the intended admin bypass.
"""

from typing import Any, ClassVar

from django import forms
from django.contrib import admin
from django.db.models import ForeignKey
from django.http import HttpRequest

from core.models import TenantScopedModel
from core.tenancy import tenant_context


class ReadOnlyAdminMixin:
    """
    Blocks all mutation regardless of superuser status. Deliberately leaves
    has_view_permission untouched — Django's default already grants view
    access to anyone with view *or* change permission (superusers have
    both), so list/detail pages stay visible; only add/change/delete are
    forced closed.
    """

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


class TenantBoundModelForm(forms.ModelForm):
    """
    Binds tenant context from the submitted (or existing) `salon` field for
    the duration of `_post_clean`, so model constraint validation — which
    runs through the tenant-scoped `_default_manager` regardless of what
    queryset the enclosing ModelAdmin uses for display (e.g. Account's
    `(salon, email)` UniqueConstraint, or every TenantScopedModel's base
    `(id, salon)` UniqueConstraint) — does not raise
    TenantContextMissingError inside an admin request, which never binds a
    tenant (TenantResolutionMiddleware only matches /api/v1/salons/<slug>/...
    paths, never /admin/...). The DB constraint stays the real guarantee;
    this just lets the form-level check run correctly scoped instead of
    blowing up.

    Shared base for every SalonScopedAdmin subclass (docs/DECISIONS.md §
    "Fix: TenantContextMissingError on admin save for TenantScopedModel") —
    originally local to accounts/admin.py's Account-only fix, promoted here
    once the same bug surfaced for ServiceCategory/Service/Specialist too.
    """

    def _post_clean(self) -> None:
        salon = self.cleaned_data.get("salon")
        salon_id = salon.id if salon is not None else self.instance.salon_id
        if salon_id is not None:
            with tenant_context(salon_id):
                super()._post_clean()  # type: ignore[misc]  # private Django API, untyped in stubs
        else:
            super()._post_clean()  # type: ignore[misc]  # private Django API, untyped in stubs


class SalonScopedAdmin(admin.ModelAdmin):
    """
    Not generic over the model (unlike core.models.TenantScopedManager):
    admin.ModelAdmin has no runtime __class_getitem__, unlike Django's own
    QuerySet/Manager — subscripting it (ModelAdmin[SomeModel]) blows up at
    import time during admin autodiscovery. self.model is therefore typed
    loosely by django-stubs; the two unscoped_objects accesses below are
    the only lines that need a narrow ignore for it.
    """

    list_filter: ClassVar[tuple[str, ...]] = ("salon",)
    form = TenantBoundModelForm

    def get_queryset(self, request: HttpRequest) -> Any:
        # unscoped_objects, not objects — see module docstring. Mirrors
        # ModelAdmin.get_queryset's own ordering-preservation logic, just
        # swapping which manager the queryset comes from.
        qs = self.model.unscoped_objects.get_queryset()  # type: ignore[attr-defined]
        ordering = self.get_ordering(request)
        if ordering:
            qs = qs.order_by(*ordering)
        return qs

    def formfield_for_foreignkey(
        self, db_field: ForeignKey, request: HttpRequest, **kwargs: Any
    ) -> Any:
        # Without this, opening the add/change form for any editable model
        # with a FK to another TenantScopedModel (e.g. Service -> Category)
        # 500s immediately: Django populates the dropdown widget from the
        # related model's default manager (objects), which raises with no
        # tenant bound — same root cause as get_queryset above, but for a
        # different model than the one being administered.
        related_model = db_field.related_model
        if (
            isinstance(related_model, type)
            and issubclass(related_model, TenantScopedModel)
            and "queryset" not in kwargs
        ):
            kwargs["queryset"] = related_model.unscoped_objects.get_queryset()  # type: ignore[attr-defined]
            # Setting kwargs["queryset"] above isn't sufficient on its own:
            # Django's ForeignKey.formfield() unconditionally evaluates
            # `related_model._default_manager.using(...)` while building
            # its *own* defaults dict, before it ever applies our
            # kwargs["queryset"] override — so the tenant-scoped manager
            # still gets touched, and still raises, regardless of what we
            # pass in. The salon id here is a throwaway sentinel: that
            # eagerly-computed queryset is immediately discarded in favor
            # of ours and never executed, so which id we bind doesn't
            # matter — only that *some* tenant is bound so the manager
            # doesn't raise.
            with tenant_context(-1):
                return super().formfield_for_foreignkey(db_field, request, **kwargs)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class ReadOnlySalonScopedAdmin(ReadOnlyAdminMixin, SalonScopedAdmin):
    pass
