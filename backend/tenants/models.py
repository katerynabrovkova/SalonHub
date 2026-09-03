from django.db import models

from core.models import TimeStamped
from core.validators import ISO_4217_PATTERN, iso_4217_validator


class Salon(TimeStamped):
    """
    The tenant root. Everything else in the platform scopes to one of these
    via a `salon` FK (docs/DECISIONS.md § Multi-tenancy). Not itself a
    TenantScopedModel — a Salon doesn't belong to a tenant, it is one.
    """

    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)

    # Drives the Stage 3 tenant-resolution middleware's 404 for a deactivated
    # tenant (docs/DECISIONS.md § Stage 3 decisions) — deliberately not a
    # delete, since Salon is the FK target for every tenant-owned row.
    is_active = models.BooleanField(default=True)

    # Local-time rendering input (docs/DECISIONS.md § Timezone). Default is
    # the demo tenant's own timezone, not a platform-wide assumption.
    timezone = models.CharField(max_length=63, default="Europe/Kyiv")

    # ISO 4217, e.g. "UAH". Deliberately no model-level default — a new
    # salon must specify its currency explicitly, never silently inherit
    # UAH (docs/DECISIONS.md § Stage 8 decisions). Frozen onto each Payment
    # at creation time; see payments.models.Payment.currency.
    currency = models.CharField(max_length=3, validators=[iso_4217_validator])

    # Destination for salon-directed operational alerts (first case: the
    # Stage 8 stuck-refund flag, turned into an email in Stage 9). NOT NULL
    # and no model default: a salon with no operational contact address is
    # not a usable tenant. NOT NULL alone still permits "" — non-emptiness
    # is enforced by the salon_contact_email_not_empty CheckConstraint
    # below, deliberately not a field-level validator (a validator is
    # bypassed by .create()/bulk_create/raw SQL; the DB constraint is not).
    # See docs/DECISIONS.md § Stage 9 decisions.
    contact_email = models.EmailField(max_length=254)

    # Business rules — see docs/DECISIONS.md § Business rules for the "why"
    # behind every default below; all are salon-configurable.
    deposit_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=20)
    min_lead_time_hours = models.PositiveIntegerField(default=3)
    max_advance_days = models.PositiveIntegerField(default=60)
    slot_granularity_minutes = models.PositiveIntegerField(default=15)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(currency__regex=ISO_4217_PATTERN),
                name="salon_currency_iso_4217",
            ),
            models.CheckConstraint(
                condition=models.Q(contact_email__gt=""),
                name="salon_contact_email_not_empty",
            ),
        ]

    def __str__(self) -> str:
        return self.name
