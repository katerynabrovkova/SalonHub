"""
Tenant resolution middleware (docs/ARCHITECTURE.md § 5, docs/DECISIONS.md §
Multi-tenancy / § Stage 3 decisions). This is the only place in the codebase
that inspects a URL to decide which salon a request belongs to — swapping
path-prefix resolution for subdomain-based resolution later means editing
only this file.
"""

import re
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse, HttpResponseNotFound

from core.tenancy import tenant_context
from tenants.models import Salon

_SALON_PATH_RE = re.compile(r"^/api/v1/salons/(?P<slug>[^/]+)/")


class TenantResolutionMiddleware:
    """
    Binds the current tenant for the duration of a request whose path
    matches `/api/v1/salons/<slug>/...`. Paths outside that prefix (health
    check, Django admin, `/api/v1/auth/...`) pass through with no tenant
    bound — those views must never touch tenant-scoped models without
    deliberately choosing a salon first.

    An unknown or inactive slug returns 404, not 403: the slug is part of the
    URL path, not a credential, so a nonexistent salon is a routing miss, and
    404 avoids confirming/denying slug existence differently to authorized
    vs. unauthorized callers.

    Placed last in MIDDLEWARE (config/settings/base.py): tenant resolution
    has no dependency on request.user (slug lookup is independent of
    identity, and JWT auth runs inside DRF view dispatch, not in
    AuthenticationMiddleware). Last position keeps the bound context's
    lifetime as narrow as possible — it wraps only the view, not any other
    middleware's request/response processing.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        match = _SALON_PATH_RE.match(request.path)
        if match is None:
            return self.get_response(request)

        salon = Salon.objects.filter(slug=match.group("slug"), is_active=True).first()
        if salon is None:
            return HttpResponseNotFound()

        with tenant_context(salon.id):
            return self.get_response(request)
