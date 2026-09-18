"""
Tenants API views (docs/DECISIONS.md § "Resolution: frontend price display
shows no currency unit", part (B)).
"""

from django.shortcuts import get_object_or_404
from rest_framework import generics
from rest_framework.permissions import AllowAny

from core.tenancy import get_current_salon_id
from tenants.models import Salon
from tenants.serializers import SalonInfoSerializer


class SalonInfoDetailView(generics.RetrieveAPIView):
    """
    Public read of the current request's own salon (`GET
    /api/v1/salons/<slug>/`), same read-access posture as catalog's public
    reads (AllowAny). `Salon` isn't a TenantScopedModel — it's the tenant
    root, with no tenant-scoped manager to filter through the way catalog's
    views do — so the target row is resolved directly from the bound tenant
    context (`TenantResolutionMiddleware` / `core.tenancy.get_current_salon_id`),
    the same way `GuestBookingCreateView` resolves it (booking/views.py),
    never from a URL kwarg — this view's URL carries no further segment
    after the shared `<slug>` prefix.
    """

    permission_classes = [AllowAny]
    serializer_class = SalonInfoSerializer

    def get_object(self) -> Salon:
        return get_object_or_404(Salon, pk=get_current_salon_id())
