"""
Stage 9(d), commit A — the guest-token re-derivation function
(docs/DECISIONS.md § Step (d) decisions, guest-token delivery). Covers the
new `booking.guest_tokens.derive_guest_token(appointment_id) -> str`, not
yet written.

Written against the agreed design before any implementation exists — this
file is expected to fail on collection (ImportError: cannot import name
'derive_guest_token' from 'booking.guest_tokens') until it is written, same
two-step-red shape as test_booking_create_guest_appointment.py's own
docstring describes.

The central invariant under test: `derive_guest_token` must reproduce the
exact same raw token `issue_guest_token` already minted for that
appointment — same salt, same single-key `{"appointment_id": ...}`
payload, same signer — because that is the only way a hash computed from
the derived token can ever match the `token_hash` already stored on the
`GuestAccessToken` row. Determinism is the load-bearing property; nothing
in the existing suite pins it today (recon found no existing test doing
so), which is why it is pinned here explicitly rather than assumed.

Fixed literal UTC datetime, never `timezone.now()` — same discipline as
the rest of the suite (e.g. test_booking_create_guest_appointment.py).
"""

import datetime as dt
import hashlib

import pytest

from booking.guest_tokens import derive_guest_token, issue_guest_token, validate_guest_token
from booking.models import GuestAccessToken
from core.tenancy import tenant_context
from tests.conftest import make_appointment

pytestmark = pytest.mark.django_db

START = dt.datetime(2026, 9, 1, 10, 0, tzinfo=dt.UTC)


@pytest.fixture
def appointment(salon, customer, specialist, service):
    return make_appointment(
        salon=salon, customer=customer, specialist=specialist, service=service, start=START
    )


def test_derive_guest_token_is_deterministic(salon, appointment):
    with tenant_context(salon.id):
        first = derive_guest_token(appointment.id)
        second = derive_guest_token(appointment.id)

    assert first == second


def test_derived_token_matches_the_issued_token_and_its_stored_hash(salon, appointment):
    with tenant_context(salon.id):
        issued_raw, row = issue_guest_token(appointment)

        derived_raw = derive_guest_token(appointment.id)

        validated = validate_guest_token(derived_raw)

    assert derived_raw == issued_raw
    assert hashlib.sha256(derived_raw.encode()).hexdigest() == row.token_hash
    assert validated.pk == row.pk


def test_derived_token_authenticates_but_derive_does_not_create_a_row(salon, appointment):
    with tenant_context(salon.id):
        issue_guest_token(appointment)

        derived_raw = derive_guest_token(appointment.id)

        row_count = GuestAccessToken.objects.filter(appointment_id=appointment.id).count()

    assert derived_raw != ""
    assert row_count == 1
