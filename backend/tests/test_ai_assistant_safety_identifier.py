"""
Stage 16 sub-step 10a — the `safety_identifier` sent to the model provider
(docs/DECISIONS.md § "Sub-step 10 details", "`safety_identifier`").

HMAC-SHA256 of the salon id and the client IP, keyed with
ASSISTANT_SAFETY_IDENTIFIER_SECRET (its own setting, never SECRET_KEY);
only the hex digest leaves the backend. The message is
"<salon id>:<client IP>": the separator keeps salon 1 + "21.0.0.1" apart
from salon 12 + "1.0.0.1".

Pure function, no database: nothing here is @pytest.mark.django_db.
"""

import hashlib
import hmac

import pytest

from ai_assistant.safety_identifier import compute_safety_identifier

SECRET = "test-safety-identifier-secret-5d1e"
SALON_ID = 987654
CLIENT_IP = "203.0.113.45"


@pytest.fixture(autouse=True)
def _secret(settings):
    settings.ASSISTANT_SAFETY_IDENTIFIER_SECRET = SECRET


def test_is_the_hmac_sha256_hex_digest_of_salon_id_and_ip():
    expected = hmac.new(
        SECRET.encode(), f"{SALON_ID}:{CLIENT_IP}".encode(), hashlib.sha256
    ).hexdigest()

    assert compute_safety_identifier(salon_id=SALON_ID, client_ip=CLIENT_IP) == expected


def test_is_stable_for_the_same_salon_and_ip():
    first = compute_safety_identifier(salon_id=SALON_ID, client_ip=CLIENT_IP)
    second = compute_safety_identifier(salon_id=SALON_ID, client_ip=CLIENT_IP)

    assert first == second


def test_differs_for_another_salon():
    own = compute_safety_identifier(salon_id=SALON_ID, client_ip=CLIENT_IP)
    other = compute_safety_identifier(salon_id=SALON_ID + 1, client_ip=CLIENT_IP)

    assert own != other


def test_differs_for_another_ip():
    own = compute_safety_identifier(salon_id=SALON_ID, client_ip=CLIENT_IP)
    other = compute_safety_identifier(salon_id=SALON_ID, client_ip="203.0.113.46")

    assert own != other


def test_differs_with_another_secret(settings):
    own = compute_safety_identifier(salon_id=SALON_ID, client_ip=CLIENT_IP)
    settings.ASSISTANT_SAFETY_IDENTIFIER_SECRET = "another-secret"
    other = compute_safety_identifier(salon_id=SALON_ID, client_ip=CLIENT_IP)

    assert own != other


def test_contains_neither_the_ip_nor_the_salon_id():
    identifier = compute_safety_identifier(salon_id=SALON_ID, client_ip=CLIENT_IP)

    assert len(identifier) == 64
    assert set(identifier) <= set("0123456789abcdef")
    assert CLIENT_IP not in identifier
    assert str(SALON_ID) not in identifier


def test_separator_keeps_salon_id_and_ip_apart():
    assert compute_safety_identifier(salon_id=1, client_ip="21.0.0.1") != (
        compute_safety_identifier(salon_id=12, client_ip="1.0.0.1")
    )
