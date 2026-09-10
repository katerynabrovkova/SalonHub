"""
Account-based JWT authentication (docs/DECISIONS.md § Stage 3-R.E).

`AccountJWTAuthentication` retargets `JWTAuthentication.get_user()` off
`AUTH_USER_MODEL` (`User`) onto `Account` — the known-risk mitigation
recorded in docs/DECISIONS.md § Stage 3-R decisions ("Known risk...
`USER_ID_FIELD` / cross-model token collision") and § Stage 3-R.E. `User`
and `Account` are separate tables with independent auto-increment
sequences, both starting at 1, so a `User` row and an `Account` row can
legitimately share the same integer pk; an unguarded pk lookup against the
wrong table would silently authenticate as the wrong identity.

The `identity_model` claim check below runs BEFORE any pk lookup — the
same shape as `core.permissions.IsSalonStaff`'s
`isinstance(request.user, Account)` guard, one layer earlier: at the
authentication layer itself, so no unrelated authenticated principal
(including a still-valid legacy `User` token minted before this claim
existed) ever reaches a permission check in the first place.
"""

from rest_framework.request import Request
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed, InvalidToken
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import Token

from accounts.cookies import ACCESS_COOKIE
from accounts.models import Account
from core.tenancy import get_current_salon_id

IDENTITY_MODEL_CLAIM = "identity_model"
ACCOUNT_IDENTITY_MODEL = "account"


class AccountJWTAuthentication(JWTAuthentication):
    # django-stubs types get_user()'s return as the module-level AuthUser
    # TypeVar (AbstractBaseUser | TokenUser), not generic per subclass —
    # returning Account here is a real, intended narrowing (this class only
    # ever authenticates as Account), not a Liskov violation in practice.
    # Same known stub friction as AccountManager/User.objects = UserManager()
    # in accounts/models.py.
    def get_user(self, validated_token: Token) -> Account:  # type: ignore[override]
        if validated_token.get(IDENTITY_MODEL_CLAIM) != ACCOUNT_IDENTITY_MODEL:
            raise InvalidToken("Token is missing a valid identity_model claim.")

        try:
            user_id = validated_token[api_settings.USER_ID_CLAIM]
        except KeyError as exc:
            raise InvalidToken("Token contained no recognizable user identification") from exc

        # Account.objects is tenant-scoped (core.models.TenantScopedManager)
        # and raises if no tenant is bound; every real caller reaches this
        # under /api/v1/salons/<slug>/... where TenantResolutionMiddleware
        # already bound one, but a stray Authorization header on a
        # non-salon-scoped route (e.g. a webhook) must fail closed here
        # rather than let that RuntimeError escape as an unhandled 500.
        if get_current_salon_id() is None:
            raise AuthenticationFailed("No salon context bound for this request.")

        try:
            account = Account.objects.get(pk=user_id)
        except Account.DoesNotExist as exc:
            raise AuthenticationFailed("Account not found.", code="user_not_found") from exc

        if api_settings.CHECK_USER_IS_ACTIVE and not account.is_active:
            raise AuthenticationFailed("Account is inactive.", code="user_inactive")

        return account


class AccountJWTCookieAuthentication(AccountJWTAuthentication):
    """
    Reads the access JWT from the ``access_token`` cookie instead of the
    ``Authorization: Bearer`` header (docs/DECISIONS.md § Stage 12). The token
    is then validated and resolved exactly as the header pipeline does — same
    ``get_validated_token()`` (signature/expiry/blacklist) and the same
    inherited ``get_user()`` (``identity_model`` claim guard, tenant-scoped
    ``Account`` lookup).

    Returns ``None`` when the cookie is absent so DRF falls through to the
    next authenticator: ``AccountJWTAuthentication`` (header) is kept in
    ``DEFAULT_AUTHENTICATION_CLASSES`` after this one, since non-browser
    clients still authenticate with a Bearer header and
    tests/test_auth_identity_model_claim.py exercises that path directly.
    """

    # Same intended Account narrowing / stub friction as get_user() above.
    def authenticate(self, request: Request) -> tuple[Account, Token] | None:  # type: ignore[override]
        raw_token = request.COOKIES.get(ACCESS_COOKIE)
        if not raw_token:
            return None

        validated_token = self.get_validated_token(raw_token.encode())
        return self.get_user(validated_token), validated_token
