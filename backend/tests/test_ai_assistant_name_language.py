"""
Stage 17.8 — names follow the customer's language. The model may pass an
optional `lang` to list_services, get_specialist_info and check_availability;
the orchestrator hands it to the tool as `name_lang`. Service, category and
specialist names use it when it is a supported language and the name is
filled in it, otherwise the site language, then any filled language.
Descriptions, bios, FAQ and the proposal card stay as they are.
"""

import datetime as dt
from typing import Any

import pytest

from ai_assistant import tools as tools_module
from ai_assistant.orchestrator import run_turn
from ai_assistant.providers.base import Finish, TextDelta, ToolCall
from ai_assistant.providers.mock import MockAssistantProvider
from ai_assistant.tools import TOOLS, check_availability, get_specialist_info, list_services
from catalog.models import Service, ServiceCategory
from core.i18n import SUPPORTED_LANGUAGES
from core.tenancy import tenant_context
from specialists.models import Specialist, SpecialistService
from tests.conftest import make_working_hours

pytestmark = pytest.mark.django_db

NOW = dt.datetime(2026, 8, 16, 0, 0, tzinfo=dt.UTC)
MONDAY = dt.date(2026, 8, 17)
SESSION_ID = "name-language-test-session-0123456789abcdefghijklmn"


@pytest.fixture
def nails(salon):
    with tenant_context(salon.id):
        return ServiceCategory.objects.create(salon=salon, name={"en": "Nails", "uk": "Нігті"})


def _service(salon, category, name: dict[str, str]) -> Service:
    with tenant_context(salon.id):
        return Service.objects.create(
            salon=salon,
            category=category,
            name=name,
            description="Опис українською",
            duration_minutes=60,
            price="500.00",
        )


def _specialist(salon, name: dict[str, str], services=(), bio=None) -> Specialist:
    with tenant_context(salon.id):
        specialist = Specialist.objects.create(salon=salon, name=name, bio=bio or {})
        for service in services:
            SpecialistService.objects.create(salon=salon, specialist=specialist, service=service)
    return specialist


