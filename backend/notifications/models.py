from django.db import models

from accounts.models import Customer
from booking.models import Appointment
from core.models import TenantScopedModel, TimeStamped


class NotificationChannel(models.TextChoices):
    EMAIL = "email", "Email"
    TELEGRAM = "telegram", "Telegram"


class NotificationTrigger(models.TextChoices):
    BOOKING_CREATED = "booking_created", "Booking created"
    BOOKING_CONFIRMED = "booking_confirmed", "Booking confirmed"
    BOOKING_CANCELLED = "booking_cancelled", "Booking cancelled"
    BOOKING_EXPIRED = "booking_expired", "Booking expired"
    APPOINTMENT_REMINDER = "appointment_reminder", "Appointment reminder"
    PAYMENT_SUCCEEDED = "payment_succeeded", "Payment succeeded"
    PAYMENT_FAILED = "payment_failed", "Payment failed"
    REVIEW_REQUEST = "review_request", "Review request"
    # Not appointment-scoped (docs/ARCHITECTURE.md § 3, § 9) — guest->User
    # linking's verification email, sent to the `user` recipient slot.
    EMAIL_VERIFICATION = "email_verification", "Email verification"


class NotificationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"


class Notification(TenantScopedModel, TimeStamped):
    """
    Send log / dedup ledger (docs/ARCHITECTURE.md § 9). `appointment` is
    nullable because not every trigger is appointment-scoped (EMAIL_VERIFICATION
    has none), so the dedup natural key is `(trigger_type, channel, dedup_key)`,
    not `(trigger_type, appointment, channel)` — a nullable column can't carry
    a uniqueness guarantee in Postgres.
    """

    appointment = models.ForeignKey(
        Appointment, null=True, blank=True, on_delete=models.CASCADE, related_name="notifications"
    )
    customer = models.ForeignKey(
        Customer, null=True, blank=True, on_delete=models.CASCADE, related_name="notifications"
    )

    channel = models.CharField(max_length=16, choices=NotificationChannel.choices)
    trigger_type = models.CharField(max_length=32, choices=NotificationTrigger.choices)
    # Caller-populated collision key, e.g. f"appointment:{id}" or f"user:{id}".
    dedup_key = models.CharField(max_length=255)
    status = models.CharField(
        max_length=16, choices=NotificationStatus.choices, default=NotificationStatus.PENDING
    )
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantScopedModel.Meta):
        abstract = False
        constraints = [
            *TenantScopedModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["trigger_type", "channel", "dedup_key"],
                name="notification_trigger_channel_dedup_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.trigger_type} via {self.channel} ({self.status})"
