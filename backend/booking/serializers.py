from decimal import Decimal

from rest_framework import serializers

from booking.models import Appointment, AppointmentStatus
from catalog.models import Service
from payments.models import Payment, PaymentStatus
from scheduling.serializers import _OffsetRequiredDateTimeField
from specialists.models import Specialist

# amount_due_at_visit is only meaningful once a deposit has actually been
# collected against a still-honored booking (docs/DECISIONS.md § Stage 15
# planning, item 4). COMPLETED is included alongside CONFIRMED for
# consistency (a completed visit's balance is exactly as well-defined as a
# confirmed one's) even though the dashboard only renders it for CONFIRMED
# today -- the frontend's display choice, not a reason to make the backend
# value inconsistent between the two.
_AMOUNT_DUE_ELIGIBLE_STATUSES = frozenset(
    {AppointmentStatus.CONFIRMED, AppointmentStatus.COMPLETED}
)


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
    Read-only representation for AccountAppointmentListView and
    AccountAppointmentCancelView (docs/DECISIONS.md § Stage 15 planning,
    items 1 and 4). Kept as a separate class from AppointmentGuestSerializer,
    not reused, since the two endpoints have different identities/permissions
    and are free to diverge later.

    Carries the raw price/deposit snapshot fields (`service_price_at_booking`,
    `deposit_percentage_at_booking`), not a computed deposit amount: the
    rounding logic for that computation
    (payments/services.py's `_compute_deposit_amount`) is module-private
    there, deliberately not meant to be imported by another app — its
    leading underscore is the same "do not reuse this elsewhere" signal as
    booking/services.py's own private helpers. The two snapshot fields are
    enough for a dashboard to show "price" and "deposit %" per appointment
    without duplicating that rounding rule; the actual charged amount, once
    a payment exists, is already on Payment.amount via a separate endpoint.

    `payment_status`/`payment_amount`/`amount_due_at_visit` read the
    OneToOneField reverse accessor `appointment.payment`
    (payments/models.py's `Payment.appointment`), which raises
    `Payment.DoesNotExist` when no Payment row exists rather than returning
    `None` -- `_payment()` below guards every read with
    `getattr(appointment, "payment", None)`. Both value fields serialize to
    `null` (never a placeholder like `0`/`"none"`) when no Payment exists,
    matching this codebase's established null-means-absence convention
    (`Payment.refund_initiated_at`, `Specialist.photo`).

    `amount_due_at_visit` is computed here, in Python, rather than left for
    the frontend: it's a plain subtraction of two values the backend
    already rounded and stored (`service_price_at_booking`, `Payment.amount`),
    so there's no rounding-drift risk either way, but computing it
    server-side keeps the "what does this client still owe" rule in one
    place -- the same reasoning that already kept
    `_compute_deposit_amount`'s rounding logic out of the frontend
    (payments/services.py), just for a trivial case of it. The queryset this
    serializer runs against (`AccountAppointmentListView.get_queryset`) adds
    `select_related("payment")`, so these three fields cost no extra query
    per row across a list.
    """

    payment_status = serializers.SerializerMethodField()
    payment_amount = serializers.SerializerMethodField()
    amount_due_at_visit = serializers.SerializerMethodField()

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
            "service_price_at_booking",
            "deposit_percentage_at_booking",
            "payment_status",
            "payment_amount",
            "amount_due_at_visit",
        ]
        read_only_fields = fields

    def _payment(self, appointment: Appointment) -> Payment | None:
        return getattr(appointment, "payment", None)

    def get_payment_status(self, appointment: Appointment) -> str | None:
        payment = self._payment(appointment)
        return payment.status if payment is not None else None

    def get_payment_amount(self, appointment: Appointment) -> Decimal | None:
        payment = self._payment(appointment)
        return payment.amount if payment is not None else None

    def get_amount_due_at_visit(self, appointment: Appointment) -> Decimal | None:
        payment = self._payment(appointment)
        if (
            appointment.status not in _AMOUNT_DUE_ELIGIBLE_STATUSES
            or payment is None
            or payment.status != PaymentStatus.SUCCEEDED
        ):
            return None
        return appointment.service_price_at_booking - payment.amount


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
