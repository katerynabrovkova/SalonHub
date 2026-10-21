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
    assert response.json() == {
        "currency": "UAH",
        "assistant_enabled": False,
        "assistant_starters": [],
    }


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


# --- narrow response shape: allowlisted keys only, nothing else ------------


def test_get_salon_info_only_exposes_allowlisted_fields(client, salon):
    response = client.get(_salon_detail_url(salon))

    assert response.status_code == 200
    assert set(response.json().keys()) == {"currency", "assistant_enabled", "assistant_starters"}


def test_get_salon_info_does_not_expose_contacts(client, salon):
    salon.phone = "+380 44 000 0000"
    salon.address = "1 Demo Street, Kyiv"
    salon.instagram_url = "https://instagram.com/bella-demo"
    salon.save(update_fields=["phone", "address", "instagram_url"])

    response = client.get(_salon_detail_url(salon))

    assert response.status_code == 200
    body = response.json()
    assert "phone" not in body
    assert "address" not in body
    assert "instagram_url" not in body


# --- assistant starters: resolved per ?lang=, empty items dropped -----------


def test_get_salon_info_resolves_starters_for_requested_lang(client, salon):
    salon.assistant_starters = [
        {"en": "Book a manicure", "uk": "Записатися на манікюр"},
        {"en": "What are your prices?", "uk": "Які у вас ціни?"},
    ]
    salon.save(update_fields=["assistant_starters"])

    response = client.get(_salon_detail_url(salon), {"lang": "uk"})

    assert response.status_code == 200
    assert response.json().get("assistant_starters") == [
        "Записатися на манікюр",
        "Які у вас ціни?",
    ]


def test_get_salon_info_starters_fall_back_and_drop_empty_items(client, salon):
    salon.assistant_starters = [
        {"en": "Book a manicure"},
        {"en": "", "uk": ""},
        {"en": "What are your prices?", "uk": "Які у вас ціни?"},
    ]
    salon.save(update_fields=["assistant_starters"])

    response = client.get(_salon_detail_url(salon), {"lang": "uk"})

    assert response.status_code == 200
    assert response.json().get("assistant_starters") == [
        "Book a manicure",
        "Які у вас ціни?",
    ]


def test_get_salon_info_returns_each_salons_own_assistant_settings(client, salon, other_salon):
    salon.assistant_enabled = True
    salon.assistant_starters = [{"en": "Book a manicure"}]
    salon.save(update_fields=["assistant_enabled", "assistant_starters"])
    other_salon.assistant_starters = [{"en": "Book a pedicure"}]
    other_salon.save(update_fields=["assistant_starters"])

    salon_response = client.get(_salon_detail_url(salon))
    other_response = client.get(_salon_detail_url(other_salon))

    assert salon_response.status_code == 200
    assert salon_response.json() == {
        "currency": "UAH",
        "assistant_enabled": True,
        "assistant_starters": ["Book a manicure"],
    }
    assert other_response.status_code == 200
    assert other_response.json() == {
        "currency": "UAH",
        "assistant_enabled": False,
        "assistant_starters": ["Book a pedicure"],
    }
