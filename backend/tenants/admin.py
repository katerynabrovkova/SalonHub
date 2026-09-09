from django.contrib import admin

from core.i18n import resolve_translation
from tenants.models import Salon


@admin.register(Salon)
class SalonAdmin(admin.ModelAdmin):
    list_display = ("resolved_name", "slug", "is_active", "timezone")
    list_filter = ("is_active",)

    @admin.display(description="name")
    def resolved_name(self, obj: Salon) -> str:
        # No `?lang=` equivalent in admin — resolve like an API read with no
        # lang (docs/DECISIONS.md § "Admin display of translatable fields").
        # No admin_order_field: a {lang: string} dict is not an ORDER BY key.
        return resolve_translation(obj.name, None)
