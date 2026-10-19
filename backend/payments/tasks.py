"""
Periodic payment sweeps run by Celery beat: the Stage 8.G stuck-refund
flagging sweep (docs/ARCHITECTURE.md § 8; docs/DECISIONS.md § Stage 8.G
decisions) and the Stage 15 refund retry sweep (docs/DECISIONS.md §
"Refund retry sweep"). Registered in config/celery.py's beat_schedule.
"""

import logging

from celery import shared_task
from django.utils import timezone

from core.tenancy import tenant_context
from payments.providers import get_payment_provider
from payments.services import flag_stuck_refunds, retry_owed_refunds
from tenants.models import Salon

logger = logging.getLogger(__name__)


@shared_task
def flag_stuck_refund_payments() -> None:
    """
    Loops every salon (Salon.objects.all() — Salon is the tenant root, not
    itself tenant-scoped), binding tenant_context(salon.id) per iteration so
    one salon's context can never bleed into the next. `now` is read once,
    before the loop, so every salon in a run is judged against the same
    instant. Each salon's processing is wrapped in its own try/except: one
    failing salon is logged and skipped, the rest still run. Deliberately no
    per-row try/except inside a salon's batch — only per-salon (mirrors §
    Stage 7.F decisions).
    """
    now = timezone.now()
    for salon in Salon.objects.all():
        try:
            with tenant_context(salon.id):
                flagged_count = flag_stuck_refunds(salon=salon, now=now)
            logger.info(
                "flag_stuck_refund_payments: flagged %d payment(s) for salon_id=%s",
                flagged_count,
                salon.id,
            )
        except Exception:
            logger.exception("flag_stuck_refund_payments: sweep failed for salon_id=%s", salon.id)


@shared_task
def retry_owed_refund_payments() -> None:
    """
    docs/DECISIONS.md § "Refund retry sweep". Same loop as
    flag_stuck_refund_payments: every salon, tenant_context(salon.id) bound
    per iteration, `now` read once before the loop, and each salon wrapped
    in its own try/except so one failing salon is logged and skipped while
    the rest still run. The provider comes only from get_payment_provider(),
    resolved once per run. Per-row handling of provider failures lives in
    retry_owed_refunds itself.
    """
    now = timezone.now()
    provider = get_payment_provider()
    for salon in Salon.objects.all():
        try:
            with tenant_context(salon.id):
                refunded_count = retry_owed_refunds(salon=salon, provider=provider, now=now)
            logger.info(
                "retry_owed_refund_payments: refunded %d payment(s) for salon_id=%s",
                refunded_count,
                salon.id,
            )
        except Exception:
            logger.exception("retry_owed_refund_payments: sweep failed for salon_id=%s", salon.id)
