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
"""

from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers


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

    def validate_email(self, value: str) -> str:
        return value.strip().lower()


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()
