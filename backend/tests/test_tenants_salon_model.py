"""
Salon model tests for the Stage 16 sub-step 1 fields (docs/DECISIONS.md
§ Stage 16 planning, "Sub-step 1 details"): defaults, and the
`assistant_starters` / `instagram_url` validation run by full_clean().
"""

import pytest
from django.core.exceptions import ValidationError

from tenants.models import Salon

pytestmark = pytest.mark.django_db


def test_new_salon_has_assistant_and_contact_defaults():
    salon = Salon.objects.create(
        name={"en": "Fresh Salon"},
        slug="fresh-salon",
        currency="UAH",
        contact_email="owner@fresh-salon.example",
    )
    salon.refresh_from_db()

    assert salon.assistant_enabled is False
    assert salon.assistant_starters == []
    assert salon.phone == ""
    assert salon.address == ""
    assert salon.instagram_url == ""


def test_full_clean_rejects_more_than_3_starters(salon):
    salon.assistant_starters = [{"en": f"Starter {i}"} for i in range(4)]

    with pytest.raises(ValidationError) as exc_info:
        salon.full_clean()

    assert "assistant_starters" in exc_info.value.message_dict


def test_full_clean_rejects_starter_value_over_60_characters(salon):
    salon.assistant_starters = [{"en": "a" * 61}]

    with pytest.raises(ValidationError) as exc_info:
        salon.full_clean()

    assert "assistant_starters" in exc_info.value.message_dict


def test_full_clean_rejects_unknown_language_key_in_starter(salon):
    salon.assistant_starters = [{"en": "Book a manicure", "fr": "Réserver"}]

    with pytest.raises(ValidationError) as exc_info:
        salon.full_clean()

    assert "assistant_starters" in exc_info.value.message_dict


def test_full_clean_rejects_non_dict_starter_and_non_string_value(salon):
    salon.assistant_starters = ["Book a manicure"]
    with pytest.raises(ValidationError) as non_dict_exc:
        salon.full_clean()
    assert "assistant_starters" in non_dict_exc.value.message_dict

    salon.assistant_starters = [{"en": 42}]
    with pytest.raises(ValidationError) as non_string_exc:
        salon.full_clean()
    assert "assistant_starters" in non_string_exc.value.message_dict


def test_full_clean_accepts_3_starters_at_60_characters(salon):
    salon.assistant_starters = [
        {"en": "a" * 60, "uk": "б" * 60},
        {"en": "c" * 60, "uk": "д" * 60},
        {"en": "e" * 60, "uk": "є" * 60},
    ]

    salon.full_clean()


def test_full_clean_rejects_invalid_instagram_url(salon):
    salon.instagram_url = "not a url"

    with pytest.raises(ValidationError) as exc_info:
        salon.full_clean()

    assert "instagram_url" in exc_info.value.message_dict
