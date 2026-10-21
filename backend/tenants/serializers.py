"""
Tenants API serializers (docs/DECISIONS.md § "Resolution: frontend price
display shows no currency unit", part (B); § Stage 16 planning, "Sub-step 1
details").
"""

from rest_framework import serializers

from core.i18n import resolve_translation
from tenants.models import Salon


class SalonInfoSerializer(serializers.ModelSerializer):
    """
    Public per-salon info read, used by SalonInfoDetailView (tenants/views.py).
    Read-only: the endpoint is GET-only (a RetrieveAPIView with no write
    method), so every field is read-only — there is no write path to guard.
    Deliberately narrow: `currency` plus the assistant widget fields
    (`assistant_enabled`, `assistant_starters`). Every other Salon field
    stays backend-internal, including the customer-facing contacts
    (`phone`, `address`, `instagram_url`), which the assistant reads through
    its own tool until a frontend consumer exists.

    `assistant_starters` resolves each `{lang_code: string}` item to a plain
    string for the request's ``?lang=`` (docs/DECISIONS.md § Stage 11.5 "API
    language contract"); an item that resolves to "" is omitted.
    """

    assistant_starters = serializers.SerializerMethodField()

    class Meta:
        model = Salon
        fields = ["currency", "assistant_enabled", "assistant_starters"]
        read_only_fields = fields

    def get_assistant_starters(self, obj: Salon) -> list[str]:
        request = self.context.get("request")
        requested_lang = request.query_params.get("lang") if request is not None else None
        resolved = (resolve_translation(item, requested_lang) for item in obj.assistant_starters)
        return [text for text in resolved if text != ""]
