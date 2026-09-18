"""
Tenants API tests (docs/DECISIONS.md § "Resolution: frontend price display
shows no currency unit", part (B)).
"""

import pytest
from rest_framework.test import APIClient

from tenants.models import Salon

pytestmark = pytest.mark.django_db


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def _salon_detail_url(salon) -> str:
    return f"/api/v1/salons/{salon.slug}/"


# --- public read: returns the requested salon's own currency ---------------


def test_get_salon_info_returns_currency(client, salon):
    response = client.get(_salon_detail_url(salon))

    assert response.status_code == 200
    assert response.json() == {"currency": "UAH"}


def test_get_salon_info_returns_the_requesting_salons_own_currency(client, salon):
    other_currency_salon = Salon.objects.create(
        name={"en": "Dollar Salon"},
        slug="dollar-salon",
        currency="USD",
        contact_email="owner@dollar-salon.example",
    )

    bella_response = client.get(_salon_detail_url(salon))
    other_response = client.get(_salon_detail_url(other_currency_salon))

    assert bella_response.status_code == 200
    assert bella_response.json()["currency"] == "UAH"
    assert other_response.status_code == 200
    assert other_response.json()["currency"] == "USD"


# --- unknown slug: 404 -------------------------------------------------------


def test_get_salon_info_404_for_unknown_slug(client):
    response = client.get("/api/v1/salons/no-such-salon/")

    assert response.status_code == 404


# --- public read: no auth required -------------------------------------------


def test_get_salon_info_is_public(client, salon):
    response = client.get(_salon_detail_url(salon))

    assert response.status_code == 200


# --- narrow response shape: currency only, nothing else --------------------


def test_get_salon_info_only_exposes_currency(client, salon):
    response = client.get(_salon_detail_url(salon))

    assert response.status_code == 200
    assert set(response.json().keys()) == {"currency"}
