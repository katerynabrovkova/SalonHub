"""
Domain exception hierarchy and the single DRF exception handler that
translates every API-boundary error into one JSON envelope
(docs/ARCHITECTURE.md § 13/14):

    {"error": {"code": "...", "message": "...", "details": {...}}}

The service layer raises DomainError subclasses — never DRF's
ValidationError directly — so it stays framework-agnostic and callable
identically from API views, Celery tasks, and the AI assistant's tool
functions. This module is the only place that knows how to turn either kind
of exception (DomainError, or DRF's own) into a response.
"""

import logging

import psycopg
from django.conf import settings
from django.db import IntegrityError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as _drf_exception_handler

logger = logging.getLogger(__name__)


class DomainError(Exception):
    """Base for every service-layer error the API boundary knows how to translate."""

    code = "domain_error"
    status_code = status.HTTP_400_BAD_REQUEST
    default_message = "A domain error occurred."

    def __init__(self, message: str | None = None, *, details: dict | None = None) -> None:
        self.message = message or self.default_message
        self.details = details or {}
        super().__init__(self.message)


class InvalidOrExpiredTokenError(DomainError):
    code = "invalid_or_expired_token"
    status_code = status.HTTP_400_BAD_REQUEST
    default_message = "This link is invalid or has expired."


class InvalidStateTransitionError(DomainError):
    code = "invalid_state_transition"
    status_code = status.HTTP_409_CONFLICT
    default_message = "This action isn't valid for the current state."


class ReviewRequiresCompletedAppointmentError(DomainError):
    """
    docs/DECISIONS.md § Stage 11 Part 2: a review may only be left for a
    COMPLETED visit. 403, not 400 — the request is well-formed and the
    caller owns the appointment; it is the appointment's state that forbids
    the action. Raised from ReviewCreateSerializer.validate().
    """

    code = "review_requires_completed_appointment"
    status_code = status.HTTP_403_FORBIDDEN
    default_message = "This appointment is not completed yet; a review requires a completed visit."


class DuplicateReviewError(DomainError):
    """
    docs/DECISIONS.md § Stage 11 Part 2: one review per appointment. The
    Review.appointment OneToOne is the real guarantee, but a bare
    UniqueViolation is translated to 400 by the handler below — this
    explicit serializer-level check raises the intended 409 instead.
    """

    code = "duplicate_review"
    status_code = status.HTTP_409_CONFLICT
    default_message = "A review for this appointment already exists."


class PaymentProviderError(DomainError):
    """
    docs/DECISIONS.md § Stage 8.C decisions: provider.start_payment() raised
    — the external payment provider failed, not our own code. 502 Bad
    Gateway, not a 4xx: nothing about the request itself was invalid.
    """

    code = "payment_provider_error"
    status_code = status.HTTP_502_BAD_GATEWAY
    default_message = "The payment provider could not process this request."


class CategoryHasActiveServicesError(DomainError):
    code = "category_has_active_services"
    status_code = status.HTTP_409_CONFLICT
    default_message = "This category still has active services; deactivate or reassign them first."


class SpecialistHasFutureAppointmentsError(DomainError):
    code = "specialist_has_future_appointments"
    status_code = status.HTTP_409_CONFLICT
    default_message = (
        "This specialist has future appointments; they must be resolved before deactivating."
    )


class InvalidDateRangeError(DomainError):
    code = "invalid_date_range"
    status_code = status.HTTP_400_BAD_REQUEST
    default_message = "The requested date range is invalid."


class SlotNotOfferedError(DomainError):
    """
    docs/DECISIONS.md § Stage 7.C decisions: `start_datetime` was never an
    offered candidate at all (outside lead-time/advance window, wrong
    working hours, inactive specialist, ...) — a frontend-bug/tampered-
    request signal, distinct from SlotUnavailableError's ordinary booking
    race. Carries no per-filter reason in its details; the service logs
    that context server-side instead.
    """

    code = "SLOT_NOT_OFFERED"
    status_code = status.HTTP_400_BAD_REQUEST
    default_message = "This slot is not currently offered."


class SlotUnavailableError(DomainError):
    """
    docs/DECISIONS.md § Stage 7.C decisions: the slot was valid and offered
    but someone else took it first — the ordinary booking race, caught by
    either the application-level re-check or the exclusion constraint.
    """

    code = "SLOT_NO_LONGER_AVAILABLE"
    status_code = status.HTTP_409_CONFLICT
    default_message = "This slot is no longer available."


class InvalidSteppingParametersError(DomainError):
    """
    docs/DECISIONS.md § Stage 6.E decisions: granularity_minutes/
    occupied_minutes non-positive means a Salon/Service row is
    misconfigured, not that the request itself was malformed — status_code
    is 500, deliberately different from InvalidDateRangeError's 400, even
    though both are DomainError subclasses raised from the same service
    layer. No HTTP mapping is wired up yet; that lands with the GET
    endpoint substage, same as InvalidDateRangeError's did.
    """

    code = "invalid_stepping_parameters"
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    default_message = "Slot granularity or occupied time is misconfigured."


def _envelope(*, code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def exception_handler(exc: Exception, context: dict) -> Response | None:
    if isinstance(exc, DomainError):
        return Response(
            _envelope(code=exc.code, message=exc.message, details=exc.details),
            status=exc.status_code,
        )

    # A serializer-level uniqueness check (e.g. catalog's validate_name,
    # docs/DECISIONS.md § Stage 4 decisions) is check-then-write and can't
    # close a race between two concurrent requests; the database constraint
    # is the actual guarantee. Narrowed to UniqueViolation specifically (not
    # bare IntegrityError) so an unrelated integrity bug — a FK mismatch, a
    # violated CHECK constraint — still surfaces as a loud 500 rather than
    # being reinterpreted as a client validation error. Deliberately generic
    # (no per-field detail parsed from the constraint name): core has no
    # domain meaning of its own, and inspecting catalog-specific constraint
    # names here would violate that boundary.
    is_unique_violation = isinstance(exc.__cause__, psycopg.errors.UniqueViolation)
    if isinstance(exc, IntegrityError) and is_unique_violation:
        return Response(
            _envelope(
                code="unique_violation",
                message="This would duplicate an existing record.",
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    response = _drf_exception_handler(exc, context)
    if response is not None:
        code = getattr(exc, "default_code", exc.__class__.__name__.lower())
        if isinstance(response.data, dict) and "detail" in response.data:
            message = str(response.data["detail"])
            details: dict = {}
        else:
            message = "Request failed."
            if isinstance(response.data, dict):
                details = response.data
            else:
                details = {"errors": response.data}
        response.data = _envelope(code=code, message=message, details=details)
        return response

    # Unrecognized exception: full traceback always logged server-side. In
    # DEBUG, let it propagate so Django's interactive traceback page still
    # works for API views during local development; otherwise return the
    # generic envelope with nothing leaked to the client.
    logger.exception("Unhandled exception in API view")
    if settings.DEBUG:
        return None
    return Response(
        _envelope(code="internal_error", message="An unexpected error occurred."),
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
