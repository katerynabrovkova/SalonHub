"""
Stage 3-R.D.3 — client registration under
``POST /api/v1/salons/<slug>/auth/register/`` against ``Account``
(docs/DECISIONS.md § Stage 3-R.D.3).

No-enumeration holds on two independent levels, each pinned by its own test:

* the tenant-scoped ``Account.objects`` manager hides *other* salons' rows
  from the duplicate check —
  ``test_same_email_registers_independently_at_two_salons``;
* an identical ``202`` (same status, same body) hides whether the email
  already exists *within* this salon —
  ``test_duplicate_email_same_salon_returns_identical_202_and_creates_no_second_row``.

The only ``400`` this endpoint ever returns is field format / password
strength (``test_weak_password...``, ``test_missing_fields...``) — never an
existence signal.

D.3 scope: create the unverified ``Account`` + send the verification email.
The verify endpoint that consumes the token is D.4; login is 3-R.E.
"""

import pytest
from django.conf import settings
from django.core import mail
from rest_framework.test import APIClient

from accounts.models import Account

pytestmark = pytest.mark.django_db

STRONG_PASSWORD = "a-strong-passw0rd!"


@pytest.fixture
def client() -> APIClient:
    return APIClient()


@pytest.fixture(autouse=True)
def _subdomain_frontend_url(settings):
    # Credential-email links are subdomain-based: FRONTEND_URL carries a
    # `{slug}` placeholder filled per salon via `.format(slug=...)` — see
    # docs/DECISIONS.md § "Frontend routing: subdomain-based". Mirrors the
    # production `.env` pattern (`FRONTEND_URL=http://{slug}.localhost:3000`).
    settings.FRONTEND_URL = "http://{slug}.testserver"


def _register_url(slug: str) -> str:
    return f"/api/v1/salons/{slug}/auth/register/"


def _payload(
    email: str = "new-client@example.com", password: str = STRONG_PASSWORD
) -> dict[str, str]:
    return {"email": email, "password": password}


# --- 1. happy path --------------------------------------------------------


def test_register_creates_unverified_client_account_and_sends_salon_aware_email(
    client, salon
) -> None:
    response = client.post(_register_url(salon.slug), _payload(), format="json")

    assert response.status_code == 202

    account = Account.unscoped_objects.get(email="new-client@example.com")
    assert account.salon_id == salon.id
    assert account.role == "client"
    assert account.email_verified_at is None
    assert account.check_password(STRONG_PASSWORD)

    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == ["new-client@example.com"]
    assert f"{settings.FRONTEND_URL.format(slug=salon.slug)}/verify-email#token=" in sent.body


# --- 2. cross-salon isolation (no-enumeration, level 1) ------------------


def test_same_email_registers_independently_at_two_salons(client, salon, other_salon) -> None:
    email = "shared@example.com"

    first = client.post(_register_url(salon.slug), _payload(email=email), format="json")
    second = client.post(_register_url(other_salon.slug), _payload(email=email), format="json")

    assert first.status_code == 202
    assert second.status_code == 202

    rows = Account.unscoped_objects.filter(email=email)
    assert rows.count() == 2
    assert {row.salon_id for row in rows} == {salon.id, other_salon.id}


# --- 3. within-salon duplicate (no-enumeration, level 2) ----------------


def test_duplicate_email_same_salon_returns_identical_202_and_creates_no_second_row(
    client, salon
) -> None:
    email = "dup@example.com"

    first = client.post(_register_url(salon.slug), _payload(email=email), format="json")
    assert first.status_code == 202
    assert len(mail.outbox) == 1

    second = client.post(_register_url(salon.slug), _payload(email=email), format="json")

    # Identical response — same status, same body — as the new-email path.
    assert second.status_code == first.status_code
    assert second.data == first.data
    # No second row, and the duplicate call sent no further email.
    assert Account.unscoped_objects.filter(salon=salon, email=email).count() == 1
    assert len(mail.outbox) == 1


# --- 4. server-assigned salon ------------------------------------------


def test_salon_in_request_body_is_ignored(client, salon, other_salon) -> None:
    payload: dict[str, object] = dict(_payload(email="body-salon@example.com"))
    payload["salon"] = other_salon.id

    response = client.post(_register_url(salon.slug), payload, format="json")

    assert response.status_code == 202
    account = Account.unscoped_objects.get(email="body-salon@example.com")
    assert account.salon_id == salon.id


# --- 5. wiring guard --------------------------------------------------


def test_unknown_salon_slug_returns_404(client) -> None:
    response = client.post(_register_url("no-such-salon"), _payload(), format="json")

    assert response.status_code == 404
    assert Account.unscoped_objects.count() == 0


# --- 6/7. the only 400s are validation, never existence -----------------


def test_weak_password_is_rejected_400_with_no_account_and_no_email(client, salon) -> None:
    response = client.post(_register_url(salon.slug), _payload(password="123"), format="json")

    assert response.status_code == 400
    assert Account.unscoped_objects.count() == 0
    assert mail.outbox == []


def test_missing_fields_return_400(client, salon) -> None:
    response = client.post(_register_url(salon.slug), {}, format="json")

    assert response.status_code == 400


# --- 8. throttle -----------------------------------------------------


def test_register_is_throttled_after_the_configured_rate(client, salon) -> None:
    for i in range(3):  # register: 3/hour (docs/DECISIONS.md § Stage 3-R.D.3)
        response = client.post(
            _register_url(salon.slug),
            _payload(email=f"throttle-{i}@example.com"),
            format="json",
        )
        assert response.status_code == 202

    response = client.post(
        _register_url(salon.slug),
        _payload(email="throttle-over@example.com"),
        format="json",
    )
    assert response.status_code == 429
