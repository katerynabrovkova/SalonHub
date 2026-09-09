from django.contrib import admin

from core.admin import SalonScopedAdmin
from core.i18n import resolve_translation
from specialists.models import Specialist, SpecialistService, TimeOff, WorkingHours


@admin.register(Specialist)
class SpecialistAdmin(SalonScopedAdmin):
    list_display = ("salon", "resolved_name")

    @admin.display(description="name")
    def resolved_name(self, obj: Specialist) -> str:
        # No `?lang=` equivalent in admin — resolve like an API read with no
        # lang (docs/DECISIONS.md § "Admin display of translatable fields").
        # No admin_order_field: a {lang: string} dict is not an ORDER BY key.
        return resolve_translation(obj.name, None)


@admin.register(SpecialistService)
class SpecialistServiceAdmin(SalonScopedAdmin):
    list_display = ("salon", "specialist", "service")


@admin.register(WorkingHours)
class WorkingHoursAdmin(SalonScopedAdmin):
    list_display = ("salon", "specialist", "day_of_week", "start_time", "end_time")


@admin.register(TimeOff)
class TimeOffAdmin(SalonScopedAdmin):
    list_display = ("salon", "specialist", "start_datetime", "end_datetime", "reason")
