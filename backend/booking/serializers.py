import datetime as dt
from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo

from rest_framework import serializers

from booking.models import (
    ACTIVE_APPOINTMENT_STATUSES,
    Appointment,
    AppointmentSource,
    AppointmentStatus,
)
from booking.services import refund_deadline
from catalog.models import Service
from core.i18n import resolve_translation
from payments.models import Payment, PaymentStatus
from payments.services import compute_deposit_amount
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


class SalonLocalDateTimeField(serializers.Field):
    """
    A read-only appointment time as ISO 8601 in the appointment's own salon
    time zone, e.g. "2026-09-21T12:15:00+03:00", never UTC "Z"
    (docs/DECISIONS.md § "Appointment times in the salon's time zone",
    point 1). The one field every appointment serializer uses for its
    display times, so they cannot drift apart. Each value is converted on
    its own, so a start before a DST switch and an end after it carry
    different offsets. Only the representation changes: the string parses
    back to the stored instant.

    The zone comes from `instance.salon`, the row's own salon, never the
    request's. A list view must `select_related("salon")` so this costs no
    query per row. `value_from`, when given, computes the datetime from the
    instance instead of reading `source` (for refund_deadline, which is not
    a model field); a `None` result renders as null.
    """

    def __init__(
        self,
        value_from: Callable[[Appointment], dt.datetime | None] | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs["read_only"] = True
        self._value_from = value_from
        super().__init__(**kwargs)

    def get_attribute(self, instance: Appointment) -> dt.datetime | None:
        if self._value_from is not None:
            value: dt.datetime | None = self._value_from(instance)
        else:
            value = super().get_attribute(instance)
        if value is None:
            return None
        return value.astimezone(ZoneInfo(instance.salon.timezone))

    def to_representation(self, value: dt.datetime) -> str:
        return value.isoformat()


class AppointmentGuestSerializer(serializers.ModelSerializer):
    """Read-only representation for the guest view/cancel endpoints (booking/views.py)."""

    start_datetime = SalonLocalDateTimeField()
    end_datetime = SalonLocalDateTimeField()

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


class AppointmentSpecialistSerializer(serializers.ModelSerializer):
    """
    The `specialist` block on AppointmentAccountSerializer, below --
    `{id, name}` with `name` resolved for the request's `?lang=`. Mirrors
    `reviews.serializers.ReviewSpecialistSerializer`'s identical shape/
    resolution, not reused directly: booking does not depend on reviews (the
    reverse dependency already exists -- reviews imports `booking.models`),
    and this app's own established convention is a separate serializer per
    read surface even with overlapping shape (AppointmentAccountSerializer's
    own docstring, "kept as a separate class... free to diverge later").
    """

    name = serializers.SerializerMethodField()

    class Meta:
        model = Specialist
        fields = ["id", "name"]
        read_only_fields = fields

    def get_name(self, obj: Specialist) -> str:
        request = self.context.get("request")
        requested_lang = request.query_params.get("lang") if request is not None else None
        return resolve_translation(obj.name, requested_lang)


class AppointmentServiceSerializer(serializers.ModelSerializer):
    """The `service` block on AppointmentAccountSerializer -- see
    AppointmentSpecialistSerializer's docstring, same reasoning/shape."""

    name = serializers.SerializerMethodField()

    class Meta:
        model = Service
        fields = ["id", "name"]
        read_only_fields = fields

    def get_name(self, obj: Service) -> str:
        request = self.context.get("request")
        requested_lang = request.query_params.get("lang") if request is not None else None
        return resolve_translation(obj.name, requested_lang)


def _refund_deadline_or_none(appointment: Appointment) -> dt.datetime | None:
    """
    When a customer cancellation stops being refund-eligible
    (docs/DECISIONS.md § Stage 15 planning, item 14 design details), for
    AppointmentAccountSerializer's `refund_deadline`. A deadline, not a
    boolean: the dashboard compares it with the clock at click time, so a
    page left open across the boundary still words its confirm dialog
    correctly. Derived from `booking.services.refund_deadline`, the same rule
    `cancel_appointment` evaluates at cancel time, which remains the final
    decision.

    None whenever a customer cancellation could refund nothing: no SUCCEEDED
    payment, or an appointment that can no longer be cancelled (anything
    outside ACTIVE_APPOINTMENT_STATUSES, e.g. CANCELLED with the deposit
    withheld). Rendered through SalonLocalDateTimeField, like
    `start_datetime`, so it carries the salon's offset too.
    """
    payment: Payment | None = getattr(appointment, "payment", None)
    if (
        appointment.status not in ACTIVE_APPOINTMENT_STATUSES
        or payment is None
        or payment.status != PaymentStatus.SUCCEEDED
    ):
        return None
    return refund_deadline(appointment.start_datetime)


class AppointmentAccountSerializer(serializers.ModelSerializer):
    """
    Read-only representation for AccountAppointmentListView and
    AccountAppointmentCancelView (docs/DECISIONS.md § Stage 15 planning,
    items 1 and 4). Kept as a separate class from AppointmentGuestSerializer,
    not reused, since the two endpoints have different identities/permissions
    and are free to diverge later.

    Carries the raw price/deposit snapshot fields (`service_price_at_booking`,
    `deposit_percentage_at_booking`) plus `deposit_amount` and `currency`
    (docs/DECISIONS.md § "Price and prepayment on dashboard cards", point
    1). `deposit_amount` is `Payment.amount` when a Payment exists,
    otherwise `payments.services.compute_deposit_amount` -- the same helper
    `initiate_payment` charges with, made public for this so the rounding
    rule exists once -- and `null` whenever the deposit is 0 (a 0%
    percentage, a deposit that rounds to 0, or a 0.00 Payment). `currency`
    is `Payment.currency` when a Payment exists, otherwise `Salon.currency`:
    a historic payment keeps the currency it was actually paid in.

    `deposit_outcome` states what happened to a paid deposit, so the
    frontend never infers it from `payment_status` (same entry, point 1):
    "refunded" for a REFUNDED Payment; "refund_processing" for
    REFUND_PENDING, or SUCCEEDED with `Payment.refund_due`; "withheld" for
    SUCCEEDED without `refund_due` on a CANCELLED appointment; `null`
    otherwise. `refund_due` is the persisted refund decision, never
    recomputed from the cancellation rules here (§ "Persisted refund
    decision (`Payment.refund_due`)").

    `payment_status`/`payment_amount`/`amount_due_at_visit` read the
    OneToOneField reverse accessor `appointment.payment`
    (payments/models.py's `Payment.appointment`), which raises
    `Payment.DoesNotExist` when no Payment row exists rather than returning
    `None` -- `_payment()` below guards every read with
    `getattr(appointment, "payment", None)`. Both value fields serialize to
    `null` (never a placeholder like `0`/`"none"`) when no Payment exists,
    matching this codebase's established null-means-absence convention
    (`Payment.refund_initiated_at`, `Specialist.photo`).

    `payment_amount`/`amount_due_at_visit` return `str(...)` of their
    `Decimal`, not a bare `Decimal`: a `SerializerMethodField` has no
    `to_representation` coercion of its own, so an un-stringified `Decimal`
    renders as a bare JSON number (verified:
    `JSONRenderer().render({"x": Decimal("12.34")})` -> `b'{"x":12.34}'`) --
    inconsistent with `service_price_at_booking`/`deposit_percentage_at_booking`
    above, real `DecimalField`s whose own `to_representation` always
    produces a string (`"12.34"`). `str()` here makes every money field on
    this serializer serialize the same way.

    `amount_due_at_visit` is computed here, in Python, rather than left for
    the frontend: it's a plain subtraction of two values the backend
    already rounded and stored (`service_price_at_booking`, `Payment.amount`),
    so there's no rounding-drift risk either way, but computing it
    server-side keeps the "what does this client still owe" rule in one
    place -- the same reasoning that keeps `compute_deposit_amount`'s
    rounding logic out of the frontend (payments/services.py), just for a
    trivial case of it. The queryset this serializer runs against
    (`AccountAppointmentListView.get_queryset`) adds
    `select_related("payment", ..., "salon")`, so these fields and
    `deposit_amount`/`currency` cost no extra query per row across a list.

    `specialist`/`service` are nested `AppointmentSpecialistSerializer`/
    `AppointmentServiceSerializer` (`{id, name}`), not the default
    `ModelSerializer` FK behavior (a bare pk) -- the client dashboard needs
    a display name per card, not just an id (docs/DECISIONS.md § Stage 15
    planning, item 4). Reading `.name` on each traverses the FK, so
    `AccountAppointmentListView.get_queryset()` also adds
    `select_related("specialist", "service")` alongside `"payment"`, for the
    same N+1 reason, and `"salon"` for SalonLocalDateTimeField's time zone.
    """

    specialist = AppointmentSpecialistSerializer()
    service = AppointmentServiceSerializer()
    payment_status = serializers.SerializerMethodField()
    payment_amount = serializers.SerializerMethodField()
    amount_due_at_visit = serializers.SerializerMethodField()
    deposit_amount = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField()
    deposit_outcome = serializers.SerializerMethodField()
    start_datetime = SalonLocalDateTimeField()
    end_datetime = SalonLocalDateTimeField()
    refund_deadline = SalonLocalDateTimeField(value_from=_refund_deadline_or_none)

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
            "deposit_amount",
            "currency",
            "deposit_outcome",
            "refund_deadline",
        ]
        read_only_fields = fields

    def _payment(self, appointment: Appointment) -> Payment | None:
        return getattr(appointment, "payment", None)

    def get_payment_status(self, appointment: Appointment) -> str | None:
        payment = self._payment(appointment)
        return payment.status if payment is not None else None

    def get_payment_amount(self, appointment: Appointment) -> str | None:
        payment = self._payment(appointment)
        return str(payment.amount) if payment is not None else None

    def get_amount_due_at_visit(self, appointment: Appointment) -> str | None:
        payment = self._payment(appointment)
        if (
            appointment.status not in _AMOUNT_DUE_ELIGIBLE_STATUSES
            or payment is None
            or payment.status != PaymentStatus.SUCCEEDED
        ):
            return None
        return str(appointment.service_price_at_booking - payment.amount)

    def get_deposit_amount(self, appointment: Appointment) -> str | None:
        payment = self._payment(appointment)
        amount = payment.amount if payment is not None else compute_deposit_amount(appointment)
        return str(amount) if amount != 0 else None

    def get_currency(self, appointment: Appointment) -> str:
        payment = self._payment(appointment)
        return payment.currency if payment is not None else appointment.salon.currency

    def get_deposit_outcome(self, appointment: Appointment) -> str | None:
        payment = self._payment(appointment)
        if payment is None:
            return None
        if payment.status == PaymentStatus.REFUNDED:
            return "refunded"
        if payment.status == PaymentStatus.REFUND_PENDING:
            return "refund_processing"
        if payment.status == PaymentStatus.SUCCEEDED and payment.refund_due:
            return "refund_processing"
        if (
            payment.status == PaymentStatus.SUCCEEDED
            and appointment.status == AppointmentStatus.CANCELLED
        ):
            return "withheld"
        return None


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

    `source` is optional; a missing one means `direct`, and a value outside
    AppointmentSource is a 400 (docs/DECISIONS.md § Stage 16 planning,
    "Sub-step 9 details").
    """

    specialist = SpecialistOrAnyField(queryset=Specialist.unscoped_objects.none())
    service = serializers.PrimaryKeyRelatedField(queryset=Service.unscoped_objects.none())
    start_datetime = _OffsetRequiredDateTimeField()
    customer_name = serializers.CharField(max_length=255)
    customer_email = serializers.EmailField()
    customer_phone = serializers.CharField(max_length=32)
    source = serializers.ChoiceField(choices=AppointmentSource.choices, required=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.fields["specialist"].queryset = Specialist.objects.all()
        # Active services only: an inactive one gets the same "does not
        # exist" 400 as an unknown id (docs/DECISIONS.md § "Fix: the
        # specialist must perform the service").
        self.fields["service"].queryset = Service.objects.filter(is_active=True)


class AccountBookingRequestSerializer(serializers.Serializer):
    """
    Input validation for the account booking POST endpoint (docs/DECISIONS.md
    § Stage 15 planning, item 5, "Cycle B — account booking endpoint
    contract, decided 20.09.2026"). Same specialist/service/start_datetime
    handling as GuestBookingRequestSerializer above (including "any" via
    SpecialistOrAnyField) -- but no `customer_email` field at all: the
    Customer's email is always `request.user.email` (the authenticated
    Account's own, verified email), never the payload, so there is nothing
    here for a caller to even attempt to override. A `customer_email` sent
    anyway is simply ignored, the same as any other field this serializer
    doesn't declare -- a plain Serializer never rejects unknown input keys.

    `customer_name`/`customer_phone` are declared `required=False`: they are
    only actually required when `request.user.customer_id` is `None` (the
    new-Customer sub-case) -- checked in `validate()` below, which needs
    `self.context["request"]` for that, the same context every DRF generic
    view supplies automatically via `get_serializer()`. When an Account
    already has a linked Customer, both fields are accepted-but-unused if
    sent (the view never reads them in that branch) -- validate() does not
    reject them, since "ignored" is exactly the required contract, not an
    error.
    """

    specialist = SpecialistOrAnyField(queryset=Specialist.unscoped_objects.none())
    service = serializers.PrimaryKeyRelatedField(queryset=Service.unscoped_objects.none())
    start_datetime = _OffsetRequiredDateTimeField()
    customer_name = serializers.CharField(max_length=255, required=False)
    customer_phone = serializers.CharField(max_length=32, required=False)
    # Same optional `source` as GuestBookingRequestSerializer above.
    source = serializers.ChoiceField(choices=AppointmentSource.choices, required=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.fields["specialist"].queryset = Specialist.objects.all()
        # Same active-only binding as GuestBookingRequestSerializer above.
        self.fields["service"].queryset = Service.objects.filter(is_active=True)

    def validate(self, attrs: dict) -> dict:
        account = self.context["request"].user
        if account.customer_id is None:
            missing = [field for field in ("customer_name", "customer_phone") if field not in attrs]
            if missing:
                raise serializers.ValidationError(
                    {field: ["This field is required."] for field in missing}
                )
        return attrs


class AppointmentCreatedSerializer(serializers.ModelSerializer):
    """
    Response shape for the created appointment, nested under the "appointment"
    key in the 201 body (docs/DECISIONS.md § Stage 7.D decisions, "201
    response envelope"). Deliberately narrow — price/deposit snapshots are
    Stage 8.
    """

    start_datetime = SalonLocalDateTimeField()
    end_datetime = SalonLocalDateTimeField()

    class Meta:
        model = Appointment
        fields = ["id", "status", "start_datetime", "end_datetime"]
        read_only_fields = fields
