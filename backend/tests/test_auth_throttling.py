import pytest
from rest_framework.test import APIClient

from accounts.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def test_login_is_throttled_after_the_configured_rate(client: APIClient, salon) -> None:
    User.objects.create_user(email="throttle-login@example.com", password="a-strong-passw0rd!")

    for _ in range(5):  # login: 5/min (docs/DECISIONS.md § Stage 3 decisions)
        response = client.post(
            f"/api/v1/salons/{salon.slug}/auth/login/",
            {"email": "throttle-login@example.com", "password": "wrong-password"},
        )
        assert response.status_code == 401

    response = client.post(
        f"/api/v1/salons/{salon.slug}/auth/login/",
        {"email": "throttle-login@example.com", "password": "wrong-password"},
    )
    assert response.status_code == 429
