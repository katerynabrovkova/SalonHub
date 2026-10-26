"""
Stage 16 sub-step 10b — startup check of the assistant provider settings
(docs/DECISIONS.md § "Sub-step 10 details", "Settings"). The project's
first system check, registered in AiAssistantConfig.ready().

With ASSISTANT_PROVIDER "openai", an empty API key or safety_identifier
secret is an error, so the misconfiguration stops startup instead of the
first customer message. An unknown provider value is an error too. "mock"
needs nothing. get_assistant_provider() raises ImproperlyConfigured from
the same errors, as a second guard.

Never imports `openai`: with the mock the SDK must not be loaded.
"""

from collections.abc import Sequence
from typing import Any

from django.apps import AppConfig
from django.conf import settings
from django.core.checks import Error

PROVIDERS = ("mock", "openai")

_REQUIRED_FOR_OPENAI = (
    ("ASSISTANT_OPENAI_API_KEY", "ai_assistant.E001"),
    ("ASSISTANT_SAFETY_IDENTIFIER_SECRET", "ai_assistant.E002"),
)


def check_assistant_provider_settings(
    app_configs: Sequence[AppConfig] | None, **kwargs: Any
) -> list[Error]:
    provider = settings.ASSISTANT_PROVIDER
    if provider not in PROVIDERS:
        return [
            Error(
                f"ASSISTANT_PROVIDER must be one of {', '.join(PROVIDERS)}; got {provider!r}.",
                id="ai_assistant.E003",
            )
        ]
    if provider != "openai":
        return []
    return [
        Error(f'{name} is required when ASSISTANT_PROVIDER is "openai".', id=check_id)
        for name, check_id in _REQUIRED_FOR_OPENAI
        if not getattr(settings, name)
    ]