def _by_id(result: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {item["id"]: item for item in result["services"]}


# --- Tools -----------------------------------------------------------------


def test_list_services_names_follow_name_lang_and_description_stays(salon, nails):
    manicure = _service(salon, nails, {"en": "Manicure", "uk": "Манікюр"})

    with tenant_context(salon.id):
        result = list_services(lang="uk", name_lang="en")

    item = _by_id(result)[manicure.id]
    assert item["name"] == "Manicure"
    assert item["category"] == "Nails"
    assert item["description"] == "Опис українською"


def test_list_services_unsupported_name_lang_falls_back_to_the_site_language(salon, nails):
    manicure = _service(salon, nails, {"en": "Manicure", "uk": "Манікюр"})

    with tenant_context(salon.id):
        results = [list_services(lang="uk", name_lang=bad) for bad in ("xx", "", 5, None, ["en"])]

    for result in results:
        assert _by_id(result)[manicure.id]["name"] == "Манікюр"
        assert _by_id(result)[manicure.id]["category"] == "Нігті"


def test_name_missing_in_name_lang_falls_back_to_the_site_language(salon, nails):
    pedicure = _service(salon, nails, {"en": "Pedicure", "uk": "Педикюр"})
    with tenant_context(salon.id):
        category = ServiceCategory.objects.create(salon=salon, name={"uk": "Брови"})
    brows = _service(salon, category, {"uk": "Брови"})

    with tenant_context(salon.id):
        result = list_services(lang="uk", name_lang="en")

    assert _by_id(result)[pedicure.id]["name"] == "Pedicure"
    assert _by_id(result)[brows.id]["name"] == "Брови"
    assert _by_id(result)[brows.id]["category"] == "Брови"


def test_name_missing_in_both_languages_uses_any_filled_language(salon, nails):
    only_uk = _service(salon, nails, {"uk": "Лише українською"})

    with tenant_context(salon.id):
        result = list_services(lang="en", name_lang="en")

    assert _by_id(result)[only_uk.id]["name"] == "Лише українською"


def test_get_specialist_info_names_follow_name_lang_and_bio_stays(salon, nails):
    manicure = _service(salon, nails, {"en": "Manicure", "uk": "Манікюр"})
    olena = _specialist(
        salon,
        {"en": "Olena", "uk": "Олена"},
        services=[manicure],
        bio={"en": "English bio", "uk": "Біографія"},
    )

    with tenant_context(salon.id):
        result = get_specialist_info(lang="uk", name_lang="en")
        one = get_specialist_info(lang="uk", name_lang="en", specialist_id=olena.id)

    for listed in (result, one):
        [item] = listed["specialists"]
        assert item["name"] == "Olena"
        assert item["services"] == [{"id": manicure.id, "name": "Manicure"}]
        assert item["bio"] == "Біографія"


def test_check_availability_free_specialist_names_follow_name_lang(salon, nails):
    salon.timezone = "Asia/Tbilisi"
    salon.save(update_fields=["timezone"])
    manicure = _service(salon, nails, {"en": "Manicure", "uk": "Манікюр"})
    olena = _specialist(salon, {"en": "Olena", "uk": "Олена"}, services=[manicure])
    make_working_hours(
        salon=salon,
        specialist=olena,
        day_of_week=MONDAY.weekday(),
        start_time=dt.time(9),
        end_time=dt.time(10),
    )

    with tenant_context(salon.id):
        english = check_availability(
            lang="uk",
            name_lang="en",
            now=NOW,
            service_id=manicure.id,
            specialist_id="any",
            date_from="2026-08-17",
            date_to="2026-08-17",
        )
        site = check_availability(
            lang="uk",
            now=NOW,
            service_id=manicure.id,
            specialist_id="any",
            date_from="2026-08-17",
            date_to="2026-08-17",
        )

    assert english["free_specialists"] == {
        "2026-08-17T09:00:00+04:00": [{"id": olena.id, "name": "Olena"}]
    }
    assert site["free_specialists"] == {
        "2026-08-17T09:00:00+04:00": [{"id": olena.id, "name": "Олена"}]
    }


# --- Specs -----------------------------------------------------------------


@pytest.mark.parametrize("name", ["list_services", "get_specialist_info", "check_availability"])
def test_name_tools_take_an_optional_supported_lang(name):
    spec, _function = TOOLS[name]

    lang = spec.parameters["properties"]["lang"]

    assert lang["type"] == "string"
    assert lang["enum"] == SUPPORTED_LANGUAGES
    assert "lang" not in spec.parameters.get("required", [])


@pytest.mark.parametrize("name", ["get_salon_info", "get_faq"])
def test_other_tools_take_no_lang(name):
    spec, _function = TOOLS[name]

    assert "lang" not in spec.parameters.get("properties", {})


# --- Orchestrator ----------------------------------------------------------


def _run(salon, script, *, lang: str) -> MockAssistantProvider:
    provider = MockAssistantProvider(script)
    with tenant_context(salon.id):
        list(
            run_turn(
                session_id=SESSION_ID,
                user_text="How much is a manicure?",
                lang=lang,
                now=NOW,
                provider=provider,
                safety_identifier="5a" * 32,
            )
        )
    return provider


def _finish() -> Finish:
    return Finish(input_tokens=1, output_tokens=1)


def test_the_models_lang_reaches_the_tool_as_name_lang_and_the_site_lang_stays(
    monkeypatch, salon, nails
):
    _service(salon, nails, {"en": "Manicure", "uk": "Манікюр"})
    received: dict[str, Any] = {}
    spec, real_list_services = tools_module.TOOLS["list_services"]

    def _spy(**kwargs: Any) -> dict[str, Any]:
        received.update(kwargs)
        return real_list_services(**kwargs)

    monkeypatch.setitem(tools_module.TOOLS, "list_services", (spec, _spy))

    provider = _run(
        salon,
        [
            [ToolCall(id="call-1", name="list_services", arguments={"lang": "en"}), _finish()],
            [TextDelta(text="Manicure is 500."), _finish()],
        ],
        lang="uk",
    )

    assert received["lang"] == "uk"
    assert received["name_lang"] == "en"
    [message] = [m for m in provider.calls[1]["messages"] if m.role == "tool"]
    assert "Manicure" in message.content
    assert "Манікюр" not in message.content


def test_without_the_models_lang_names_stay_in_the_site_language(salon, nails):
    _service(salon, nails, {"en": "Manicure", "uk": "Манікюр"})

    provider = _run(
        salon,
        [
            [ToolCall(id="call-1", name="list_services", arguments={}), _finish()],
            [TextDelta(text="Манікюр коштує 500."), _finish()],
        ],
        lang="uk",
    )

    [message] = [m for m in provider.calls[1]["messages"] if m.role == "tool"]
    assert "Манікюр" in message.content
