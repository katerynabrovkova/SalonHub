import datetime as dt
from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo

from rest_framework import serializers

from booking import amounts
from booking.models import (
    Appointment,
    AppointmentSource,
)
from catalog.models import Service
from core.i18n import resolve_translation
from scheduling.serializers import _OffsetRequiredDateTimeField
from specialists.models import Specialist


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


class AppointmentSpecialistSerializer(serializers.ModelSerializer):
    """
    The `specialist` block on _AppointmentCustomerViewBase, below (so on
    both the account and the guest appointment serializers) -- `{id, name}`
    with `name` resolved for the request's `?lang=`. Mirrors
    `reviews.serializers.ReviewSpecialistSerializer`'s identical shape/
    resolution, not reused directly: booking does not depend on reviews (the
    reverse dependency already exists -- reviews imports `booking.models`).
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
    """The `service` block on _AppointmentCustomerViewBase -- see
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


class _AppointmentCustomerViewBase(serializers.ModelSerializer):
    """
    The computed read-only fields a customer sees about their own
    appointment, shared by AppointmentAccountSerializer (the account list and
    cancel) and AppointmentGuestSerializer (the guest-token detail and
    cancel), so the two can never compute a value differently
    (docs/DECISIONS.md § "Booking > Guest token", "Guest detail and cancel
    responses"). Never used on its own: each subclass names in its own
    `Meta.fields` which of these it returns (DRF does not require a field
    declared on a parent class to be listed).

    Every value comes from `booking.amounts`, which the booking emails use
    too; the rules (Payment first, salon fallback, null-means-nothing-to-
    show, the refund deadline from `booking.services.refund_deadline`) are
    documented there. This class only renders them.

    Money method fields return `str(...)` of their `Decimal`, not a bare
    `Decimal`: a `SerializerMethodField` has no `to_representation` coercion
    of its own, so an un-stringified `Decimal` renders as a bare JSON number
    (verified: `JSONRenderer().render({"x": Decimal("12.34")})` ->
    `b'{"x":12.34}'`) -- inconsistent with `service_price_at_booking`, a real
    `DecimalField` whose own `to_representation` always produces a string
    (`"12.34"`). `str()` makes every money field serialize the same way.
    `refund_deadline` goes through SalonLocalDateTimeField, like
    `start_datetime`, so it carries the salon's offset too.

    `specialist`/`service` are nested `AppointmentSpecialistSerializer`/
    `AppointmentServiceSerializer` (`{id, name}`, name resolved per
    `?lang=`), not the default `ModelSerializer` FK behavior (a bare pk) --
    a customer needs a display name, not just an id (docs/DECISIONS.md §
    Stage 15 planning, item 4). A list view using a subclass must
    `select_related("payment", "specialist", "service", "salon")` so these
    fields, and SalonLocalDateTimeField's time zone, cost no query per row
    (see `AccountAppointmentListView.get_queryset`).
    """

    specialist = AppointmentSpecialistSerializer()
    service = AppointmentServiceSerializer()
    amount_due_at_visit = serializers.SerializerMethodField()
    deposit_amount = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField()
    deposit_outcome = serializers.SerializerMethodField()
    start_datetime = SalonLocalDateTimeField()
    end_datetime = SalonLocalDateTimeField()
    refund_deadline = SalonLocalDateTimeField(value_from=amounts.refund_deadline_or_none)

    def get_amount_due_at_visit(self, appointment: Appointment) -> str | None:
        amount = amounts.amount_due_at_visit(appointment)
        return str(amount) if amount is not None else None

    def get_deposit_amount(self, appointment: Appointment) -> str | None:
        amount = amounts.deposit_amount(appointment)
        return str(amount) if amount is not None else None

    def get_currency(self, appointment: Appointment) -> str:
        return amounts.currency(appointment)

    def get_deposit_outcome(self, appointment: Appointment) -> str | None:
        return amounts.deposit_outcome(appointment)


class AppointmentAccountSerializer(_AppointmentCustomerViewBase):
    """
    Read-only representation for AccountAppointmentListView and
    AccountAppointmentCancelView (docs/DECISIONS.md § Stage 15 planning,
    items 1 and 4). The computed fields come from _AppointmentCustomerViewBase,
    shared with AppointmentGuestSerializer; on top of those this adds the raw
    `deposit_percentage_at_booking` snapshot and the Payment's own
    `payment_status`/`payment_amount` (null without a Payment), which the
    dashboard reads and the guest responses do not carry.
    """

    payment_status = serializers.SerializerMethodField()
    payment_amount = serializers.SerializerMethodField()

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

    def get_payment_status(self, appointment: Appointment) -> str | None:
        payment = amounts.payment_of(appointment)
        return payment.status if payment is not None else None

    def get_payment_amount(self, appointment: Appointment) -> str | None:
        payment = amounts.payment_of(appointment)
        return str(payment.amount) if payment is not None else None


class AppointmentGuestSerializer(_AppointmentCustomerViewBase):
    """
    Read-only representation for the guest-token detail and cancel endpoints
    (booking/views.py), the data of the guest manage page (docs/DECISIONS.md
    § "Booking > Guest token", "Guest detail and cancel responses"). The
    computed fields come from _AppointmentCustomerViewBase, shared with
    AppointmentAccountSerializer. Never the customer's name, email or phone:
    the page can be opened from a forwarded email.
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
            "service_price_at_booking",
            "deposit_amount",
            "amount_due_at_visit",
            "currency",
            "deposit_outcome",
            "refund_deadline",
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
