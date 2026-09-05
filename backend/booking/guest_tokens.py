"""
Guest access token issuance and validation (docs/ARCHITECTURE.md § 3,
docs/DECISIONS.md § Stage 3 decisions). The raw token is a signed value
(django.core.signing) — tamper-evident and cheap to reject before touching
the database — but only its SHA-256 hash is ever stored, on
GuestAccessToken.token_hash.
"""

import hashlib
from datetime import timedelta

from django.core import signing
from django.utils import timezone

from booking.models import Appointment, GuestAccessToken
from core.exceptions import InvalidOrExpiredTokenError

_SALT = "booking.guest-access-token"

# 30 days after Appointment.end_datetime, not salon-configurable — a fixed
# security parameter, not a business lever (docs/DECISIONS.md § Stage 3
# decisions).
GUEST_TOKEN_VALIDITY = timedelta(days=30)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def _sign_token(appointment_id: int) -> str:
    return signing.Signer(salt=_SALT).sign_object({"appointment_id": appointment_id})


def issue_guest_token(appointment: Appointment) -> tuple[str, GuestAccessToken]:
    """
    Creates the GuestAccessToken row and returns (raw_token, row). The raw
    token is handed back once, for the caller to put in the confirmation
    email link — it is never itself persisted or logged. Already called from
    production code: booking.services.create_guest_appointment, at
    guest-booking-creation time.
    """
    raw_token = _sign_token(appointment.id)
    row = GuestAccessToken.objects.create(
        salon=appointment.salon,
        appointment=appointment,
        token_hash=_hash_token(raw_token),
        expires_at=appointment.end_datetime + GUEST_TOKEN_VALIDITY,
    )
    return raw_token, row


def derive_guest_token(appointment_id: int) -> str:
    """
    Re-derives the exact raw token issue_guest_token minted for this
    appointment, without touching the database (no row read or write) —
    pure re-computation from the id. Relies on the signature being
    deterministic (a plain Signer over {"appointment_id": ...}, no
    timestamp or nonce): signing the same payload with the same salt always
    reproduces the same raw token, which hashes to the same token_hash
    already stored on the GuestAccessToken row. Determinism is the
    load-bearing constraint this function depends on (docs/DECISIONS.md §
    Step (d) decisions) — do not add a timestamp or any other
    non-deterministic element to _sign_token.

    Exists so a caller that only has the appointment id — e.g. the payment
    webhook building the BOOKING_CONFIRMED email — can reconstruct the raw
    token to put in the guest access link, since only its SHA-256 hash is
    ever stored.
    """
    return _sign_token(appointment_id)


def validate_guest_token(raw_token: str, *, for_cancel: bool = False) -> GuestAccessToken:
    """
    Validates the token itself — signature, known hash, expiry, and (when
    for_cancel) not already spent — and returns the row. Deliberately does
    NOT take a caller-supplied appointment_id to check against: the returned
    row's own `appointment_id` is what the caller must treat as the target,
    never something validated *against* a separately-sourced id
    (docs/DECISIONS.md § Stage 3 sub-step 3 decisions). This is what lets
    callers resolve "which appointment" from the token alone — see
    booking/views.py's _GuestTokenAppointmentMixin.get_object().

    Every failure path — missing/tampered token, unknown hash, expired, or
    (when for_cancel) a cancel capability already spent — raises the exact
    same InvalidOrExpiredTokenError, so none of these cases is
    distinguishable to the caller. Must be called with tenant context
    already bound (true for every caller: it's only ever invoked from
    within a /api/v1/salons/<slug>/... request).
    """
    if not raw_token:
        raise InvalidOrExpiredTokenError()

    try:
        payload = signing.Signer(salt=_SALT).unsign_object(raw_token)
    except signing.BadSignature as exc:
        raise InvalidOrExpiredTokenError() from exc

    token_row = GuestAccessToken.objects.filter(token_hash=_hash_token(raw_token)).first()
    # payload["appointment_id"] vs token_row.appointment_id is an internal
    # consistency check (both are derived from the same issuance call and
    # should always agree) — not a check against anything the caller
    # supplied.
    if token_row is None or token_row.appointment_id != payload.get("appointment_id"):
        raise InvalidOrExpiredTokenError()

    if token_row.expires_at <= timezone.now():
        raise InvalidOrExpiredTokenError()

    if for_cancel and token_row.cancelled_via_token_at is not None:
        raise InvalidOrExpiredTokenError()

    return token_row
