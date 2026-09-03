from collections.abc import Callable

import pytest
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory

from core.tenancy import get_current_salon_id
from tenants.middleware import TenantResolutionMiddleware
from tenants.models import Salon

factory = RequestFactory()


def _echo_current_salon_id(captured: dict) -> Callable[[HttpRequest], HttpResponse]:
    def get_response(request: HttpRequest) -> HttpResponse:
        captured["salon_id"] = get_current_salon_id()
        return HttpResponse()

    return get_response


@pytest.mark.django_db
def test_binds_salon_id_for_a_matching_active_slug(salon) -> None:
    captured: dict = {}
    middleware = TenantResolutionMiddleware(_echo_current_salon_id(captured))
    request = factory.get(f"/api/v1/salons/{salon.slug}/services/")

    response = middleware(request)

    assert response.status_code == 200
    assert captured["salon_id"] == salon.id


@pytest.mark.django_db
def test_clears_salon_id_after_the_response() -> None:
    salon = Salon.objects.create(
        name="Clears Salon",
        slug="clears-salon",
        currency="UAH",
        contact_email="owner@clears-salon.example",
    )
    middleware = TenantResolutionMiddleware(lambda request: HttpResponse())
    request = factory.get(f"/api/v1/salons/{salon.slug}/services/")

    middleware(request)

    assert get_current_salon_id() is None


@pytest.mark.django_db
def test_clears_salon_id_even_when_the_view_raises() -> None:
    def get_response(request: HttpRequest) -> HttpResponse:
        raise RuntimeError("view exploded")

    salon = Salon.objects.create(
        name="Raises Salon",
        slug="raises-salon",
        currency="UAH",
        contact_email="owner@raises-salon.example",
    )
    middleware = TenantResolutionMiddleware(get_response)
    request = factory.get(f"/api/v1/salons/{salon.slug}/services/")

    with pytest.raises(RuntimeError):
        middleware(request)

    assert get_current_salon_id() is None


@pytest.mark.django_db
def test_unknown_slug_returns_404_and_binds_no_context() -> None:
    calls: list[bool] = []
    middleware = TenantResolutionMiddleware(lambda request: calls.append(True) or HttpResponse())
    request = factory.get("/api/v1/salons/does-not-exist/services/")

    response = middleware(request)

    assert response.status_code == 404
    assert calls == []  # get_response was never called
    assert get_current_salon_id() is None


@pytest.mark.django_db
def test_inactive_slug_returns_404() -> None:
    salon = Salon.objects.create(
        name="Inactive Salon",
        slug="inactive-salon",
        is_active=False,
        currency="UAH",
        contact_email="owner@inactive-salon.example",
    )
    middleware = TenantResolutionMiddleware(lambda request: HttpResponse())
    request = factory.get(f"/api/v1/salons/{salon.slug}/services/")

    response = middleware(request)

    assert response.status_code == 404


@pytest.mark.django_db
def test_paths_outside_the_salon_prefix_bind_no_context() -> None:
    captured: dict = {}
    middleware = TenantResolutionMiddleware(_echo_current_salon_id(captured))

    for path in ["/admin/", "/api/v1/auth/login/", "/api/v1/me/", "/healthz/"]:
        response = middleware(factory.get(path))
        assert response.status_code == 200
        assert captured["salon_id"] is None


@pytest.mark.django_db
def test_tenant_context_does_not_leak_between_sequential_requests(salon, other_salon) -> None:
    """
    Regression guard for the explicit Stage 3 requirement that tenant context
    must not leak from one request into the next. A middleware bug that binds
    without a matching reset would only show up when a second request follows
    a first — a single isolated request/response test can't catch it.
    """
    captured: dict = {}
    middleware = TenantResolutionMiddleware(_echo_current_salon_id(captured))

    middleware(factory.get(f"/api/v1/salons/{salon.slug}/services/"))
    assert get_current_salon_id() is None  # cleared immediately after request A

    middleware(factory.get("/api/v1/me/"))  # request B binds nothing itself
    assert captured["salon_id"] is None  # ...and doesn't see request A's leftovers

    middleware(factory.get(f"/api/v1/salons/{other_salon.slug}/services/"))
    assert captured["salon_id"] == other_salon.id  # request C binds its own tenant, not A's
