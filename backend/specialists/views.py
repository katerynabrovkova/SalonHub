"""
Specialist API views (docs/ARCHITECTURE.md § 4, § 13; Stage 5 sub-step 5).

Reads are public (AllowAny); writes require IsSalonStaff for the resolved
tenant, same read/write split as catalog (docs/DECISIONS.md § Stage 4
decisions, § Stage 5 decisions). `salon` is assigned server-side only, from
the URL's bound tenant context — never from client input (both serializers
keep it read-only, see specialists/serializers.py).
"""

from django.db.models import QuerySet
from rest_framework import generics
from rest_framework.permissions import SAFE_METHODS, AllowAny, BasePermission
from rest_framework.request import Request
from rest_framework.serializers import BaseSerializer

from core.permissions import IsSalonStaff
from core.tenancy import get_current_salon_id
from specialists.models import Specialist
from specialists.serializers import SpecialistReadSerializer, SpecialistWriteSerializer
from specialists.services import soft_delete_specialist


class _SpecialistSerializerMixin:
    """
    Read/write serializer split (docs/DECISIONS.md § Stage 11.5 "Read/write
    serializer split for catalog and specialists"): GET resolves `name`/`bio`
    to a plain string for ``?lang=``; writes take/echo the full language dict.
    Mirrors catalog's `_ServiceSerializerMixin`.
    """

    request: Request  # set by DRF's APIView.dispatch

    def get_serializer_class(self) -> type[BaseSerializer]:
        if self.request.method in SAFE_METHODS:
            return SpecialistReadSerializer
        return SpecialistWriteSerializer


class _SpecialistViewMixin:
    """
    is_active=False specialists are hidden from every SAFE_METHODS read (list
    and single-object GET) unless the caller passes ?include_inactive=true
    AND is staff of *this* salon — same parameter name and semantics as
    catalog's _CatalogViewMixin (docs/DECISIONS.md § Stage 4 decisions
    "Catalog read semantics"). The filter is skipped entirely for
    non-SAFE_METHODS: applying it there would also hide a deactivated row
    from the very PATCH that reactivates it (docs/DECISIONS.md § Stage 4
    decisions "include_inactive=true only gates read visibility").

    Without this, reactivation is technically supported but practically
    unreachable: a deactivated specialist is invisible in every list, so
    staff would have no way to find its id to PATCH except psql or the admin.
    """

    request: Request  # set by DRF's APIView.dispatch

    def get_permissions(self) -> list[BasePermission]:
        if self.request.method in SAFE_METHODS:
            return [AllowAny()]
        return [IsSalonStaff()()]

    def _is_staff_here(self) -> bool:
        return IsSalonStaff()().has_permission(self.request, self)  # type: ignore[arg-type]

    def _apply_visibility(self, queryset: QuerySet) -> QuerySet:
        if self.request.method not in SAFE_METHODS:
            return queryset
        wants_inactive = self.request.query_params.get("include_inactive") == "true"
        if not (wants_inactive and self._is_staff_here()):
            queryset = queryset.filter(is_active=True)
        return queryset


class SpecialistListCreateView(
    _SpecialistSerializerMixin, _SpecialistViewMixin, generics.ListCreateAPIView
):
    def get_queryset(self) -> QuerySet[Specialist]:
        queryset = self._apply_visibility(Specialist.objects.all())
        service_id = self.request.query_params.get("service")
        if service_id and service_id.isdigit():
            queryset = queryset.filter(services__id=service_id)
        return queryset

    def perform_create(self, serializer: BaseSerializer) -> None:
        serializer.save(salon_id=get_current_salon_id())


class SpecialistDetailView(
    _SpecialistSerializerMixin, _SpecialistViewMixin, generics.RetrieveUpdateDestroyAPIView
):
    def get_queryset(self) -> QuerySet[Specialist]:
        return self._apply_visibility(Specialist.objects.all())

    def perform_destroy(self, instance: Specialist) -> None:
        soft_delete_specialist(instance)
