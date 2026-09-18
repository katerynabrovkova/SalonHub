from rest_framework import serializers

from booking.models import Appointment
from catalog.models import Service
from scheduling.serializers import _OffsetRequiredDateTimeField
from specialists.models import Specialist


class AppointmentGuestSerializer(serializers.ModelSerializer):
    """Read-only representation for the guest view/cancel endpoints (booking/views.py)."""

    class Meta:
        model = Appointment
        fields = [
            "id",
            "status",
            "start_datetime",
            "end_datetime",
            "specialist",
            "service",
            "cancelled_at",
            "cancelled_by",
            "cancellation_reason",
        ]
        read_only_fields = fields


class AppointmentAccountSerializer(serializers.ModelSerializer):
    """
    Read-only representation for AccountAppointmentListView (docs/DECISIONS.md
    § Stage 15 planning, item 1). Same field set as AppointmentGuestSerializer
    — kept as a separate class rather than reused, since the two endpoints
    have different identities/permissions and are free to diverge later.
    """

    class Meta:
        model = Appointment
        fields = [
            "id",
            "status",
            "start_datetime",
            "end_datetime",
            "specialist",
            "service",
            "cancelled_at",
            "cancelled_by",
            "cancellation_reason",
        ]
        read_only_fields = fields


class SpecialistOrAnyField(serializers.PrimaryKeyRelatedField):
    """
    Accepts either a real specialist pk or the literal string "any"
    (docs/DECISIONS.md § Stage 14 implementation decisions, "'Any
    specialist' is encoded as the literal URL value specialist=any").
    Anything else falls through to PrimaryKeyRelatedField's own pk
    validation/error — this is exact acceptance of "any" or a valid pk, not
    a loosened "accept anything" field.
    """

    def to_internal_value(self, data: object) -> "Specialist | str":
        if data == "any":
            return "any"
        return super().to_internal_value(data)


class GuestBookingRequestSerializer(serializers.Serializer):
    """
    Input validation for the guest booking POST endpoint (docs/DECISIONS.md §
    Stage 7.D decisions). A plain Serializer, not a ModelSerializer — the
    fields span two future model rows (Customer, Appointment) that don't
    exist yet at validation time, so there's no single instance to bind to.

    `specialist`/`service` are PrimaryKeyRelatedFields rebound in __init__ to
    the tenant-scoped querysets, the same pattern
    scheduling/serializers.py's AvailabilityQuerySerializer uses — the
    class-body placeholder queryset is a harmless, always-empty,
    non-tenant-scoped one. `start_datetime` reuses
    scheduling/serializers.py's `_OffsetRequiredDateTimeField` rather than
    duplicating the naive-datetime rejection. `specialist` uses
    SpecialistOrAnyField, above, so `specialist=any` (docs/DECISIONS.md §
    Stage 14 scope revision) validates alongside a real pk.
    """

    specialist = SpecialistOrAnyField(queryset=Specialist.unscoped_objects.none())
    service = serializers.PrimaryKeyRelatedField(queryset=Service.unscoped_objects.none())
    start_datetime = _OffsetRequiredDateTimeField()
    customer_name = serializers.CharField(max_length=255)
    customer_email = serializers.EmailField()
    customer_phone = serializers.CharField(max_length=32)

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.fields["specialist"].queryset = Specialist.objects.all()
        self.fields["service"].queryset = Service.objects.all()


class AppointmentCreatedSerializer(serializers.ModelSerializer):
    """
    Response shape for the created appointment, nested under the "appointment"
    key in the 201 body (docs/DECISIONS.md § Stage 7.D decisions, "201
    response envelope"). Deliberately narrow — price/deposit snapshots are
    Stage 8.
    """

    class Meta:
        model = Appointment
        fields = ["id", "status", "start_datetime", "end_datetime"]
        read_only_fields = fields
