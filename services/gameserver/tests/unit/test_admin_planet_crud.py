"""Admin planet PATCH + DELETE authz + colonized-guard proof (DB-free).

Auth gate: get_current_admin → get_current_admin_user → get_current_user.
We override get_current_user (NOT require_admin / get_current_admin directly)
so the real is_admin check still executes — that's what makes the 403 case a
genuine authz proof, not a rubber stamp.

DB-free mechanics (from WO-PINFRA-CI-PYTEST-LANE):
- TestClient(app, base_url="http://localhost") avoids TrustedHostMiddleware 400.
- Fixture name is NOT "client" to avoid the conftest name-match skip heuristic.
- For 401/403 paths: admin dep resolves before db dep in the route signature,
  so an auth failure never touches get_db — no real Postgres connection needed.
- For happy-path + guard paths: override get_db with a MagicMock session.
"""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.auth.dependencies import get_current_user
from src.core.database import get_db
from src.main import app

API = "/api/v1/admin/planets"


def _admin_user():
    return SimpleNamespace(id=uuid.uuid4(), username="admin", is_admin=True)


def _player_user():
    return SimpleNamespace(id=uuid.uuid4(), username="trader", is_admin=False)


def _make_planet(
    owner_id=None,
    colonized_at=None,
    population=0,
    status_value="UNINHABITABLE",
    is_population_hub=False,
):
    """Build a fake Planet object with the fields the delete guard checks."""
    from src.models.planet import PlanetStatus
    planet = MagicMock()
    planet.id = uuid.uuid4()
    planet.name = "TestWorld"
    planet.owner_id = owner_id
    planet.colonized_at = colonized_at
    planet.population = population
    planet.status = PlanetStatus(status_value)
    planet.is_population_hub = is_population_hub
    return planet


def _make_db(planet=None, player=None):
    """Return a mock Session whose Planet lookup resolves to `planet` and
    whose AdminScopeGrant lookup (require_scope(GALAXY_MANAGE) -- both
    routes are RBAC-E5-wrapped, gated on an active scope grant rather than
    is_admin alone) resolves to a granted row. A blanket
    `db.query.return_value...` accidentally coupled the two unrelated
    lookups (same mock answers both "planet exists?" and "admin has scope?"),
    which happened to pass when `planet` was truthy and 403'd instead of
    404'd when it wasn't -- dispatch per queried model instead.

    Optional `player` answers Player.id lookups for owner_id PATCH validation.
    """
    db = MagicMock()

    def _query(model):
        q = MagicMock()
        name = getattr(model, "__name__", str(model))
        model_s = str(model)
        if "AdminScopeGrant" in model_s or name == "AdminScopeGrant":
            q.filter.return_value.first.return_value = (uuid.uuid4(),)  # active grant
        elif "Player" in model_s or name == "Player":
            q.filter.return_value.first.return_value = player
        else:
            q.filter.return_value.first.return_value = planet
        return q

    db.query.side_effect = _query
    return db


@pytest.fixture
def planet_client():
    return TestClient(app, base_url="http://localhost")


def _clear_admin_rate_limit_buckets() -> None:
    """Reset live RateLimitingMiddleware buckets for TestClient isolation.

    Admin ``/admin`` paths are *not* skipped under User-Agent testclient
    (only non-admin paths are). Dependency overrides authenticate without a
    Bearer JWT, so peek_request_user_id falls through to ``admin-ip:<host>``
    and every admin write in the DB-free suite shares one 30/min write
    bucket — enough to bleed 429 into later delete-planet cases in CI.
    """
    from src.api.middleware.security import RateLimitingMiddleware

    stack = getattr(app, "middleware_stack", None)
    if stack is None:
        try:
            stack = app.build_middleware_stack()
            app.middleware_stack = stack
        except Exception:
            return
    seen: set[int] = set()
    cur = stack
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, RateLimitingMiddleware):
            cur.request_counts.clear()
        cur = getattr(cur, "app", None)


@pytest.fixture(autouse=True)
def _isolate_overrides():
    # Clear before each test so prior admin write traffic (this file or
    # earlier suite modules) cannot 429 a colonized-guard / success DELETE.
    _clear_admin_rate_limit_buckets()
    saved_user = app.dependency_overrides.get(get_current_user)
    saved_db = app.dependency_overrides.get(get_db)
    yield
    for key, saved in ((get_current_user, saved_user), (get_db, saved_db)):
        if saved is not None:
            app.dependency_overrides[key] = saved
        else:
            app.dependency_overrides.pop(key, None)
    _clear_admin_rate_limit_buckets()


