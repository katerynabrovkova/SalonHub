"""
Auth serializers (docs/ARCHITECTURE.md § 3).

`LogoutSerializer` validates the refresh token to be blacklisted.
`RegisterSerializer` (Stage 3-R.D.3) validates a client self-registration
payload — email format + password strength only. It deliberately does NOT
check for a duplicate email: the view issues an identical 202 whether or
not the address is already registered in this salon, per the
no-enumeration rule (docs/DECISIONS.md § Stage 3-R.D.3). The
email-verification and password-reset serializers removed in 3-R.D.2 get
their `Account`-based replacements in 3-R.D.4-D.5.
"""

from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, validators=[validate_password])

    def validate_email(self, value: str) -> str:
        return value.strip().lower()


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()
