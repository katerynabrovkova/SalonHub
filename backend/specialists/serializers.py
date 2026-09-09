"""
Specialist serializers (docs/ARCHITECTURE.md § 2, § 4; Stage 5 sub-step 4,
Stage 11.5 sub-step 2 step 6).

`salon` is always read-only — it comes from the URL's tenant context
(tenants.middleware.TenantResolutionMiddleware), never from client input, same
convention as catalog/serializers.py.

Read/write split (docs/DECISIONS.md § Stage 11.5 "Read/write serializer split
for catalog and specialists"): `SpecialistReadSerializer` resolves the
translatable `name`/`bio` to a plain string for the request's ``?lang=``;
`SpecialistWriteSerializer` takes/echoes the full ``{lang_code: string}`` dict
and validates its keys against `core.i18n.SUPPORTED_LANGUAGES`. Unlike
catalog, `Specialist` has no ``(salon, name)`` uniqueness constraint, so there
is no per-language uniqueness check here — only the key allowlist.
"""

from typing import Any

from rest_framework import serializers

from catalog.models import Service
from core.i18n import SUPPORTED_LANGUAGES, resolve_translation
from specialists.models import Specialist


def _reject_unsupported_language_keys(value: dict[str, str]) -> dict[str, str]:
    """Every key must be a supported language code — an unsupported key is
    rejected, never silently dropped (this is a write path; a dropped key
    would look like a successful save). Same rule and generic phrasing as
    catalog's write serializers (docs/DECISIONS.md § Stage 11.5 "Write-side
    language key validation")."""
    for lang in value:
        if lang not in SUPPORTED_LANGUAGES:
            raise serializers.ValidationError(f"Unsupported language code: {lang!r}")
    return value


class SpecialistWriteSerializer(serializers.ModelSerializer):
    """
    Write representation: `name`/`bio` are the full ``{lang_code: string}``
    dicts, echoed back as-is; `services` is the writable M2M link. Read
    requests use SpecialistReadSerializer instead.

    `services` is the M2M link to catalog.Service, through SpecialistService
    (specialists/models.py). Its real queryset can't be built at
    class-body/import time — TenantScopedManager.get_queryset() raises
    immediately if no tenant is bound, and nothing is bound at module import —
    so the class body wires it to a harmless, always-empty placeholder
    (Service.unscoped_objects.none(), a plain non-tenant-scoped manager, so
    building it doesn't touch tenant context at all), and __init__ rebinds it
    to the real tenant-scoped queryset once a request is actually being
    served — on `.child_relation`, not on the field itself (many=True wraps
    the declared PrimaryKeyRelatedField in a ManyRelatedField that has no
    `queryset` of its own; see the comment in __init__ below). A service id
    belonging to another salon is then simply absent from that scoped
    queryset — PrimaryKeyRelatedField.to_internal_value() (called once per
    item by the ManyRelatedField wrapper) does self.get_queryset().get(pk=data),
    that lookup misses, and DRF raises its ordinary "does not exist"
    ValidationError under the "services" key: a 400 with a field-specific
    error, not a cross-tenant leak and not a 500. The
    composite tenant FK on SpecialistService
    (specialists/migrations/0001_initial.py) is the actual guarantee
    underneath this — this check only turns that DB-level rejection into a
    clean error message instead of an IntegrityError surfacing as a 500.

    SpecialistService also carries a required `salon` field with no default,
    so the M2M can't be saved via the plain ModelSerializer default
    (instance.services.set(value)) — that omits through_defaults entirely and
    would hit SpecialistService's NOT NULL constraint on salon. create()/
    update() are overridden below to supply through_defaults={"salon_id":
    instance.salon_id} explicitly.
    """

    services = serializers.PrimaryKeyRelatedField(
        many=True, required=False, queryset=Service.unscoped_objects.none()
    )

    class Meta:
        model = Specialist
        fields = ["id", "salon", "name", "bio", "is_active", "services", "created_at", "updated_at"]
        read_only_fields = ["id", "salon", "created_at", "updated_at"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # many=True wraps the declared PrimaryKeyRelatedField in a
        # ManyRelatedField (relations.py's many_init()); the wrapper itself
        # has no `queryset` attribute at all — the real one lives on
        # `.child_relation`, the actual PrimaryKeyRelatedField instance that
        # to_internal_value() delegates to per item. Rebinding the wrapper's
        # own (nonexistent) `.queryset` here would silently set an unused
        # attribute and leave validation running against the empty
        # class-body placeholder — caught by
        # test_attaching_a_service_from_the_same_salon_succeeds failing red.
        self.fields["services"].child_relation.queryset = Service.objects.all()

    def validate_name(self, value: dict[str, str]) -> dict[str, str]:
        return _reject_unsupported_language_keys(value)

    def validate_bio(self, value: dict[str, str]) -> dict[str, str]:
        return _reject_unsupported_language_keys(value)

    def create(self, validated_data: dict[str, Any]) -> Specialist:
        services = validated_data.pop("services", [])
        instance = Specialist.objects.create(**validated_data)
        if services:
            instance.services.set(services, through_defaults={"salon_id": instance.salon_id})
        return instance

    def update(self, instance: Specialist, validated_data: dict[str, Any]) -> Specialist:
        services = validated_data.pop("services", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if services is not None:
            instance.services.set(services, through_defaults={"salon_id": instance.salon_id})
        return instance


class SpecialistReadSerializer(serializers.ModelSerializer):
    """
    Read representation: `name`/`bio` resolved to a plain string for the
    request's ``?lang=`` (docs/DECISIONS.md § Stage 11.5), never the raw dict.
    `services` stays a read-only list of ids.
    """

    name = serializers.SerializerMethodField()
    bio = serializers.SerializerMethodField()
    services = serializers.PrimaryKeyRelatedField(many=True, read_only=True)

    class Meta:
        model = Specialist
        fields = ["id", "salon", "name", "bio", "is_active", "services", "created_at", "updated_at"]
        read_only_fields = fields

    def _resolved(self, value: dict[str, str]) -> str:
        request = self.context.get("request")
        requested_lang = request.query_params.get("lang") if request is not None else None
        return resolve_translation(value, requested_lang)

    def get_name(self, obj: Specialist) -> str:
        return self._resolved(obj.name)

    def get_bio(self, obj: Specialist) -> str:
        return self._resolved(obj.bio)
