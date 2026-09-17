from django.db import models

from booking.models import Appointment
from core.models import TenantScopedModel, TimeStamped
from core.validators import ISO_4217_PATTERN, iso_4217_validator


class PaymentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    EXPIRED = "expired", "Expired"
    CANCELLED = "cancelled", "Cancelled"
    REFUND_PENDING = "refund_pending", "Refund pending"
    REFUNDED = "refunded", "Refunded"


class Payment(TenantScopedModel, TimeStamped):
    """
    The deposit payment/refund lifecycle (docs/ARCHITECTURE.md § 8), modeled
    separately from Appointment.status. One deposit payment per appointment.
    """

    appointment = models.OneToOneField(
        Appointment, on_delete=models.PROTECT, related_name="payment"
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    # ISO 4217, frozen from Salon.currency at Payment creation time — copied
    # once and never mutated afterward, even if the salon later changes its
    # currency (docs/DECISIONS.md § Stage 8 decisions). No model-level
    # default; the copying itself is Stage 8.C, not this field addition.
    currency = models.CharField(max_length=3, validators=[iso_4217_validator])
    status = models.CharField(
        max_length=32, choices=PaymentStatus.choices, default=PaymentStatus.PENDING
    )
    # unique=True: the webhook lookup (payments/views.py's PaymentWebhookView)
    # does Payment.unscoped_objects.get(provider_reference_id=...) and assumes
    # exactly one match — a DB-level guarantee, not just an app-level
    # assumption that each start_payment() call happens to produce a fresh
    # value (docs/DECISIONS.md § "Stage 14 payment step: WayForPay webhook
    # -> PaymentWebhookView mapping"). unique=True already creates its own
    # index, so the separate db_index=True this replaces is redundant.
    provider_reference_id = models.CharField(max_length=255, blank=True, unique=True)
    # Provider-neutral hand-off data returned alongside provider_reference_id
    # by PaymentIntent (e.g. a hosted-payment-page URL) — whatever the
    # frontend needs to continue payment, or None. Stored so a guest
    # re-requesting pay while the Payment is still PENDING sees the same
    # link again instead of losing it (docs/DECISIONS.md § Stage 14 payment
    # step). null for MockPaymentProvider, which always returns None, and
    # for every pre-existing row.
    provider_data = models.CharField(max_length=1024, null=True, blank=True)
    # Stuck-refund alert marker, not a new state-machine status — REFUND_PENDING
    # already describes the state; this is a flag on top of it, set by a
    # future background sweep (docs/DECISIONS.md § Stage 8 decisions). No
    # db_index yet — nothing filters on it until an admin view needs to.
    flagged_for_review = models.BooleanField(default=False)
    # The instant this payment transitioned SUCCEEDED -> REFUND_PENDING,
    # stamped once by initiate_refund and never moved afterward. null for
    # every payment that never entered a refund — the honest "no refund"
    # state, same null-vs-"" reasoning as Specialist.photo. This is the
    # sweep's own clock for "how long stuck", deliberately not updated_at
    # (auto_now moves on every save, untrustworthy for that measurement;
    # docs/DECISIONS.md § Stage 8.G decisions).
    refund_initiated_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantScopedModel.Meta):
        abstract = False
        constraints = [
            *TenantScopedModel.Meta.constraints,
            models.CheckConstraint(
                condition=models.Q(currency__regex=ISO_4217_PATTERN),
                name="payment_currency_iso_4217",
            ),
        ]

    def __str__(self) -> str:
        return f"Payment for {self.appointment} ({self.status})"


class ProcessedWebhookEvent(models.Model):
    """
    Idempotency ledger for inbound payment-provider webhooks
    (docs/ARCHITECTURE.md § 8). Not a TenantScopedModel: an event arrives
    before we know which salon its payment belongs to.
    """

    provider_event_id = models.CharField(max_length=255, unique=True)
    processed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.provider_event_id
