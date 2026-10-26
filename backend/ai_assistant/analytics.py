"""
Stage 16 sub-step 9 — the assistant's daily counters (docs/DECISIONS.md §
Stage 16 planning, "Sub-step 9 details").

`record_conversation_started` and `record_proposal` add one to the salon's
row for the salon-local date of `now`. The given salon must be the bound
tenant: the tenant-scoped UPDATE would otherwise match nothing while the
create inserted a row for the other salon.

An increment is never a read-modify-write: an UPDATE with F("count") + 1;
when no row exists yet, a create in a savepoint; when that create loses to a
concurrent one (IntegrityError), the UPDATE is retried exactly once and the
error is raised if it still matches nothing. The orchestrator logs and
swallows any failure. No conversation text is involved.
"""

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.db.models import F

from ai_assistant.models import DailyConversationCount, DailyProposalCount
from catalog.models import Service
from core.tenancy import TenantContextMismatchError, TenantContextMissingError, get_current_salon_id
from tenants.models import Salon


def _require_bound_salon(salon: Salon) -> None:
    bound_salon_id = get_current_salon_id()
    if bound_salon_id is None:
        raise TenantContextMissingError("The assistant counters need a bound tenant context.")
    if bound_salon_id != salon.id:
        raise TenantContextMismatchError(
            f"Counter for salon {salon.id} called under tenant context {bound_salon_id}."
        )


def _local_date(salon: Salon, now: dt.datetime) -> dt.date:
    return now.astimezone(ZoneInfo(salon.timezone)).date()


def _increment(model: type[DailyConversationCount | DailyProposalCount], **row: Any) -> None:
    if model.objects.filter(**row).update(count=F("count") + 1):
        return
    try:
        with transaction.atomic():
            model.objects.create(count=1, **row)
    except IntegrityError:
        # A concurrent create won: its row is there now. Retried once only,
        # so a state where the row still cannot be seen raises, never loops.
        if not model.objects.filter(**row).update(count=F("count") + 1):
            raise


def record_conversation_started(*, salon: Salon, now: dt.datetime) -> None:
    _require_bound_salon(salon)
    _increment(DailyConversationCount, salon=salon, date=_local_date(salon, now))


def record_proposal(*, salon: Salon, service_id: int, now: dt.datetime) -> None:
    """Raises ValueError for a service of another salon; the composite
    tenant FK on the table rejects such a row as well."""
    _require_bound_salon(salon)
    if not Service.objects.filter(pk=service_id).exists():
        raise ValueError(f"The service {service_id} does not belong to salon {salon.id}.")
    _increment(DailyProposalCount, salon=salon, date=_local_date(salon, now), service_id=service_id)
