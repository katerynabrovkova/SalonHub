from django.contrib import admin

from booking.models import Appointment, GuestAccessToken
from core.admin import ReadOnlySalonScopedAdmin


@admin.register(Appointment)
class AppointmentAdmin(ReadOnlySalonScopedAdmin):
    """
    Read-only: status, cancelled_by, and the booking-time price/deposit
    snapshots are all governed by service-layer logic that doesn't exist
    yet (booking core is Stage 7, payments Stage 8) — hand-editing any of
    them here could corrupt invariants that layer will assume hold
    (docs/DECISIONS.md § Stage 3 sub-step 4 decisions).
    """

    list_display = (
        "salon",
        "customer",
        "specialist",
        "service",
        "start_datetime",
        "status",
        "cancelled_by",
        "source",
    )
    list_filter = (*ReadOnlySalonScopedAdmin.list_filter, "status", "source")


@admin.register(GuestAccessToken)
class GuestAccessTokenAdmin(ReadOnlySalonScopedAdmin):
    """
    Read-only: rows are only ever meant to be created by
    booking.guest_tokens.issue_guest_token — an admin-typed token_hash
    wouldn't correspond to any real signed token (docs/DECISIONS.md §
    Stage 3 sub-step 4 decisions).

    token_hash is excluded from the form, not just list_display: with no
    fields/exclude set, Django's default ModelAdmin renders every model
    field on the change/detail page regardless of list_display, and
    read-only (has_change_permission False) only disables editing — it
    doesn't remove the field from the page.
    """

    list_display = ("salon", "appointment", "expires_at", "cancelled_via_token_at")
    exclude = ("token_hash",)
