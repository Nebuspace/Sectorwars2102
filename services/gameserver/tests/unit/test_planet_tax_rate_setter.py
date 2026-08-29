"""LEG-525: planet owner PATCH tax-rate setter."""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.auth.dependencies import get_current_player
from src.core.database import get_db
from src.main import app

API = "/api/v1/planets"


def _owner():
    return SimpleNamespace(id=uuid.uuid4(), username="colonist")


def _other_player():
    return SimpleNamespace(id=uuid.uuid4(), username="rival")


def _planet(owner_id):
    planet = MagicMock()
    planet.id = uuid.uuid4()
    planet.name = "TestColony"
    planet.owner_id = owner_id
    planet.tax_rate = None
    return planet


def _make_db(planet):
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value.with_for_update.return_value.first.return_value = planet
    db.query.return_value = q
    return db


@pytest.fixture
def tax_client():
    return TestClient(app, base_url="http://localhost")


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_owner_can_set_tax_rate(tax_client):
    owner = _owner()
    planet = _planet(owner.id)
    app.dependency_overrides[get_current_player] = lambda: owner
    app.dependency_overrides[get_db] = lambda: _make_db(planet)

    resp = tax_client.patch(
        f"{API}/{planet.id}/tax-rate",
        json={"tax_rate": 0.12},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tax_rate"] == 0.12
    assert planet.tax_rate == 0.12


def test_non_owner_gets_403(tax_client):
    owner = _owner()
    planet = _planet(owner.id)
    app.dependency_overrides[get_current_player] = lambda: _other_player()
    app.dependency_overrides[get_db] = lambda: _make_db(planet)

    resp = tax_client.patch(
        f"{API}/{planet.id}/tax-rate",
        json={"tax_rate": 0.10},
    )
    assert resp.status_code == 403


def test_tax_rate_clamped_to_canon_max(tax_client):
    owner = _owner()
    planet = _planet(owner.id)
    app.dependency_overrides[get_current_player] = lambda: owner
    app.dependency_overrides[get_db] = lambda: _make_db(planet)

    resp = tax_client.patch(
        f"{API}/{planet.id}/tax-rate",
        json={"tax_rate": 0.99},
    )
    assert resp.status_code == 200
    assert resp.json()["tax_rate"] == 0.20
