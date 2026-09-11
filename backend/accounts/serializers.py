"""
Auth serializers (docs/ARCHITECTURE.md § 3).

`LogoutSerializer` validates the refresh token to be blacklisted.
`RegisterSerializer` (Stage 3-R.D.3) validates a client self-registration
payload — email format + password strength only. It deliberately does NOT
check for a duplicate email: the view issues an identical 202 whether or
not the address is already registered in this salon, per the
no-enumeration rule (docs/DECISIONS.md § Stage 3-R.D.3).

`PasswordResetRequestSerializer` / `PasswordResetConfirmSerializer` /
`ResendVerificationSerializer` (Stage 3-R.D.5) replace the password-reset
surface removed in 3-R.D.2; a weak `new_password` on confirm is a
field-level 400, never the neutral token-failure collapse.

`AccountTokenObtainPairSerializer` / `AccountTokenRefreshSerializer` (Stage
3-R.E) retarget login/refresh from `User` onto `Account`, tenant-scoped to
the salon bound from the URL slug — see docs/DECISIONS.md § Stage 3-R.E for
the full contract (login request shape, no-enumeration failure, the
`identity_model` claim mitigation).

`MeSerializer` (Stage 12) is the read representation for `GET auth/me/` —
see docs/DECISIONS.md § "`/me/` endpoint (Stage 12)".
"""

from typing import Any

from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from rest_framework import exceptions, serializers
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.serializers import TokenObtainSerializer, TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.authentication import ACCOUNT_IDENTITY_MODEL, IDENTITY_MODEL_CLAIM
from accounts.models import Account


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, validators=[validate_password])

    def validate_email(self, value: str) -> str:
        return value.strip().lower()


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value: str) -> str:
        return value.strip().lower()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value: str) -> str:
        validate_password(value)
        return value


class ResendVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField()


class MeSerializer(serializers.ModelSerializer):
    """
    Read representation for ``GET auth/me/`` (docs/DECISIONS.md § "`/me/`
    endpoint (Stage 12)"). Deliberately just `email` + `role` — no `salon`
    (the frontend already has the slug from the subdomain before login
    happens) and no `email_verified_at` (deferred until an actual
    "verify your email" UI exists).
    """

    class Meta:
        model = Account
        fields = ["email", "role"]
        read_only_fields = fields

    def validate_email(self, value: str) -> str:
        return value.strip().lower()


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class AccountTokenObtainPairSerializer(TokenObtainSerializer):
    """
    Issues an access/refresh pair for an `Account`, tenant-scoped to the
    salon bound from the URL slug (docs/DECISIONS.md § Stage 3-R.E, "Login
    request shape") — never a salon named in the request body: the salon
    isn't even a declared field here, so a "salon" key in the body is
    silently ignored, same as register/reset-request.

    Does not call `TokenObtainSerializer.validate()`'s stock
    implementation: that runs Django's `authenticate()`, which goes
    through `AUTHENTICATION_BACKENDS` against `AUTH_USER_MODEL` (`User`) —
    the wrong model entirely for this login.

    Wrong password and unknown email collapse to the exact same
    `AuthenticationFailed` call — same message, same code — so the two
    failure responses are byte-identical (no-enumeration, same posture as
    D.3 register / D.5 password-reset-request).
    """

    username_field = "email"
    token_class = RefreshToken

    def validate(self, attrs: dict[str, Any]) -> dict[str, str]:
        email = attrs[self.username_field]
        password = attrs["password"]

        account = Account.objects.filter(email=email).first()
        if account is None or not account.check_password(password) or not account.is_active:
            raise exceptions.AuthenticationFailed(
                self.error_messages["no_active_account"], "no_active_account"
            )

        refresh = self.get_token(account)

        data = {"refresh": str(refresh), "access": str(refresh.access_token)}

        if api_settings.UPDATE_LAST_LOGIN:
            # Inlined rather than calling django.contrib.auth.models.
            # update_last_login(None, account) (what stock
            # TokenObtainSerializer.validate() does): that function's
            # django-stubs signature is pinned to the concrete User model,
            # not AbstractBaseUser, so passing an Account fails mypy for no
            # real reason — the function body is exactly these two lines.
            account.last_login = timezone.now()
            account.save(update_fields=["last_login"])

        return data

    # django-stubs types TokenObtainSerializer.get_token()'s `user` param as
    # the module-level AuthUser TypeVar (AbstractBaseUser | TokenUser), not
    # generic per subclass — narrowing to Account here is a real, intended
    # narrowing (this serializer only ever mints tokens for Account), not a
    # Liskov violation in practice. Same known stub friction as
    # `AccountManager`/`User.objects = UserManager()` in accounts/models.py.
    @classmethod
    def get_token(cls, user: Account) -> RefreshToken:  # type: ignore[override]
        """
        Builds the token payload directly rather than calling
        `RefreshToken.for_user()` (the stock `TokenObtainSerializer.
        get_token()` implementation): `RefreshToken` picks up
        `BlacklistMixin.for_user()` via MRO (the `token_blacklist` app is
        installed, config/settings/base.py), which eagerly does
        `OutstandingToken.objects.create(user=user, ...)` —
        `OutstandingToken.user` is a hard FK to `AUTH_USER_MODEL` (`User`),
        so passing an `Account` instance raises `ValueError` at model
        construction. This is a fourth SimpleJWT integration point the
        docs/DECISIONS.md recon didn't surface (it only exercised
        `BlacklistMixin.blacklist()`/`outstand()`, which degrade
        gracefully — see below).

        This reproduces the two lines `Token.for_user()` itself runs
        (`getattr(user, USER_ID_FIELD)`, no `isinstance`/`get_user_model()`
        — the duck-typing the recon did confirm) and nothing else, so it
        loses no real tracking: `outstand()`/`blacklist()` (used by
        refresh/logout) already wrap their own `User.objects.get()` lookup
        in `try`/`except DoesNotExist`, degrading to a `user=None` row when
        the claimed id names no `User` row — so the `OutstandingToken` row
        is simply created lazily on the first `outstand()`/`blacklist()`
        call instead of at mint time, with the same eventual shape.
        """
        user_id = str(getattr(user, api_settings.USER_ID_FIELD))
        token = cls.token_class()
        token[api_settings.USER_ID_CLAIM] = user_id
        token[IDENTITY_MODEL_CLAIM] = ACCOUNT_IDENTITY_MODEL
        return token


