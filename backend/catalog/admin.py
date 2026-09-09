from django.contrib import admin

from catalog.models import Service, ServiceCategory
from core.admin import SalonScopedAdmin
from core.i18n import resolve_translation


@admin.register(ServiceCategory)
class ServiceCategoryAdmin(SalonScopedAdmin):
    list_display = ("salon", "resolved_name", "ordering")

    @admin.display(description="name")
    def resolved_name(self, obj: ServiceCategory) -> str:
        # No `?lang=` equivalent in admin — resolve like an API read with no
        # lang (docs/DECISIONS.md § "Admin display of translatable fields").
        # No admin_order_field: a {lang: string} dict is not an ORDER BY key.
        return resolve_translation(obj.name, None)


@admin.register(Service)
class ServiceAdmin(SalonScopedAdmin):
    list_display = (
        "salon",
        "resolved_name",
        "category",
        "duration_minutes",
        "price",
        "buffer_minutes",
    )

    @admin.display(description="name")
    def resolved_name(self, obj: Service) -> str:
        return resolve_translation(obj.name, None)
