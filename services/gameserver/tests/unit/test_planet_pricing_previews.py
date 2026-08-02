"""WO-API-PHASE1 Lane A (B3 + B4): server-authoritative planetary pricing
previews -- thin-client, advisory-only quotes the client now reads instead of
re-deriving.

B3: GET /planets/{planet_id}/defenses/pricing -- prices EXACTLY what the
existing PUT .../defenses commit path (PlanetaryService.update_defenses)
charges per added unit, via the SAME defense_unit_price(unit_type,
citadel_level, planet_type) function (a pure function of those three inputs --
no per-player secret state, no DB access). Owner-gated (403), read-only
(no db.commit), and its response carries ONLY the unit_type -> price mapping.

B4: PlanetaryService._get_buildings_data now embeds nextUpgradeCost per
building, computed via the SAME _calculate_upgrade_cost(building_type,
current_level, current_level + 1) the existing POST .../buildings/upgrade
commit path (upgrade_building) charges. None once a building is already at
MAX_BUILDING_LEVEL (no next level to price).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.auth.dependencies import get_current_player
from src.core.database import get_db
from src.main import app
from src.models.planet import PlanetType
from src.services.planetary_service import (
    BUILDING_LEVEL_TYPES,
    MAX_BUILDING_LEVEL,
    PlanetaryService,
    defense_unit_price,
)


# --------------------------------------------------------------------------- #
# B3 -- GET /planets/{planet_id}/defenses/pricing
# --------------------------------------------------------------------------- #

PRICING_URL = "/api/v1/planets/{planet_id}/defenses/pricing"


def _pricing_player():
    return SimpleNamespace(id=uuid4(), credits=100000)


def _pricing_db(owned_planet):
    """Routes db.query(Planet).join(...).filter(...).first() -- owned_planet
    is what the ownership join+filter would return for the calling player
    (None simulates not-found-or-not-owned, mirroring update_defenses' own
    player_planets ownership check)."""
    db = MagicMock()
    q = MagicMock()
    q.join.return_value = q
    q.filter.return_value = q
    q.first.return_value = owned_planet
    db.query.return_value = q
    return db


@pytest.fixture
def pricing_client():
    return TestClient(app, base_url="http://localhost")


@pytest.fixture(autouse=True)
def _isolate_route_overrides():
    saved_player = app.dependency_overrides.get(get_current_player)
    saved_db = app.dependency_overrides.get(get_db)
    yield
    for key, saved in ((get_current_player, saved_player), (get_db, saved_db)):
        if saved is not None:
            app.dependency_overrides[key] = saved
        else:
            app.dependency_overrides.pop(key, None)


class TestDefensePricingRoute:
    def _authed(self, player, db):
        app.dependency_overrides[get_current_player] = lambda: player
        app.dependency_overrides[get_db] = lambda: db

    @pytest.mark.parametrize("citadel_level", [None, 1, 2, 3, 4, 5, 9])
    @pytest.mark.parametrize(
        "planet_type", [PlanetType.TERRAN, PlanetType.GAS_GIANT, PlanetType.MOUNTAINOUS]
    )
    def test_pricing_matches_defense_unit_price_exactly(self, pricing_client, citadel_level, planet_type):
        """DRY: the route's numbers must equal defense_unit_price -- the SAME
        function update_defenses uses to charge -- for every unit type."""
        player = _pricing_player()
        planet = SimpleNamespace(id=uuid4(), citadel_level=citadel_level, type=planet_type)
        db = _pricing_db(planet)
        self._authed(player, db)

        resp = pricing_client.get(PRICING_URL.format(planet_id=str(planet.id)))

        assert resp.status_code == 200
        body = resp.json()
        assert body["turrets"] == defense_unit_price("turrets", citadel_level, planet_type)
        assert body["shields"] == defense_unit_price("shields", citadel_level, planet_type)
        assert body["fighters"] == defense_unit_price("fighters", citadel_level, planet_type)

    def test_pricing_response_exposes_only_unit_type_to_price(self, pricing_client):
        """No citadel_level / planet_type / other-player data -- ONLY the
        unit_type -> price mapping, per the WO's minimal-response mandate."""
        player = _pricing_player()
        planet = SimpleNamespace(id=uuid4(), citadel_level=3, type=PlanetType.DESERT)
        db = _pricing_db(planet)
        self._authed(player, db)

        resp = pricing_client.get(PRICING_URL.format(planet_id=str(planet.id)))

        assert resp.status_code == 200
        assert set(resp.json().keys()) == {"turrets", "shields", "fighters"}

    def test_pricing_denies_non_owner_with_403(self, pricing_client):
        """The ownership join+filter finds nothing for this caller (either the
        planet doesn't exist, or it belongs to someone else) -- both deny
        identically, mirroring update_defenses' own semantics."""
        player = _pricing_player()
        db = _pricing_db(None)
        self._authed(player, db)

        resp = pricing_client.get(PRICING_URL.format(planet_id=str(uuid4())))

        assert resp.status_code == 403

    def test_pricing_invalid_planet_id_returns_400(self, pricing_client):
        player = _pricing_player()
        db = _pricing_db(None)
        self._authed(player, db)

        resp = pricing_client.get(PRICING_URL.format(planet_id="not-a-uuid"))

        assert resp.status_code == 400

    def test_pricing_is_read_only(self, pricing_client):
        """No mutation: the route never calls db.commit/flush/add -- a pure
        preview, the commit route remains the sole source of the real charge."""
        player = _pricing_player()
        planet = SimpleNamespace(id=uuid4(), citadel_level=2, type=PlanetType.OCEANIC)
        db = _pricing_db(planet)
        self._authed(player, db)

        resp = pricing_client.get(PRICING_URL.format(planet_id=str(planet.id)))

        assert resp.status_code == 200
        db.commit.assert_not_called()
        db.flush.assert_not_called()
        db.add.assert_not_called()


