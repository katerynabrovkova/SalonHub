"""
Query-param validation for the availability time-grid GET endpoint
(docs/DECISIONS.md § Stage 6.I decisions). A plain serializers.Serializer,
not a ModelSerializer — there's no model instance to serialize here, only
request.query_params to validate.
"""

import datetime as dt

from django.utils import timezone
from rest_framework import serializers

from catalog.models import Service
from core.i18n import resolve_translation
from specialists.models import Specialist


class AvailabilityQuerySerializer(serializers.Serializer):
    """
    `service`/`date_from`/`date_to` are required and load-bearing for the
    computation itself, unlike catalog's `category` list filter (an optional
    refinement, silently ignored when malformed) — malformed input here must
    hard-fail rather than silently degrade (§ Stage 6.I decisions).

    `service`/`specialist` are PrimaryKeyRelatedFields, rebound in __init__
    to the tenant-scoped querysets — the same pattern
    catalog/serializers.py's ServiceSerializer.category_id uses, not the
    .child_relation variant (neither field is many=True). The class-body
    placeholder queryset is a harmless, always-empty, non-tenant-scoped one
    (TenantScopedManager.get_queryset() raises with no tenant bound, and
    nothing is bound at import time) — __init__ rebinds it once a request is
    actually being served. A cross-tenant or nonexistent id then simply
    misses that scoped queryset and surfaces as DRF's ordinary "does not
    exist" ValidationError, the same 400 as any other invalid id, not a
    cross-tenant leak.
    """

    service = serializers.PrimaryKeyRelatedField(queryset=Service.unscoped_objects.none())
    specialist = serializers.PrimaryKeyRelatedField(
        queryset=Specialist.unscoped_objects.none(), required=False
    )
    date_from = serializers.DateField()
    date_to = serializers.DateField()

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.fields["service"].queryset = Service.objects.all()
        self.fields["specialist"].queryset = Specialist.objects.all()


class _OffsetRequiredDateTimeField(serializers.DateTimeField):
    """
    Rejects a naive input instead of DRF's normal behavior under
    `USE_TZ = True` — silently treating it as the project's default
    timezone (§ Stage 6.K decisions). The offset exists precisely to
    disambiguate the submitted moment on a DST-transition day; silently
    defaulting a naive value would reintroduce exactly that ambiguity.
    `enforce_timezone` is the DRF hook that receives the parsed value before
    any such default is applied, so the naive/aware distinction the client
    actually sent is still visible here.
    """

    def enforce_timezone(self, value: dt.datetime) -> dt.datetime:
        if not timezone.is_aware(value):
            raise serializers.ValidationError(
                "Datetime must include a UTC offset (e.g. '+03:00').", code="naive"
            )
        return super().enforce_timezone(value)


class SpecialistsAtTimeQuerySerializer(serializers.Serializer):
    """
    Query-param validation for the specialist-availability GET endpoint
    (docs/DECISIONS.md § Stage 6.K decisions) — same reasoning as
    AvailabilityQuerySerializer above: a plain serializers.Serializer, not a
    ModelSerializer, and both fields are required and load-bearing rather
    than an optional, silently-degrading refinement.

    `service` is a PrimaryKeyRelatedField rebound in __init__ to the
    tenant-scoped queryset, the same pattern as AvailabilityQuerySerializer.
    `datetime` is the offset-required field above, not a plain
    serializers.DateTimeField.
    """

    service = serializers.PrimaryKeyRelatedField(queryset=Service.unscoped_objects.none())
    datetime = _OffsetRequiredDateTimeField()

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.fields["service"].queryset = Service.objects.all()


class SpecialistAtTimeSerializer(serializers.ModelSerializer):
    """
    Response shape for a single specialist entry (§ Stage 6.K decisions):
    exactly photo/name/bio, nothing else. A ModelSerializer here, unlike the
    query serializers above — this one does serialize a real `Specialist`
    instance, not just validate query params.

    `name`/`bio` are translatable JSONFields (Stage 11.5): resolved to a
    plain string for the request's ``?lang=`` via
    `core.i18n.resolve_translation`, mirroring
    `specialists.serializers.SpecialistReadSerializer._resolved`. The view
    must build this serializer with ``context={"request": request}`` for the
    ``?lang=`` lookup to work.
    """

    name = serializers.SerializerMethodField()
    bio = serializers.SerializerMethodField()

    class Meta:
        model = Specialist
        fields = ["photo", "name", "bio"]

    def _resolved(self, value: dict[str, str]) -> str:
        request = self.context.get("request")
        requested_lang = request.query_params.get("lang") if request is not None else None
        return resolve_translation(value, requested_lang)

    def get_name(self, obj: Specialist) -> str:
        return self._resolved(obj.name)

    def get_bio(self, obj: Specialist) -> str:
        return self._resolved(obj.bio)
