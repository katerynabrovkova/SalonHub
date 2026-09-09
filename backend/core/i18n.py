"""
Read-side language resolution for translatable JSONField values
(docs/DECISIONS.md § "API language contract" and § "Full fallback chain",
Stage 11.5).

One reusable implementation of the fallback rule so no serializer
re-derives it, and one list — SUPPORTED_LANGUAGES — as the single source of
truth for which languages exist (reused by the write-side key allowlist,
docs/DECISIONS.md § "Write-side language key validation").
"""

# English first: it is the global fallback (docs/DECISIONS.md § "Languages
# and fallback"). Nothing here assumes exactly two entries.
SUPPORTED_LANGUAGES: list[str] = ["en", "uk"]


def resolve_translation(value: dict[str, str], requested_lang: str | None) -> str:
    """
    Resolve a ``{lang_code: string}`` dict to a single display string.

    Order (docs/DECISIONS.md § "Full fallback chain"):
      1. the requested language, if supported and populated;
      2. else English, if populated;
      3. else the first populated value among SUPPORTED_LANGUAGES, in order;
      4. else "".

    ``requested_lang`` being None, "", or an unsupported code all mean "no
    valid request" and fall through identically from step 2. No input shape
    raises — a missing or empty key is simply "not populated".
    """
    candidates: list[str] = []
    if requested_lang is not None and requested_lang in SUPPORTED_LANGUAGES:
        candidates.append(requested_lang)
    if "en" not in candidates:
        candidates.append("en")
    candidates.extend(lang for lang in SUPPORTED_LANGUAGES if lang not in candidates)

    for lang in candidates:
        resolved = value.get(lang)
        # A missing or empty key is simply "not populated"; a non-string
        # value in a malformed dict is ignored rather than raising.
        if isinstance(resolved, str) and resolved != "":
            return resolved

    return ""


def resolve_language_code(requested_lang: str | None) -> str:
    """
    Resolve a plain language *preference* to a supported language *code*
    (docs/DECISIONS.md § "Notification message builder: language
    resolution").

    Unlike ``resolve_translation``, which resolves a ``{lang: string}``
    dict to a display string, this picks the code itself — needed to
    select which ``(subject, body)`` tuple to use from a per-language
    message-template mapping.

    ``requested_lang`` being None, ``""``, or an unsupported code all mean
    "no valid request" → English. No "first non-empty among remaining
    languages" fallthrough: a caller selecting by code is expected to have
    every supported language populated.
    """
    if requested_lang is not None and requested_lang in SUPPORTED_LANGUAGES:
        return requested_lang
    return "en"


def resolve_display_name(value: dict[str, str], fallback: str) -> str:
    """
    Resolve a translatable name for a staff/admin context (no ``?lang=``
    equivalent — docs/DECISIONS.md § "Admin display of translatable
    fields") to a non-empty display string.

    Same fallback chain as ``resolve_translation(value, None)``, but a
    row whose every language key is empty or absent yields ``fallback``
    (e.g. ``"Specialist #7"``) instead of ``""`` — an empty admin
    changelist link / FK dropdown entry would make the row unfindable.
    """
    return resolve_translation(value, None) or fallback
