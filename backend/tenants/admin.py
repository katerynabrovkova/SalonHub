from django.contrib import admin

from core.admin import SalonScopedAdmin
from core.i18n import resolve_translation
from tenants.models import FAQItem, Salon


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


@admin.register(FAQItem)
class FAQItemAdmin(SalonScopedAdmin):
    list_display = ("salon", "question", "ordering", "is_active")
    list_filter = ("salon", "is_active")
