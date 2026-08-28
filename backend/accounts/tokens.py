"""
Account email-verification token (docs/DECISIONS.md § Stage 3-R.D.3).

Stateless: a ``TimestampSigner``-signed payload of ``{account_id, email}``,
checked against the account's *current* email at verify time rather than
tracked in a database row. Verifying is idempotent (setting
``email_verified_at`` twice is a no-op), unlike the guest access token's
cancel action, which has a real one-time consequence — that's why guest
tokens need a stored single-use record (``booking/guest_tokens``) and this
one does not.

Salon is deliberately NOT in the payload: the emailed link already carries
the slug (``{FRONTEND_URL}/salons/<slug>/verify-email#token=...``), so the
verify endpoint (D.4) runs under the salon prefix with tenant context
bound. ``read_account_verification_token`` resolves the account through the
tenant-scoped ``Account.objects`` on top of that, so a token minted in one
salon cannot be redeemed under another even if replayed against the wrong
slug.

Password reset uses Django's built-in ``PasswordResetTokenGenerator``
(3-R.D.5) — self-invalidating on password change, a property this stateless
signer intentionally does not try to replicate.
"""

from django.core import signing

from accounts.models import Account

_SALT = "accounts.account-verification"

# 48 hours. A fixed security parameter, not a salon-configurable business
# lever — same stance as booking/guest_tokens.GUEST_TOKEN_VALIDITY, and the
# reason it is a module constant here rather than re-added to settings (the
# pre-3-R.D.2 EMAIL_VERIFICATION_TIMEOUT setting was deleted with the old
# User-based flow; PASSWORD_RESET_TIMEOUT stays a setting only because
# Django's own generator reads it directly).
ACCOUNT_VERIFICATION_TOKEN_MAX_AGE = 60 * 60 * 48


def generate_account_verification_token(account: Account) -> str:
    signer = signing.TimestampSigner(salt=_SALT)
    return signer.sign_object({"account_id": account.id, "email": account.email})


def read_account_verification_token(token: str) -> int:
    """
    Returns the verified account id, or raises ``signing.BadSignature`` /
    ``signing.SignatureExpired`` if the token is invalid, expired, issued
    for an email the account no longer has, or names an account that does
    not exist in the currently bound salon.

    Must be called with tenant context bound — every caller is the D.4
    verify endpoint under ``/api/v1/salons/<slug>/...``.
    """
    signer = signing.TimestampSigner(salt=_SALT)
    payload = signer.unsign_object(token, max_age=ACCOUNT_VERIFICATION_TOKEN_MAX_AGE)
    account_id = payload["account_id"]
    email = payload["email"]

    try:
        account = Account.objects.get(pk=account_id)
    except Account.DoesNotExist as exc:
        raise signing.BadSignature("No such account in this salon.") from exc

    if account.email != email:
        raise signing.BadSignature("Token was issued for a different email address.")

    return account_id
