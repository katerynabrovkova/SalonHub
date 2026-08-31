"""
Stage 3-R.E — the ``identity_model`` JWT claim guard
(docs/DECISIONS.md § Stage 3-R.E, "Known-risk mitigation: cross-model token
collision, resolved at the authentication layer").

RED phase: written against the agreed contract before ``JWTAuthentication``
is overridden to check the claim. Nothing here imports an unwritten
symbol — ``rest_framework_simplejwt.tokens`` and ``accounts.models`` both
already exist — so collection succeeds. The failures these tests pin today
are not 404s (there is no new URL surface here, deliberately: the probe
view below bypasses routing entirely, calling ``.as_view()`` directly, the
same pattern ``tests/test_core_permissions.py`` uses for permission-class
checks) — they are assertion failures showing *today's* actual, unguarded
authentication result (a token authenticates when the contract says it
must be rejected), which is exactly the gap 3-R.E's claim check closes.

Every test below deliberately arranges a same-pk collision between a
``User`` row and an ``Account`` row (``@pytest.mark.django_db
(reset_sequences=True)`` resets both tables' sequences first, so the first
row created in each lands on id=1 — matching docs/DECISIONS.md § Stage 3-R
decisions, "Known risk... `USER_ID_FIELD` / cross-model token collision").
The collision is not incidental scaffolding: without it, today's stock
``JWTAuthentication.get_user()`` (still ``AUTH_USER_MODEL=User``, no claim
awareness) would already return 401 for any of these tokens simply because
no ``User`` row exists at the named pk — a pass for the wrong reason, not
because anything the claim contract requires was exercised. Colliding a
real ``User`` onto the same pk means today's naive lookup *succeeds*
(200), so an assertion of 401 here only starts passing once the claim is
actually checked before the pk lookup runs.

The contract under test (docs/DECISIONS.md § Stage 3-R.E):

* Tokens issued by the Account login carry ``"identity_model": "account"``.
* ``JWTAuthentication.get_user()`` verifies this claim *before* any pk
  lookup and rejects any token lacking the correct claim — including a
  still-valid legacy ``User``-issued token minted before E.
* The pk lookup, once the claim passes, always resolves against ``Account``,
  never ``User``.
"""

import pytest
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from accounts.models import Account, User
from core.tenancy import tenant_context

# Every test in this file needs a fresh, from-1 id sequence for both User
# and Account (see module docstring) — reset_sequences=True at module scope,
# not per-test, since every test here needs the same guarantee.
pytestmark = pytest.mark.django_db(reset_sequences=True)

factory = APIRequestFactory()


class _AuthenticatedProbeView(APIView):
    """No permission_classes override: relies on the project-wide
    DEFAULT_PERMISSION_CLASSES=[IsAuthenticated] default, same convention as
    tests/test_core_permissions.py's _NoPermissionClassProbeView. Calling
    .as_view()(request) directly (not through URL routing) runs the real
    DRF authentication/permission pipeline against whatever Authorization
    header the request carries."""

    def get(self, request):
        return Response({"ok": True})


def _make_colliding_user_and_account(salon) -> tuple[User, Account]:
    user = User.objects.create_user(
        email="legacy-collision@example.com", password="a-strong-passw0rd!"
    )
    with tenant_context(salon.id):
        account = Account.objects.create_account(
            salon=salon,
            email="named-collision@example.com",
            password="a-strong-passw0rd!",
        )
    assert user.pk == account.pk == 1  # the exact collision this substep exists to guard against
    return user, account


# --- C. claim mechanism (point test) --------------------------------------


def test_token_missing_identity_model_claim_is_rejected_despite_a_same_id_account_existing(
    salon,
) -> None:
    _user, account = _make_colliding_user_and_account(salon)

    token = AccessToken.for_user(account)  # no identity_model claim set at all

    request = factory.get("/", HTTP_AUTHORIZATION=f"Bearer {token}")
    response = _AuthenticatedProbeView.as_view()(request)

    assert response.status_code == 401


def test_token_with_wrong_identity_model_claim_is_rejected_despite_a_same_id_account_existing(
    salon,
) -> None:
    _user, account = _make_colliding_user_and_account(salon)

    token = AccessToken.for_user(account)
    token["identity_model"] = "user"  # corrupted: names the wrong identity model

    request = factory.get("/", HTTP_AUTHORIZATION=f"Bearer {token}")
    response = _AuthenticatedProbeView.as_view()(request)

    assert response.status_code == 401


# --- D. the real cross-model collision (the scenario 3-R.E exists for) ---


def test_legacy_pre_e_user_token_does_not_authenticate_as_the_same_id_account(salon) -> None:
    user, _account = _make_colliding_user_and_account(salon)

    # The pre-E way: RefreshToken.for_user() on a real User, no
    # identity_model claim at all — exactly what a client holding a
    # still-valid token minted before E would present.
    token = RefreshToken.for_user(user)

    request = factory.get("/", HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    response = _AuthenticatedProbeView.as_view()(request)

    assert response.status_code == 401
