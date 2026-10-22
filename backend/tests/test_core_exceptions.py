from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from core.exceptions import DomainError, SpecialistDoesNotOfferServiceError, exception_handler


class _BoomDomainError(DomainError):
    code = "boom"
    default_message = "Boom."


def _context() -> dict:
    return {"view": APIView(), "request": APIRequestFactory().get("/"), "args": (), "kwargs": {}}


def test_domain_error_is_translated_into_the_error_envelope() -> None:
    response = exception_handler(_BoomDomainError(), _context())

    assert response is not None
    assert response.status_code == 400
    assert response.data == {"error": {"code": "boom", "message": "Boom.", "details": {}}}


def test_drf_exception_is_translated_into_the_same_envelope_shape() -> None:
    exc = ValidationError({"email": ["This field is required."]})

    response = exception_handler(exc, _context())

    assert response is not None
    assert response.status_code == 400
    assert response.data["error"]["code"] == "invalid"
    assert response.data["error"]["details"] == {"email": ["This field is required."]}


def test_specialist_does_not_offer_service_error_shape() -> None:
    exc = SpecialistDoesNotOfferServiceError()

    assert isinstance(exc, DomainError)
    assert exc.code == "SPECIALIST_DOES_NOT_OFFER_SERVICE"
    assert exc.status_code == 400
