"""
Stage 16 sub-step 10b — choosing the assistant provider and checking its
settings (docs/DECISIONS.md § "Sub-step 10 details", "Settings").

`get_assistant_provider()` reads ASSISTANT_PROVIDER: `mock` (default) or
`openai`. With `openai`, an empty ASSISTANT_OPENAI_API_KEY or
ASSISTANT_SAFETY_IDENTIFIER_SECRET raises ImproperlyConfigured; so does an
unknown provider value. The same conditions are Django system check errors
from ai_assistant/checks.py, registered in AiAssistantConfig.ready(), so a
misconfiguration is caught at startup:

    ai_assistant.E001  openai without ASSISTANT_OPENAI_API_KEY
    ai_assistant.E002  openai without ASSISTANT_SAFETY_IDENTIFIER_SECRET
    ai_assistant.E003  unknown ASSISTANT_PROVIDER value

ai_assistant.checks and ai_assistant.providers.openai are imported through
helpers inside each test, so every test fails on its own before they
exist (same shape as test_ai_assistant_analytics.py). The existing
test_get_assistant_provider_returns_mock covers the default.
"""

import importlib
import os
import subprocess
import sys
from types import ModuleType

import pytest
from django.core.checks import Error, run_checks
from django.core.exceptions import ImproperlyConfigured

from ai_assistant.providers import get_assistant_provider


def _checks() -> ModuleType:
    return importlib.import_module("ai_assistant.checks")


def _adapter() -> ModuleType:
    return importlib.import_module("ai_assistant.providers.openai")


@pytest.fixture
def openai_configured(settings):
    settings.ASSISTANT_PROVIDER = "openai"
    settings.ASSISTANT_OPENAI_API_KEY = "sk-test-key"
    settings.ASSISTANT_SAFETY_IDENTIFIER_SECRET = "test-secret"
    return settings


_MISSING = [
    pytest.param("ASSISTANT_OPENAI_API_KEY", "ai_assistant.E001", id="api_key"),
    pytest.param("ASSISTANT_SAFETY_IDENTIFIER_SECRET", "ai_assistant.E002", id="secret"),
]


# --- Factory -------------------------------------------------------------------


def test_factory_returns_openai_provider_when_configured(openai_configured):
    assert isinstance(get_assistant_provider(), _adapter().OpenAIAssistantProvider)


@pytest.mark.parametrize(("setting", "_check_id"), _MISSING)
def test_factory_raises_when_openai_setting_missing(openai_configured, setting, _check_id):
    setattr(openai_configured, setting, "")
    with pytest.raises(ImproperlyConfigured):
        get_assistant_provider()


def test_factory_raises_for_unknown_provider(settings):
    settings.ASSISTANT_PROVIDER = "opneai"
    with pytest.raises(ImproperlyConfigured):
        get_assistant_provider()


# --- System check ------------------------------------------------------------


def _check_ids() -> list[str]:
    errors = _checks().check_assistant_provider_settings(app_configs=None)
    assert all(isinstance(error, Error) for error in errors)
    return [error.id for error in errors]


@pytest.mark.parametrize(("setting", "check_id"), _MISSING)
def test_check_reports_missing_openai_setting(openai_configured, setting, check_id):
    setattr(openai_configured, setting, "")
    assert _check_ids() == [check_id]


def test_check_reports_unknown_provider(settings):
    settings.ASSISTANT_PROVIDER = "opneai"
    assert _check_ids() == ["ai_assistant.E003"]


def test_check_passes_for_mock_without_any_openai_setting(settings):
    settings.ASSISTANT_PROVIDER = "mock"
    settings.ASSISTANT_OPENAI_API_KEY = ""
    settings.ASSISTANT_SAFETY_IDENTIFIER_SECRET = ""
    assert _check_ids() == []


def test_check_passes_for_openai_when_configured(openai_configured):
    assert _check_ids() == []


def test_check_is_registered(openai_configured):
    _checks()  # fails on its own before the module exists
    openai_configured.ASSISTANT_OPENAI_API_KEY = ""
    assert "ai_assistant.E001" in [message.id for message in run_checks()]


# --- Lazy SDK import (agreed 26.10.2026) ----------------------------------------


def test_providers_and_checks_do_not_import_openai_with_the_mock():
    """
    A fresh interpreter: this test process has already imported `openai`.
    Setting up Django, importing ai_assistant.providers and
    ai_assistant.checks, and getting the mock provider must not load it.
    """
    code = (
        "import sys, django\n"
        "django.setup()\n"
        "import ai_assistant.checks, ai_assistant.providers\n"
        "ai_assistant.providers.get_assistant_provider()\n"
        "print(sorted(m for m in sys.modules if m == 'openai' or m.startswith('openai.')))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "ASSISTANT_PROVIDER": "mock", "ASSISTANT_OPENAI_API_KEY": ""},
    )
    assert result.stdout.strip() == "[]"
