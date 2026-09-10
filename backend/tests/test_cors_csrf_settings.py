"""
Stage 12 — CORS / CSRF settings for subdomain-based frontend routing
(docs/DECISIONS.md § "Frontend routing: subdomain-based").

RED phase: written against the agreed contract before the settings change.
The contract:

* ``CORS_ALLOWED_ORIGIN_REGEXES`` replaces the single hardcoded
  ``CORS_ALLOWED_ORIGINS = ["http://localhost:3000"]`` with two fully
  anchored patterns:
  - dev:  any ``<slug>.localhost:3000`` subdomain over http
  - prod: any ``<slug>.<PLATFORM_DOMAIN>`` subdomain over https
* ``PLATFORM_DOMAIN`` is a new env var, default ``"salonhub.com"`` (a
  placeholder, not a confirmed purchased domain).
* ``CSRF_TRUSTED_ORIGINS`` gains a wildcard prod entry
  ``https://*.<PLATFORM_DOMAIN>``.

Today none of these exist in this form, so this file fails: the
``CORS_ALLOWED_ORIGIN_REGEXES`` and ``CSRF_TRUSTED_ORIGINS`` settings are
unset (AttributeError), as is ``PLATFORM_DOMAIN``.
"""

import re

from django.conf import settings


def _platform_domain() -> str:
    return settings.PLATFORM_DOMAIN


def _matches_any(origin: str) -> bool:
    return any(re.match(pattern, origin) for pattern in settings.CORS_ALLOWED_ORIGIN_REGEXES)


def test_dev_subdomain_origin_matches() -> None:
    assert _matches_any("http://bella.localhost:3000")


def test_dev_hyphenated_slug_origin_matches() -> None:
    assert _matches_any("http://bella-demo.localhost:3000")


def test_prod_subdomain_origin_matches() -> None:
    assert _matches_any(f"https://bella.{_platform_domain()}")


def test_unrelated_origin_does_not_match() -> None:
    assert not _matches_any("https://evil.com")


def test_patterns_are_anchored_against_suffix_spoofing() -> None:
    # A fully anchored regex must reject a host that merely starts with an
    # allowed origin — not a substring match.
    assert not _matches_any("http://bella.localhost:3000.evil.com")


def test_csrf_trusted_origins_is_non_empty_list() -> None:
    assert isinstance(settings.CSRF_TRUSTED_ORIGINS, list)
    assert settings.CSRF_TRUSTED_ORIGINS


def test_csrf_trusted_origins_has_wildcard_prod_entry() -> None:
    assert f"https://*.{_platform_domain()}" in settings.CSRF_TRUSTED_ORIGINS