# ---------------------------------------------------------------------------
# PATCH authz
# ---------------------------------------------------------------------------

class TestPatchPlanetAuthz:
    """PATCH /admin/planets/{id} — auth gate fires before any DB access."""

    def test_unauthenticated_returns_401(self, planet_client):
        resp = planet_client.patch(f"{API}/{uuid.uuid4()}", json={"name": "X"})
        assert resp.status_code == 401

    def test_non_admin_returns_403(self, planet_client):
        app.dependency_overrides[get_current_user] = _player_user
        resp = planet_client.patch(f"{API}/{uuid.uuid4()}", json={"name": "X"})
        assert resp.status_code == 403

    def test_admin_missing_planet_returns_404(self, planet_client):
        app.dependency_overrides[get_current_user] = _admin_user
        app.dependency_overrides[get_db] = lambda: _make_db(planet=None)
        resp = planet_client.patch(f"{API}/{uuid.uuid4()}", json={"name": "X"})
        assert resp.status_code == 404

    def test_admin_patch_updates_allowed_fields(self, planet_client):
        planet = _make_planet()
        db = _make_db(planet=planet)
        app.dependency_overrides[get_current_user] = _admin_user
        app.dependency_overrides[get_db] = lambda: db
        resp = planet_client.patch(
            f"{API}/{planet.id}",
            json={"name": "NewName", "habitability_score": 75},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "updated_fields" in body
        assert "name" in body["updated_fields"]
        assert "habitability_score" in body["updated_fields"]
        db.commit.assert_called_once()

    def test_admin_patch_invalid_planet_type_returns_400(self, planet_client):
        planet = _make_planet()
        app.dependency_overrides[get_current_user] = _admin_user
        app.dependency_overrides[get_db] = lambda: _make_db(planet=planet)
        resp = planet_client.patch(
            f"{API}/{planet.id}",
            json={"type": "NOT_A_REAL_TYPE"},
        )
        assert resp.status_code == 400



# ---------------------------------------------------------------------------
# PATCH owner_id (LEG-1446)
# ---------------------------------------------------------------------------

class TestPatchPlanetOwnerId:
    """PATCH owner_id persists / clears; unknown player 404."""

    def test_admin_patch_sets_owner_id(self, planet_client):
        planet = _make_planet()
        owner = SimpleNamespace(id=uuid.uuid4())
        db = _make_db(planet=planet, player=owner)
        app.dependency_overrides[get_current_user] = _admin_user
        app.dependency_overrides[get_db] = lambda: db
        resp = planet_client.patch(
            f"{API}/{planet.id}",
            json={"owner_id": str(owner.id)},
        )
        assert resp.status_code == 200
        assert "owner_id" in resp.json()["updated_fields"]
        assert planet.owner_id == str(owner.id)
        db.commit.assert_called_once()

    def test_admin_patch_clears_owner_id_with_null(self, planet_client):
        planet = _make_planet(owner_id=uuid.uuid4())
        db = _make_db(planet=planet)
        app.dependency_overrides[get_current_user] = _admin_user
        app.dependency_overrides[get_db] = lambda: db
        resp = planet_client.patch(
            f"{API}/{planet.id}",
            json={"owner_id": None},
        )
        assert resp.status_code == 200
        assert "owner_id" in resp.json()["updated_fields"]
        assert planet.owner_id is None
        db.commit.assert_called_once()

    def test_admin_patch_unknown_owner_returns_404(self, planet_client):
        planet = _make_planet()
        db = _make_db(planet=planet, player=None)
        app.dependency_overrides[get_current_user] = _admin_user
        app.dependency_overrides[get_db] = lambda: db
        resp = planet_client.patch(
            f"{API}/{planet.id}",
            json={"owner_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 404
        body = resp.json()
        # Error middleware wraps HTTPException as {error, message, ...} not FastAPI {detail}.
        msg = body.get("message") or body.get("detail") or str(body)
        assert "Player not found" in str(msg)


# ---------------------------------------------------------------------------
# DELETE authz
# ---------------------------------------------------------------------------

class TestDeletePlanetAuthz:
    """DELETE /admin/planets/{id} — auth gate fires before any DB access."""

    def test_unauthenticated_returns_401(self, planet_client):
        resp = planet_client.delete(f"{API}/{uuid.uuid4()}")
        assert resp.status_code == 401

    def test_non_admin_returns_403(self, planet_client):
        app.dependency_overrides[get_current_user] = _player_user
        resp = planet_client.delete(f"{API}/{uuid.uuid4()}")
        assert resp.status_code == 403

    def test_missing_planet_returns_404(self, planet_client):
        app.dependency_overrides[get_current_user] = _admin_user
        app.dependency_overrides[get_db] = lambda: _make_db(planet=None)
        resp = planet_client.delete(f"{API}/{uuid.uuid4()}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE colonized guard — fail-closed adversarial proof
# ---------------------------------------------------------------------------

class TestDeletePlanetColonizedGuard:
    """DELETE must refuse (409) any planet that shows signs of habitation.

    Adversarial contract: db.delete is NEVER called when the guard triggers.
    """

    def _assert_guard_fires(self, planet_client, planet):
        db = _make_db(planet=planet)
        app.dependency_overrides[get_current_user] = _admin_user
        app.dependency_overrides[get_db] = lambda: db
        resp = planet_client.delete(f"{API}/{planet.id}")
        assert resp.status_code == 409, f"Expected 409 for colonized planet, got {resp.status_code}"
        db.delete.assert_not_called()
        # admin_action_attempt rolls back any buffered mutation before its
        # own best-effort commit of the blocked-attempt audit row (RBAC
        # E-5) -- a commit call here is the audit trail, not the guarded
        # delete, so the load-bearing proof is delete-never-called + the
        # rollback that precedes the audit commit.
        db.rollback.assert_called_once()

    def test_planet_with_owner_id_is_refused(self, planet_client):
        self._assert_guard_fires(planet_client, _make_planet(owner_id=uuid.uuid4()))

    def test_planet_with_colonized_at_is_refused(self, planet_client):
        from datetime import datetime, timezone
        self._assert_guard_fires(
            planet_client,
            _make_planet(colonized_at=datetime.now(timezone.utc)),
        )

    def test_planet_with_nonzero_population_is_refused(self, planet_client):
        self._assert_guard_fires(planet_client, _make_planet(population=1))

    def test_planet_with_colonized_status_is_refused(self, planet_client):
        self._assert_guard_fires(planet_client, _make_planet(status_value="COLONIZED"))

    def test_planet_with_developed_status_is_refused(self, planet_client):
        self._assert_guard_fires(planet_client, _make_planet(status_value="DEVELOPED"))

    def test_planet_with_dying_status_is_refused(self, planet_client):
        self._assert_guard_fires(planet_client, _make_planet(status_value="DYING"))

    def test_planet_with_restricted_status_is_refused(self, planet_client):
        self._assert_guard_fires(planet_client, _make_planet(status_value="RESTRICTED"))

    def test_planet_with_terraforming_status_is_refused(self, planet_client):
        self._assert_guard_fires(planet_client, _make_planet(status_value="TERRAFORMING"))

    def test_population_hub_is_refused(self, planet_client):
        self._assert_guard_fires(planet_client, _make_planet(is_population_hub=True))


# ---------------------------------------------------------------------------
# DELETE success — uncolonized planet
# ---------------------------------------------------------------------------

class TestDeletePlanetSuccess:
    """DELETE succeeds for a clean, uncolonized planet."""

    def test_uncolonized_planet_is_deleted(self, planet_client):
        planet = _make_planet(
            owner_id=None,
            colonized_at=None,
            population=0,
            status_value="UNINHABITABLE",
        )
        db = _make_db(planet=planet)
        app.dependency_overrides[get_current_user] = _admin_user
        app.dependency_overrides[get_db] = lambda: db

        resp = planet_client.delete(f"{API}/{planet.id}")

        assert resp.status_code == 200
        body = resp.json()
        assert "deleted" in body["message"].lower() or "success" in body["message"].lower()
        db.delete.assert_called_once_with(planet)
        db.commit.assert_called_once()

    def test_habitable_uncolonized_planet_is_deleted(self, planet_client):
        planet = _make_planet(status_value="HABITABLE")
        db = _make_db(planet=planet)
        app.dependency_overrides[get_current_user] = _admin_user
        app.dependency_overrides[get_db] = lambda: db

        resp = planet_client.delete(f"{API}/{planet.id}")

        assert resp.status_code == 200
        db.delete.assert_called_once_with(planet)
