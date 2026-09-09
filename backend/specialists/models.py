from django.db import models

from catalog.models import Service
from core.models import TenantScopedModel, TimeStamped


class Specialist(TenantScopedModel, TimeStamped):
    # Translatable: {lang_code: string}. Empty dict is the "no translations
    # yet" state (docs/DECISIONS.md § Stage 11.5). Resolution + English
    # fallback is a serializer concern (sub-step 2), not done here.
    name = models.JSONField(default=dict)
    bio = models.JSONField(default=dict, blank=True)
    # Employment status only: True = currently employed, False = no longer
    # employed. Not a general availability/visibility flag — temporary
    # absence (vacation, sick leave, parental leave) is TimeOff rows, never
    # a status here (docs/DECISIONS.md § Stage 5 decisions).
    is_active = models.BooleanField(default=True)
    photo = models.CharField(max_length=1024, null=True, blank=True)

    services: "models.ManyToManyField[Service, SpecialistService]" = models.ManyToManyField(
        Service, through="SpecialistService", related_name="specialists"
    )

    class Meta(TenantScopedModel.Meta):
        abstract = False
        # `name` is a JSON dict now, not a scalar — it can't be a meaningful
        # ORDER BY key (was `["name", "id"]`). `id` stays as the deterministic
        # tiebreak, the same role it played before.
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return str(self.name)


class SpecialistService(TenantScopedModel, TimeStamped):
    """
    Explicit Specialist<->Service through model, not a bare ManyToManyField —
    needed so the pairing itself can carry the composite cross-tenant FK
    protection on both sides (docs/ARCHITECTURE.md § 5).
    """

    specialist = models.ForeignKey(Specialist, on_delete=models.CASCADE)
    service = models.ForeignKey(Service, on_delete=models.CASCADE)

    class Meta(TenantScopedModel.Meta):
        abstract = False
        constraints = [
            *TenantScopedModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["specialist", "service"], name="specialistservice_specialist_service_uniq"
            ),
        ]


class DayOfWeek(models.IntegerChoices):
    MONDAY = 0, "Monday"
    TUESDAY = 1, "Tuesday"
    WEDNESDAY = 2, "Wednesday"
    THURSDAY = 3, "Thursday"
    FRIDAY = 4, "Friday"
    SATURDAY = 5, "Saturday"
    SUNDAY = 6, "Sunday"


class WorkingHours(TenantScopedModel, TimeStamped):
    """Recurring weekly template. Multiple rows per day are allowed (split shifts)."""

    specialist = models.ForeignKey(
        Specialist, on_delete=models.CASCADE, related_name="working_hours"
    )
    day_of_week = models.IntegerField(choices=DayOfWeek.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta(TenantScopedModel.Meta):
        abstract = False
        constraints = [
            *TenantScopedModel.Meta.constraints,
            models.CheckConstraint(
                condition=models.Q(start_time__lt=models.F("end_time")),
                name="workinghours_start_before_end",
            ),
        ]


class TimeOff(TenantScopedModel, TimeStamped):
    """Dated exception to WorkingHours (illness, vacation, ...)."""

    specialist = models.ForeignKey(Specialist, on_delete=models.CASCADE, related_name="time_off")
    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField()
    reason = models.CharField(max_length=255, blank=True)

    class Meta(TenantScopedModel.Meta):
        abstract = False
        constraints = [
            *TenantScopedModel.Meta.constraints,
            models.CheckConstraint(
                condition=models.Q(start_datetime__lt=models.F("end_datetime")),
                name="timeoff_start_before_end",
            ),
        ]
