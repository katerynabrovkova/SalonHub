"""
Catalog serializers (docs/ARCHITECTURE.md § 2, § 4; Stage 4 sub-step 3).
`salon` is always read-only on both — it comes from the URL's tenant
context (tenants.middleware.TenantResolutionMiddleware), never from client
input.
"""

from typing import Any

from rest_framework import serializers

from catalog.models import Service, ServiceCategory
from core.i18n import SUPPORTED_LANGUAGES, resolve_translation


class ServiceCategoryWriteSerializer(serializers.ModelSerializer):
    """
    Write representation (docs/DECISIONS.md § Stage 11.5 "Read/write
    serializer split for catalog and specialists"): `name` is the full
    ``{lang_code: string}`` dict, echoed back as-is. Read requests use
    ServiceCategoryReadSerializer instead, which resolves `name` to a plain
    string.
    """

    class Meta:
        model = ServiceCategory
        fields = ["id", "salon", "name", "ordering", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "salon", "created_at", "updated_at"]

    def validate_name(self, value: dict[str, str]) -> dict[str, str]:
        """
        Two checks, both serializer-level (docs/DECISIONS.md § Stage 11.5):

        1. Every key is a supported language code. An unsupported key is
           rejected, never silently dropped — this is a write path and a
           dropped key would look like a successful save (§ "Write-side
           language key validation").
        2. Per-language ``(salon, name)`` uniqueness: for each populated key,
           another category in this salon must not already use that exact
           string under that same key. The message is deliberately generic —
           it does not name the colliding language.

        DRF's automatic UniqueTogetherValidator is silently skipped for the
        ``(salon, name)`` constraint — `salon` is read-only with no default
        (docs/DECISIONS.md § Stage 4 decisions) — so this is the only
        serializer-level guard. It is check-then-write, not race-proof: the
        per-language DB UniqueConstraints (catalog/models.py, sub-step 1) are
        the real guarantee, and core.exceptions.exception_handler turns a
        concurrent violation into the same 400 shape.
        """
        for lang in value:
            if lang not in SUPPORTED_LANGUAGES:
                raise serializers.ValidationError(f"Unsupported language code: {lang!r}")

        queryset = ServiceCategory.objects.all()
        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.pk)
        for lang, text in value.items():
            if not text:
                continue
            if queryset.filter(**{f"name__{lang}": text}).exists():
                raise serializers.ValidationError("A category with this name already exists.")
        return value


class ServiceCategoryReadSerializer(serializers.ModelSerializer):
    """
    Read representation: `name` resolved to a plain string for the request's
    ``?lang=`` (docs/DECISIONS.md § Stage 11.5 "API language contract"),
    never the raw dict.
    """

    name = serializers.SerializerMethodField()

    class Meta:
        model = ServiceCategory
        fields = ["id", "salon", "name", "ordering", "is_active", "created_at", "updated_at"]
        read_only_fields = fields

    def get_name(self, obj: ServiceCategory) -> str:
        request = self.context.get("request")
        requested_lang = request.query_params.get("lang") if request is not None else None
        return resolve_translation(obj.name, requested_lang)


class ServiceCategoryMiniSerializer(serializers.ModelSerializer):
    """Nested read-only summary of a Service's category — see ServiceSerializer."""

    class Meta:
        model = ServiceCategory
        fields = ["id", "name"]
        read_only_fields = fields


class ServiceSerializer(serializers.ModelSerializer):
    """
    `category` is a nested read-only summary; `category_id` is the writable
    FK. Its real queryset can't be built at class-body/import time —
    TenantScopedManager.get_queryset() raises immediately if no tenant is
    bound, and nothing is bound at module import — so the class body wires
    it to a harmless, always-empty placeholder
    (`ServiceCategory.unscoped_objects.none()`, a plain non-tenant-scoped
    manager, so building it doesn't touch tenant context at all), and
    __init__ rebinds it to the real tenant-scoped queryset once a request is
    actually being served, after the tenant-resolution middleware has bound
    one. A `category_id` for another salon is then simply absent from that
    scoped queryset — PrimaryKeyRelatedField.to_internal_value() does
    `self.get_queryset().get(pk=data)`, that lookup misses, and DRF raises
    its ordinary "does not exist" ValidationError, the same 400 as any other
    invalid id, not a cross-tenant leak.
    """

    category = ServiceCategoryMiniSerializer(read_only=True)
    category_id = serializers.PrimaryKeyRelatedField(
        source="category", queryset=ServiceCategory.unscoped_objects.none()
    )
    price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    duration_minutes = serializers.IntegerField(min_value=1)

    class Meta:
        model = Service
        fields = [
            "id",
            "salon",
            "category",
            "category_id",
            "name",
            "duration_minutes",
            "price",
            "buffer_minutes",
            "ordering",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "salon", "created_at", "updated_at"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["category_id"].queryset = ServiceCategory.objects.all()

    def validate_name(self, value: str) -> str:
        """See ServiceCategorySerializer.validate_name — same DRF gotcha,
        same (salon, name) constraint, same fix."""
        queryset = Service.objects.all()
        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.filter(name=value).exists():
            raise serializers.ValidationError("A service with this name already exists.")
        return value
