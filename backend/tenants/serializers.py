"""
Tenants API serializers (docs/DECISIONS.md § "Resolution: frontend price
display shows no currency unit", part (B)).
"""

from rest_framework import serializers

from tenants.models import Salon


class SalonInfoSerializer(serializers.ModelSerializer):
    """
    Public per-salon info read, used by SalonInfoDetailView (tenants/views.py).
    Read-only: the endpoint is GET-only (a RetrieveAPIView with no write
    method), so every field is read-only — there is no write path to guard.
    Deliberately narrow (`currency` only) — every other Salon field
    (contact_email, deposit_percentage, min_lead_time_hours, ...) stays
    backend-internal.
    """

    class Meta:
        model = Salon
        fields = ["currency"]
        read_only_fields = fields
