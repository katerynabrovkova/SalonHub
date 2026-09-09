"""
Review serializers (docs/DECISIONS.md § Stage 11 Part 2).

`salon`, `customer`, `specialist`, `appointment` are all server-derived from
the resolved appointment (passed in via serializer context), never client
input — same posture as catalog/serializers.py's read-only `salon`.
`specialist` in particular is the denormalized snapshot of who performed the
visit (docs/DECISIONS.md § Stage 11).
"""

from typing import Any

from rest_framework import serializers

from booking.models import AppointmentStatus
from core.exceptions import DuplicateReviewError, ReviewRequiresCompletedAppointmentError
from core.i18n import resolve_translation
from reviews.models import REVIEW_TEXT_MAX_LENGTH, Review
from specialists.models import Specialist


class ReviewPublicSerializer(serializers.ModelSerializer):
    """
    Public read representation for the grouped list endpoint
    (docs/DECISIONS.md § Stage 11 Part 2). Deliberately omits `customer`
    (and every other identifying field) — the list endpoint is
    unauthenticated. Separate from ReviewCreateSerializer, which is
    write-oriented and exposes `customer` / `salon` / `appointment`.
    """

    class Meta:
        model = Review
        fields = ["id", "rating", "text", "created_at"]
        read_only_fields = fields


class ReviewSpecialistSerializer(serializers.ModelSerializer):
    """
    The `specialist` block of each group in the public list response.

    `name` is resolved to a plain string for the request's ``?lang=`` per
    the read-side language contract (docs/DECISIONS.md § "Wiring ?lang= into
    the Reviews read endpoint"), never the raw ``{lang: text}`` dict —
    mirrors specialists.SpecialistReadSerializer. ReviewListView constructs
    this serializer by hand, so it must pass ``context={"request": request}``
    for ``get_name`` to see the parameter.
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


class ReviewCreateSerializer(serializers.ModelSerializer):
    """
    Write serializer for POST .../appointments/<id>/review/. `rating` (1-5)
    and `text` (<= 2000) are bounded at the field level so an out-of-range
    value is a clean 400 here rather than a 500 from the model
    CheckConstraint (which surfaces as an untranslated CheckViolation). The
    DB constraints remain the real backstop.
    """

    rating = serializers.IntegerField(min_value=1, max_value=5)
    text = serializers.CharField(
        max_length=REVIEW_TEXT_MAX_LENGTH, allow_blank=True, required=False, default=""
    )

    class Meta:
        model = Review
        fields = [
            "id",
            "appointment",
            "salon",
            "customer",
            "specialist",
            "rating",
            "text",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "appointment",
            "salon",
            "customer",
            "specialist",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        appointment = self.context["appointment"]
        if appointment.status != AppointmentStatus.COMPLETED:
            raise ReviewRequiresCompletedAppointmentError()
        # Check-then-write; the Review.appointment OneToOne is the real
        # guarantee against a concurrent double-submit (a UniqueViolation
        # then surfaces as 400 via core.exceptions.exception_handler).
        if Review.objects.filter(appointment=appointment).exists():
            raise DuplicateReviewError()
        return attrs

    def create(self, validated_data: dict[str, Any]) -> Review:
        appointment = self.context["appointment"]
        return Review.objects.create(
            salon_id=appointment.salon_id,
            appointment=appointment,
            customer_id=appointment.customer_id,
            specialist_id=appointment.specialist_id,
            **validated_data,
        )
