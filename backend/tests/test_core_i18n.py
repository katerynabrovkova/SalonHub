"""
Stage 11.5 sub-step 2, step 1 — core.i18n.resolve_translation
(docs/DECISIONS.md § "API language contract" and § "Full fallback chain").

The single reusable implementation of the read-side language resolution
rule, so no serializer duplicates the fallback logic.

Written against the agreed design before any implementation exists — this
file is expected to fail on collection (ImportError: cannot import name
'resolve_translation' from 'core.i18n') until core/i18n.py is written, the
same two-step-red shape as test_core_formatting.py's docstring describes.
"""

from core.i18n import resolve_language_code, resolve_translation

_DICT = {"en": "Haircut", "uk": "Стрижка"}


def test_returns_requested_language_when_populated():
    assert resolve_translation(_DICT, "uk") == "Стрижка"


def test_falls_back_to_english_when_requested_missing():
    assert resolve_translation(_DICT, None) == "Haircut"


def test_falls_back_to_english_when_requested_language_empty_string():
    assert resolve_translation(_DICT, "") == "Haircut"


def test_falls_back_to_english_when_requested_unsupported():
    assert resolve_translation(_DICT, "fr") == "Haircut"


def test_falls_back_to_other_populated_language_when_english_empty():
    assert resolve_translation({"en": "", "uk": "Стрижка"}, None) == "Стрижка"


def test_falls_back_to_other_populated_language_when_english_missing_key():
    assert resolve_translation({"uk": "Стрижка"}, None) == "Стрижка"


def test_returns_empty_string_when_all_languages_empty():
    assert resolve_translation({"en": "", "uk": ""}, None) == ""


def test_returns_empty_string_when_dict_has_no_supported_keys():
    assert resolve_translation({}, "uk") == ""


# --- resolve_language_code (docs/DECISIONS.md § "Notification message
# builder: language resolution") — resolves a plain language preference to
# a supported language CODE, for selecting a (subject, body) tuple.


def test_resolve_language_code_returns_a_supported_request_unchanged():
    assert resolve_language_code("uk") == "uk"
    assert resolve_language_code("en") == "en"


def test_resolve_language_code_falls_back_to_english_for_none_empty_or_unsupported():
    assert resolve_language_code(None) == "en"
    assert resolve_language_code("") == "en"
    assert resolve_language_code("fr") == "en"
