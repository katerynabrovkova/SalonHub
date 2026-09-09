from django.db import models
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Length
from django.db.models.lookups import LessThanOrEqual

from core.i18n import resolve_display_name
from core.models import TimeStamped
from core.validators import ISO_4217_PATTERN, iso_4217_validator

SALON_ABOUT_MAX_LENGTH = 2000

# Enumerated per-language length checks for the translatable `about` dict
# (docs/DECISIONS.md § Stage 11.5 sub-step 1). A dict of arbitrary keys can't
# be length-checked as a whole at the DB level, so each supported language
# key is checked individually as `length(about ->> '<lang>') <= N`
# (KeyTextTransform is the `->>` text extraction). A missing key makes
# `about ->> '<lang>'` SQL NULL, so `LENGTH(NULL) <= N` is NULL — not false —
# and an untranslated language passes the constraint. Adding a language means
# adding its check here in a migration.
_ABOUT_LENGTH_LANGUAGES = ("en", "uk")


def _about_length_condition() -> models.Q:
    return models.Q(
        *(
            LessThanOrEqual(
                Length(KeyTextTransform(lang, "about")),
                models.Value(SALON_ABOUT_MAX_LENGTH),
            )
            for lang in _ABOUT_LENGTH_LANGUAGES
        )
    )


class Salon(TimeStamped):
    """
    The tenant root. Everything else in the platform scopes to one of these
    via a `salon` FK (docs/DECISIONS.md § Multi-tenancy). Not itself a
    TenantScopedModel — a Salon doesn't belong to a tenant, it is one.
    """

    # Translatable: {lang_code: string}, e.g. {"en": "Bella", "uk": "Белла"}.
    # Empty dict is the "no translations yet" state (docs/DECISIONS.md
    # § Stage 11.5). Resolution + English fallback is a serializer concern
    # (sub-step 2), not done here.
    name = models.JSONField(default=dict)
    slug = models.SlugField(unique=True)

    # Salon-profile free text, translatable: {lang_code: string}. New in
    # Stage 11.5; there was no bio/description field before. Optional — empty
    # dict when unset. Each populated language value is capped by the
    # salon_about_max_length_2000_per_language CheckConstraint below.
    about = models.JSONField(default=dict, blank=True)

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
        # `name` is a JSON dict now, not a scalar — it can't be a meaningful
        # ORDER BY key (was `["name"]`).
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(currency__regex=ISO_4217_PATTERN),
                name="salon_currency_iso_4217",
            ),
            models.CheckConstraint(
                condition=models.Q(contact_email__gt=""),
                name="salon_contact_email_not_empty",
            ),
            models.CheckConstraint(
                condition=_about_length_condition(),
                name="salon_about_max_length_2000_per_language",
            ),
        ]

    def __str__(self) -> str:
        # `name` is a {lang: str} dict (Stage 11.5). Resolve like an API read
        # with no `?lang=` — English, then any populated language — with an
        # identifying fallback so a fully-untranslated row is still findable
        # in admin (docs/DECISIONS.md § "Admin display of translatable
        # fields"; § "Notification message builder: language resolution").
        return resolve_display_name(self.name, f"Salon #{self.pk}")