class AccountTokenRefreshSerializer(TokenRefreshSerializer):
    """
    Retargets stock `TokenRefreshSerializer.validate()`'s "does the user
    this token names still exist and stay active" check off
    `AUTH_USER_MODEL` (`User`) onto `Account` — the stock lookup,
    unmodified, would either crash (`Account` ids and `User` ids are
    independent sequences; a real `Account` token's `user_id` usually
    names no `User` row at all, so `get_user_model().objects.get(...)`
    raises `DoesNotExist` uncaught) or, worse, silently succeed against an
    unrelated colliding `User`/`Account` row sharing that pk.

    The `identity_model` claim is checked before any pk lookup runs — the
    same collision guard as
    `accounts.authentication.AccountJWTAuthentication.get_user()`, for the
    same reason (docs/DECISIONS.md § Stage 3-R.E). Does not call
    `super().validate()`: that method's pk lookup is exactly what is being
    replaced.

    `RefreshToken.access_token` copies every claim already on the refresh
    token's payload (except `token_type`/`exp`/`jti`/`iat`) onto the new
    access token, and `ROTATE_REFRESH_TOKENS` re-signs the *same* payload
    in place — so `identity_model`, once present on the original refresh
    token, survives onto both the new access token and the rotated refresh
    token with no extra propagation code needed here.
    """

    def validate(self, attrs: dict[str, Any]) -> dict[str, str]:
        refresh = self.token_class(attrs["refresh"])

        if refresh.get(IDENTITY_MODEL_CLAIM) != ACCOUNT_IDENTITY_MODEL:
            raise InvalidToken("Token is missing a valid identity_model claim.")

        try:
            user_id = refresh[api_settings.USER_ID_CLAIM]
        except KeyError as exc:
            raise InvalidToken("Token contained no recognizable user identification") from exc

        try:
            account = Account.objects.get(pk=user_id)
        except Account.DoesNotExist as exc:
            raise exceptions.AuthenticationFailed(
                self.error_messages["no_active_account"], "no_active_account"
            ) from exc

        if not account.is_active:
            raise exceptions.AuthenticationFailed(
                self.error_messages["no_active_account"], "no_active_account"
            )

        data = {"access": str(refresh.access_token)}

        if api_settings.ROTATE_REFRESH_TOKENS:
            if api_settings.BLACKLIST_AFTER_ROTATION:
                try:
                    refresh.blacklist()
                except AttributeError:
                    pass

            refresh.set_jti()
            refresh.set_exp()
            refresh.set_iat()
            refresh.outstand()

            data["refresh"] = str(refresh)

        return data
