"""
Catalog API views (docs/ARCHITECTURE.md § 4, § 13; Stage 4 sub-step 4).

Reads are public (AllowAny); writes require IsSalonStaff for the resolved
tenant (docs/DECISIONS.md § Stage 4 decisions "Catalog read semantics").
`salon` is assigned server-side only, from the URL's bound tenant context —
never from client input (both serializers keep it read-only, see
catalog/serializers.py).
"""

from django.db.models import QuerySet
from rest_framework import generics
from rest_framework.permissions import SAFE_METHODS, AllowAny, BasePermission
from rest_framework.request import Request
from rest_framework.serializers import BaseSerializer

from catalog.models import Service, ServiceCategory
from catalog.serializers import (
    ServiceCategoryReadSerializer,
    ServiceCategoryWriteSerializer,
    ServiceSerializer,
)
from catalog.services import soft_delete_category, soft_delete_service
from core.permissions import IsSalonStaff
from core.tenancy import get_current_salon_id


class _CatalogViewMixin:
    """
    Shared read/write permission split and is_active/include_inactive/search
    visibility filtering for both catalog resources.

    include_inactive=true only takes effect for a request that's staff of
    the *current* URL's salon — reuses IsSalonStaff's own has_permission
    check (rather than duplicating the Account admin-role lookup query) so
    staff of a different salon passing include_inactive=true against this
    salon's endpoint silently falls back to the ordinary public (active-only)
    result: not an error, not elevated access (docs/DECISIONS.md § Stage 4
    decisions "Catalog read semantics").
    """

    request: Request  # set by DRF's APIView.dispatch

    def get_permissions(self) -> list[BasePermission]:
        if self.request.method in SAFE_METHODS:
            return [AllowAny()]
        return [IsSalonStaff()()]

    def _is_staff_here(self) -> bool:
        return IsSalonStaff()().has_permission(self.request, self)  # type: ignore[arg-type]

    def _apply_visibility(self, queryset: QuerySet) -> QuerySet:
        """
        Applied on every SAFE_METHODS request (list and single-object GET)
        so a public caller, or staff without include_inactive=true, never
        sees a deactivated row. Skipped entirely for write methods
        (PATCH/PUT/DELETE): get_permissions() already restricted those to
        IsSalonStaff for this salon before get_queryset() ever runs (DRF
        checks permissions in dispatch(), ahead of any handler method), so
        there's no protective reason left to also hide a deactivated row
        from a same-salon staff member reactivating or editing it —
        requiring ?include_inactive=true on a PATCH would just be
        undiscoverable friction with no security benefit
        (docs/DECISIONS.md § Stage 4 decisions).
        """
        request = self.request
        if request.method not in SAFE_METHODS:
            return queryset
        wants_inactive = request.query_params.get("include_inactive") == "true"
        if not (wants_inactive and self._is_staff_here()):
            queryset = queryset.filter(is_active=True)
        search = request.query_params.get("search")
        if search:
            queryset = queryset.filter(name__icontains=search)
        return queryset


class _ServiceCategorySerializerMixin:
    """
    Read/write serializer split (docs/DECISIONS.md § Stage 11.5 "Read/write
    serializer split for catalog and specialists"): GET resolves `name` to a
    plain string for ``?lang=``; writes take/echo the full language dict.
    """

    request: Request  # set by DRF's APIView.dispatch

    def get_serializer_class(self) -> type[BaseSerializer]:
        if self.request.method in SAFE_METHODS:
            return ServiceCategoryReadSerializer
        return ServiceCategoryWriteSerializer


class ServiceCategoryListCreateView(
    _ServiceCategorySerializerMixin, _CatalogViewMixin, generics.ListCreateAPIView
):
    def get_queryset(self) -> QuerySet[ServiceCategory]:
        return self._apply_visibility(ServiceCategory.objects.all())

    def perform_create(self, serializer: BaseSerializer) -> None:
        serializer.save(salon_id=get_current_salon_id())


class ServiceCategoryDetailView(
    _ServiceCategorySerializerMixin, _CatalogViewMixin, generics.RetrieveUpdateDestroyAPIView
):
    def get_queryset(self) -> QuerySet[ServiceCategory]:
        return self._apply_visibility(ServiceCategory.objects.all())

    def perform_destroy(self, instance: ServiceCategory) -> None:
        soft_delete_category(instance)


class ServiceListCreateView(_CatalogViewMixin, generics.ListCreateAPIView):
    serializer_class = ServiceSerializer

    def get_queryset(self) -> QuerySet[Service]:
        queryset = self._apply_visibility(Service.objects.select_related("category"))
        category_id = self.request.query_params.get("category")
        if category_id and category_id.isdigit():
            queryset = queryset.filter(category_id=category_id)
        return queryset

    def perform_create(self, serializer: BaseSerializer) -> None:
        serializer.save(salon_id=get_current_salon_id())


class ServiceDetailView(_CatalogViewMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ServiceSerializer

    def get_queryset(self) -> QuerySet[Service]:
        return self._apply_visibility(Service.objects.select_related("category"))

    def perform_destroy(self, instance: Service) -> None:
        soft_delete_service(instance)