# --------------------------------------------------------------------------- #
# B4 -- PlanetaryService._get_buildings_data's nextUpgradeCost field
# --------------------------------------------------------------------------- #

def _make_planet(levels: dict, active_events=None):
    """A minimal Planet-shaped SimpleNamespace: _get_buildings_data only ever
    reads active_events + the per-building *_level columns, no DB access."""
    return SimpleNamespace(
        active_events=active_events or {},
        factory_level=levels.get("factory", 0),
        farm_level=levels.get("farm", 0),
        mine_level=levels.get("mine", 0),
        defense_level=levels.get("defense", 0),
        research_level=levels.get("research", 0),
    )


def _buildings_by_type(service, planet):
    return {b["type"]: b for b in service._get_buildings_data(planet)}


def test_next_upgrade_cost_matches_calculate_upgrade_cost_for_every_building_type():
    service = PlanetaryService(db=MagicMock())
    planet = _make_planet({bt: 3 for bt in BUILDING_LEVEL_TYPES})

    buildings = _buildings_by_type(service, planet)

    for building_type in BUILDING_LEVEL_TYPES:
        expected = service._calculate_upgrade_cost(building_type, 3, 4)
        assert buildings[building_type]["nextUpgradeCost"] == expected


def test_next_upgrade_cost_tracks_current_level():
    service = PlanetaryService(db=MagicMock())
    planet = _make_planet({"factory": 1, "mine": 7})

    buildings = _buildings_by_type(service, planet)

    assert buildings["factory"]["nextUpgradeCost"] == service._calculate_upgrade_cost("factory", 1, 2)
    assert buildings["mine"]["nextUpgradeCost"] == service._calculate_upgrade_cost("mine", 7, 8)
    # Sanity: the two differ (proves this isn't a hardcoded constant).
    assert buildings["factory"]["nextUpgradeCost"] != buildings["mine"]["nextUpgradeCost"]


def test_next_upgrade_cost_is_none_at_the_server_level_cap():
    service = PlanetaryService(db=MagicMock())
    planet = _make_planet({"research": MAX_BUILDING_LEVEL})

    buildings = _buildings_by_type(service, planet)

    assert buildings["research"]["nextUpgradeCost"] is None


def test_next_upgrade_cost_present_one_level_below_the_cap():
    service = PlanetaryService(db=MagicMock())
    planet = _make_planet({"research": MAX_BUILDING_LEVEL - 1})

    buildings = _buildings_by_type(service, planet)

    expected = service._calculate_upgrade_cost("research", MAX_BUILDING_LEVEL - 1, MAX_BUILDING_LEVEL)
    assert buildings["research"]["nextUpgradeCost"] == expected


def test_get_buildings_data_is_read_only():
    """No DB access at all -- self.db is never touched (pure column reads)."""
    db = MagicMock()
    service = PlanetaryService(db=db)
    planet = _make_planet({"factory": 2})

    service._get_buildings_data(planet)

    db.query.assert_not_called()
    db.commit.assert_not_called()
