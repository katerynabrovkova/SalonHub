"""
Stage 16 sub-step 9 — the assistant's daily usage counters (docs/DECISIONS.md
§ Stage 16 planning, "Sub-step 9 details").

Aggregates only: `ai_assistant` stores no conversation text and nothing
about individual customers. Two tables rather than one with a nullable
service, because Postgres treats NULLs as distinct in a unique constraint.
Rows are written only through ai_assistant.analytics.
"""

from django.db import models

from catalog.models import Service
from core.models import TenantScopedModel


class DailyConversationCount(TenantScopedModel):
    """Conversations started per salon-local day."""

    date = models.DateField()
    count = models.PositiveIntegerField(default=0)

    class Meta(TenantScopedModel.Meta):
        abstract = False
        constraints = [
            *TenantScopedModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["salon", "date"], name="ai_assistant_conversation_count_salon_date_uniq"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.date.isoformat()}: {self.count} conversations"


class DailyProposalCount(TenantScopedModel):
    """Emitted proposal cards per salon-local day and service."""

    date = models.DateField()
    # PROTECT like Appointment.service: services are only soft-deleted, and
    # a counter row is history.
    service = models.ForeignKey(Service, on_delete=models.PROTECT, related_name="+")
    count = models.PositiveIntegerField(default=0)

    class Meta(TenantScopedModel.Meta):
        abstract = False
        constraints = [
            *TenantScopedModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["salon", "date", "service"],
                name="ai_assistant_proposal_count_salon_date_service_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.date.isoformat()}: {self.count} proposals for service {self.service_id}"
