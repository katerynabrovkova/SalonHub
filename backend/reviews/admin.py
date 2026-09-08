from django.contrib import admin

from core.admin import ReadOnlySalonScopedAdmin
from reviews.models import Review


@admin.register(Review)
class ReviewAdmin(ReadOnlySalonScopedAdmin):
    """
    Read-only: reviews are immutable once posted (docs/DECISIONS.md §
    Business rules, § Stage 11) — admin edit would directly violate that.
    There is no hide/moderation mechanism.
    """

    list_display = ("salon", "appointment", "customer", "specialist", "rating")
