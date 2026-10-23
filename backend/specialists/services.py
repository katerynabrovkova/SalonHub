"""
Specialist service layer (docs/ARCHITECTURE.md § 2; Stage 5 sub-step 5).

Soft delete only — Specialist rows are never hard-deleted once they could be
referenced by an Appointment (docs/ARCHITECTURE.md § 2), same as catalog's
Service/ServiceCategory (catalog/services.py). Deactivation is additionally
refused outright (409) if the specialist still has a future non-cancelled
appointment — docs/DECISIONS.md § Stage 5 decisions records why: this stage
has no payment/notification/conflict-resolution layer to honor the standing
rule (docs/DECISIONS.md § Business rules) that such a conflict must be
explicitly resolved, never silently orphaned.
"""

from django.db.models import Avg, Count, QuerySet
from django.utils import timezone

from booking.models import ACTIVE_APPOINTMENT_STATUSES, Appointment
from catalog.models import Service
from core.exceptions import SpecialistHasFutureAppointmentsError
from specialists.models import Specialist, SpecialistService


def _future_appointments(specialist: Specialist) -> QuerySet[Appointment]:
    """
    "Still expected" reuses booking's own ACTIVE_APPOINTMENT_STATUSES — the
    same set the double-booking exclusion constraint treats as holding a slot
    — rather than a second, hand-written list (docs/DECISIONS.md § Stage 5
    decisions). "Future" compares end_datetime, not blocked_until, against
    now(): an appointment already in progress (started, not yet ended) still
    counts, since the specialist has a live commitment until the visit itself
    ends; the buffer after it is room/equipment overhead, not something a
    customer is waiting on.
    """
    return Appointment.objects.filter(
        specialist=specialist,
        status__in=ACTIVE_APPOINTMENT_STATUSES,
        end_datetime__gt=timezone.now(),
    )


def annotate_rating(queryset: QuerySet[Specialist]) -> QuerySet[Specialist]:
    """
    `queryset` annotated with `average_rating` and `review_count`, for the
    specialists views and the AI assistant (docs/DECISIONS.md § Stage 16
    planning, "Sub-step 4 details", 4a). Callers prefetch `services`
    separately, never join it into this aggregate: a join on `services`
    would multiply rows before aggregation and inflate both values. A
    specialist with no reviews gets `average_rating=None`, `review_count=0`.
    """
    return queryset.annotate(average_rating=Avg("reviews__rating"), review_count=Count("reviews"))


def specialist_offers_service(specialist: Specialist, service: Service) -> bool:
    """
    Whether a SpecialistService row links `specialist` to `service`
    (docs/DECISIONS.md § Stage 16 planning, "Sub-step 6 details", 6a).
    Tenant-scoped: requires tenant context to already be bound.
    """
    return SpecialistService.objects.filter(specialist=specialist, service=service).exists()


def soft_delete_specialist(specialist: Specialist) -> None:
    future_ids = list(_future_appointments(specialist).values_list("id", flat=True))
    if future_ids:
        raise SpecialistHasFutureAppointmentsError(
            details={
                "future_appointment_count": len(future_ids),
                "future_appointment_ids": future_ids,
            }
        )
    specialist.is_active = False
    specialist.save(update_fields=["is_active"])
