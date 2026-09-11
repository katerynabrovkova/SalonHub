from django.db import models
from django.db.models import Func, TextField, Value
from django.db.models.fields.json import KeyTextTransform

from core.i18n import resolve_display_name
from core.models import TenantScopedModel, TimeStamped


class NullIf(Func):
    """
    ``NULLIF(<expr>, <expr>)``. Used below to fold an absent or empty JSON
    language value (``name ->> '<lang>'`` returning NULL or ``''``) to SQL
    NULL, so it drops out of the per-language functional unique index:
    Postgres treats NULLs as distinct, so any number of rows still lacking a
    translation for that language never collide with each other
    (docs/DECISIONS.md § Stage 11.5 sub-step 1).
    """

    function = "NULLIF"
    arity = 2


class ServiceCategory(TenantScopedModel, TimeStamped):
    # Translatable: {lang_code: string}, e.g. {"en": "Nails", "uk": "Нігті"}.
    # Empty dict is the "no translations yet" state (docs/DECISIONS.md
    # § Stage 11.5). Resolution + English fallback is a serializer concern
    # (sub-step 2), not done here.
    name = models.JSONField(default=dict)
    ordering = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta(TenantScopedModel.Meta):
        abstract = False
        constraints = [
            *TenantScopedModel.Meta.constraints,
            # (salon, name) uniqueness is enforced per populated language key,
            # not against one canonical language (docs/DECISIONS.md
            # § Stage 11.5 "Uniqueness on (salon, name) translatable fields").
            # One constraint per supported language; a new language means a
            # new pair of these in a migration.
            models.UniqueConstraint(
                "salon",
                NullIf(KeyTextTransform("en", "name"), Value(""), output_field=TextField()),
                name="servicecategory_salon_name_en_uniq",
            ),
            models.UniqueConstraint(
                "salon",
                NullIf(KeyTextTransform("uk", "name"), Value(""), output_field=TextField()),
                name="servicecategory_salon_name_uk_uniq",
            ),
        ]
        # `name` is a JSON dict now, not a scalar — it can't be a meaningful
        # ORDER BY key. `id` is the deterministic tiebreak (was `name`).
        ordering = ["ordering", "id"]

    def __str__(self) -> str:
        return resolve_display_name(self.name, f"ServiceCategory #{self.pk}")


class Service(TenantScopedModel, TimeStamped):
    """
    docs/ARCHITECTURE.md § 2. `buffer_minutes` blocks the calendar after the
    appointment but is never itself a bookable start (§ 6-7).
    """

    category = models.ForeignKey(ServiceCategory, on_delete=models.PROTECT, related_name="services")
    # Translatable: {lang_code: string} — see ServiceCategory.name.
    name = models.JSONField(default=dict)
    duration_minutes = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    buffer_minutes = models.PositiveIntegerField(default=0)
    ordering = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta(TenantScopedModel.Meta):
        abstract = False
        constraints = [
            *TenantScopedModel.Meta.constraints,
            models.UniqueConstraint(
                "salon",
                NullIf(KeyTextTransform("en", "name"), Value(""), output_field=TextField()),
                name="service_salon_name_en_uniq",
            ),
            models.UniqueConstraint(
                "salon",
                NullIf(KeyTextTransform("uk", "name"), Value(""), output_field=TextField()),
                name="service_salon_name_uk_uniq",
            ),
        ]
        ordering = ["ordering", "id"]

    def __str__(self) -> str:
        return resolve_display_name(self.name, f"Service #{self.pk}")
