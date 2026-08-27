"""
Stage 3-R.D.2 — the `User`-based product-auth surface (registration, email
verification, password reset) was removed; only login/refresh/logout remain
under /api/v1/auth/ until 3-R.E (docs/DECISIONS.md § Stage 3-R.D.2). These
pin the removal so the endpoints can't be silently remounted — an absent
test guards nothing.
"""

import pytest
from rest_framework.test import APIClient


@pytest.fixture
def client() -> APIClient:
    return APIClient()


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/auth/register/",
        "/api/v1/auth/verify-email/",
        "/api/v1/auth/resend-verification/",
        "/api/v1/auth/password-reset/",
        "/api/v1/auth/password-reset/confirm/",
    ],
)
def test_removed_auth_endpoint_returns_404(client: APIClient, path: str) -> None:
    assert client.post(path, {}).status_code == 404
