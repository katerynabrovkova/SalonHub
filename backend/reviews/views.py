"""
Review endpoints (docs/DECISIONS.md § Stage 11 Part 2).

Write:

    POST /api/v1/salons/<slug>/appointments/<appointment_id>/review/

Dual identity (Account JWT or X-Guest-Token, Account wins) is handled by
reviews.permissions.CanSubmitAppointmentReview. This view resolves the
target appointment *from the identity* and compares it to the URL id — a
mismatch, a cross-tenant id, or an appointment the caller doesn't own all
yield 404 (never 403), so the endpoint doesn't reveal which appointment ids
exist. Same "resolve from identity, compare to URL, mismatch = not found"
shape as booking/views.py's _GuestTokenAppointmentMixin, written out here
rather than reused because that mixin is single-auth (guest token only).

Eligibility (COMPLETED) and the one-per-appointment rule live in
ReviewCreateSerializer.validate(); no view-level try/except — the
DomainError subclasses it raises propagate to
core.exceptions.exception_handler (403 / 409), the same pattern as the
booking service-layer errors.
"""

from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from rest_framework import generics
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from booking.models import Appointment
from reviews.models import Review
from reviews.permissions import CanSubmitAppointmentReview
from reviews.serializers import (
    ReviewCreateSerializer,
    ReviewPublicSerializer,
    ReviewSpecialistSerializer,
)
from specialists.models import Specialist


class ReviewCreateView(generics.CreateAPIView):
    permission_classes = [CanSubmitAppointmentReview]
    serializer_class = ReviewCreateSerializer

    def get_queryset(self) -> QuerySet[Review]:
        return Review.objects.all()

    def create(self, request: Request, *args: object, **kwargs: object) -> Response:
        self._appointment = self._resolve_owned_appointment()
        return super().create(request, *args, **kwargs)

    def get_serializer_context(self) -> dict[str, object]:
        context = super().get_serializer_context()
        context["appointment"] = self._appointment
        return context

    def _resolve_owned_appointment(self) -> Appointment:
        url_appointment_id = self.kwargs["appointment_id"]

        guest_token = getattr(self.request, "guest_access_token", None)
        if guest_token is not None:
            if guest_token.appointment_id != url_appointment_id:
                raise NotFound()
            return guest_token.appointment

        # Account path: Appointment.objects is tenant-scoped, so an id from
        # another salon is simply absent -> 404.
        appointment = get_object_or_404(Appointment.objects.all(), pk=url_appointment_id)
        if appointment.customer_id != self.request.user.customer_id:
            raise NotFound()
        return appointment


class ReviewListView(APIView):
    """
    GET /api/v1/salons/<slug>/reviews/ — public, unpaginated, grouped by
    specialist (docs/DECISIONS.md § Stage 11 Part 2). Returns a bare list of
    ``{"specialist": {...}, "reviews": [...]}`` groups, one per specialist
    with at least one review, ordered by review count descending (tie:
    lower specialist id first). Reviews within a group are newest-first.

    One query: every tenant review with its specialist joined; grouping and
    ordering happen in Python (per-salon review volume is assumed modest —
    the same premise that made pagination unnecessary). Tenant scoping is
    the ambient TenantResolutionMiddleware context, like every view here.
    """

    permission_classes = [AllowAny]

    def get(self, request: Request, *args: object, **kwargs: object) -> Response:
        reviews = Review.objects.select_related("specialist").order_by("-created_at", "-id")

        grouped: dict[int, list[Review]] = {}
        specialists: dict[int, Specialist] = {}
        for review in reviews:
            grouped.setdefault(review.specialist_id, []).append(review)
            specialists.setdefault(review.specialist_id, review.specialist)

        ordered = sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))

        payload = [
            {
                "specialist": ReviewSpecialistSerializer(specialists[specialist_id]).data,
                "reviews": ReviewPublicSerializer(rows, many=True).data,
            }
            for specialist_id, rows in ordered
        ]
        return Response(payload)
