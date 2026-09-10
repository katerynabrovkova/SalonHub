"""
Frontend-URL construction for emailed links (docs/DECISIONS.md § Stage 12
"Frontend routing: subdomain-based").

Guest manage links and the credential emails (verify-email, password-reset)
point at the tenant's own subdomain, not a shared host with a `/salons/<slug>/`
path prefix. ``settings.FRONTEND_URL`` carries a ``{slug}`` placeholder
(e.g. ``"http://{slug}.localhost:3000"``) that this helper fills, so no call
site re-implements the ``.format(slug=...)`` step.
"""

from django.conf import settings


def build_salon_frontend_url(slug: str, path: str) -> str:
    """
    Build a subdomain-based frontend URL for a given salon slug.

    ``path`` must start with ``"/"``. Uses ``settings.FRONTEND_URL``, which
    contains a ``{slug}`` placeholder (e.g. ``"http://{slug}.localhost:3000"``).
    """
    base = settings.FRONTEND_URL.format(slug=slug)
    return f"{base}{path}"
