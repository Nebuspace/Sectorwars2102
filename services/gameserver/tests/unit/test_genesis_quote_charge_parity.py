"""WO-API-B2: server-authoritative genesis quote endpoint == deploy charge.

Proves ``compute_genesis_costs`` (the ONE function GenesisService.deploy_genesis_device
and the new GenesisService.get_genesis_quote both call) is correct against the
FROZEN registry-fee contract, that ``get_genesis_quote`` is read-only, and --
the load-bearing property -- that a live call through the real
``deploy_genesis_device`` charges EXACTLY what ``compute_genesis_costs`` (and
therefore the quote route) reports for the same (tier, registration, player
reputation) inputs. Device tiers `basic`/`enhanced` are exercised end-to-end;
`advanced` (Colony-Ship-sacrifice + instant Settlement formation) is exercised
only via the pure-function matrix below -- covering its cost number without
faking ShipService.destroy_ship / citadel-formation, which are orthogonal to
the cost/fee delegation this WO is about.

DB-free: `self.db` is a hand-rolled MagicMock router (this repo's established
pattern for GenesisService -- see test_genesis_available_purchases_reputation_gate.py)
that dispatches `.query(Model)` by model identity, never touching Postgres.

REVISE (mack HIGH + cipher LOW + mack LOW): also covers the `GET /genesis/quote`
route's Pydantic-level tier/registration validation (mirrors the deploy route's
Field pattern -- defense-in-depth so both paths reject a bogus value the same
way, before ever reaching the service layer).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.auth.dependencies import get_current_player
from src.core.database import get_db
from src.main import app
from src.models.player import Player
from src.models.sector import Sector
from src.models.ship import Ship, ShipType
from src.services.genesis_service import (
    GENESIS_TIERS,
    GenesisService,
    compute_genesis_costs,
)


# --------------------------------------------------------------------------- #
#  compute_genesis_costs -- pure-function correctness matrix
# --------------------------------------------------------------------------- #

# FROZEN registry contract fixed fees.
REGISTERED_FEE = 10000
CLANDESTINE_FEE = 60000


def _chartered_fee(rep: int) -> int:
    """Mirrors the FROZEN chartered curve independently of compute_genesis_costs,
    so the correctness assertions below aren't just re-stating the implementation."""
    rep_factor = max(0.0, min(1.0, rep / 1000.0))
    return int(10000 + 40000 * (1 - rep_factor * 0.75))


@pytest.mark.parametrize("tier", ["basic", "enhanced", "advanced"])
def test_device_cost_matches_tier_table(tier):
    result = compute_genesis_costs(tier, "registered", personal_reputation=0)
    assert result["device_cost"] == GENESIS_TIERS[tier]["cost"]


@pytest.mark.parametrize("tier", ["basic", "enhanced", "advanced"])
def test_registered_fee_is_fixed_regardless_of_reputation(tier):
    for rep in (0, 250, 1000, 5000):
        result = compute_genesis_costs(tier, "registered", personal_reputation=rep)
        assert result["registration_fee"] == REGISTERED_FEE
        assert result["total_cost"] == GENESIS_TIERS[tier]["cost"] + REGISTERED_FEE


@pytest.mark.parametrize("tier", ["basic", "enhanced", "advanced"])
def test_clandestine_fee_is_fixed_regardless_of_reputation(tier):
    for rep in (0, 250, 1000, 5000):
        result = compute_genesis_costs(tier, "clandestine", personal_reputation=rep)
        assert result["registration_fee"] == CLANDESTINE_FEE
        assert result["total_cost"] == GENESIS_TIERS[tier]["cost"] + CLANDESTINE_FEE


# Boundaries the scaling curve actually bends at/clamps at: 0 (floor, max fee),
# 1000 (ceiling, min fee), values below/above the [0, 1000] clamp range, and
# the falsy-reputation defaults (None / 0).
@pytest.mark.parametrize(
    "rep,expected_fee",
    [
        (None, 50000),   # falsy -> treated as 0 -> rep_factor 0 -> 10000 + 40000*1
        (0, 50000),
        (-500, 50000),   # clamped up to 0
        (1, _chartered_fee(1)),
        (249, _chartered_fee(249)),
        (250, _chartered_fee(250)),  # GENESIS_MIN_REPUTATION deploy-gate value -- not special to the fee curve
        (500, _chartered_fee(500)),
        (999, _chartered_fee(999)),
        (1000, 20000),   # 10000 + 40000*(1-0.75) = 20000, the fee floor
        (1500, 20000),   # clamped down to 1000 -> same floor
        (5000, 20000),   # clamped down to 1000 -> same floor
    ],
)
def test_chartered_fee_scales_with_reputation_and_clamps(rep, expected_fee):
    result = compute_genesis_costs("basic", "chartered", personal_reputation=rep)
    assert result["registration_fee"] == expected_fee
    assert result["total_cost"] == GENESIS_TIERS["basic"]["cost"] + expected_fee


def test_case_insensitive_and_default_registration():
    # deploy_genesis_device lowercases both tier/registration before this call
    # and defaults a missing registration to "registered" -- mirror both here.
    upper = compute_genesis_costs("BASIC", "CHARTERED", personal_reputation=1000)
    lower = compute_genesis_costs("basic", "chartered", personal_reputation=1000)
    assert upper == lower

    defaulted = compute_genesis_costs("basic", None, personal_reputation=0)
    assert defaulted["registration"] == "registered"
    assert defaulted["registration_fee"] == REGISTERED_FEE


def test_invalid_tier_raises_value_error():
    with pytest.raises(ValueError, match="Invalid genesis device tier"):
        compute_genesis_costs("mythic", "registered", personal_reputation=0)


def test_invalid_registration_raises_value_error():
    with pytest.raises(ValueError, match="Invalid registration status"):
        compute_genesis_costs("basic", "anonymous", personal_reputation=0)


# --------------------------------------------------------------------------- #
#  GenesisService.get_genesis_quote -- read-only, player-priced
# --------------------------------------------------------------------------- #

def _make_quote_db(player):
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.first.return_value = player
    db.query.return_value = q
    return db


def test_quote_prices_for_the_calling_players_reputation():
    player = SimpleNamespace(id=uuid4(), credits=100000, personal_reputation=1000)
    db = _make_quote_db(player)
    service = GenesisService(db)

    result = service.get_genesis_quote(player.id, "basic", "chartered")

    expected = compute_genesis_costs("basic", "chartered", 1000)
    assert result["device_cost"] == expected["device_cost"]
    assert result["registration_fee"] == expected["registration_fee"]
    assert result["total_cost"] == expected["total_cost"]
    assert result["player_credits"] == 100000
    assert result["can_afford"] is (100000 >= expected["total_cost"])


def test_quote_is_read_only_no_writes_no_credit_or_reputation_change():
    player = SimpleNamespace(id=uuid4(), credits=42, personal_reputation=250)
    db = _make_quote_db(player)
    service = GenesisService(db)

    service.get_genesis_quote(player.id, "enhanced", "clandestine")

    assert player.credits == 42
    assert player.personal_reputation == 250
    db.commit.assert_not_called()
    db.flush.assert_not_called()
    db.add.assert_not_called()


def test_quote_reputation_gate_reflects_player():
    below = SimpleNamespace(id=uuid4(), credits=0, personal_reputation=100)
    at = SimpleNamespace(id=uuid4(), credits=0, personal_reputation=250)

    result_below = GenesisService(_make_quote_db(below)).get_genesis_quote(below.id, "basic", "registered")
    result_at = GenesisService(_make_quote_db(at)).get_genesis_quote(at.id, "basic", "registered")

    assert result_below["reputation_gate"] == {"required": 250, "current": 100, "met": False}
    assert result_at["reputation_gate"] == {"required": 250, "current": 250, "met": True}


def test_quote_unknown_player_raises_value_error():
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.first.return_value = None
    db.query.return_value = q

    with pytest.raises(ValueError, match="Player not found"):
        GenesisService(db).get_genesis_quote(uuid4(), "basic", "registered")


# --------------------------------------------------------------------------- #
#  quote == charge: a real deploy_genesis_device call charges EXACTLY what
#  compute_genesis_costs (and therefore the quote route) reports
# --------------------------------------------------------------------------- #

def _make_deploy_db(*, player, ship, sector, existing_planet_count=0):
    """Routes db.query(Model) by identity to the right fixture. Everything
    else (Planet count queries, the medal-dispatch count, player_planets
    insert) is best-effort/exception-swallowed in the real code, so a
    generic MagicMock branch is sufficient for those."""
    db = MagicMock()

    def route(model, *args, **kwargs):
        q = MagicMock()
        if model is Player:
            q.filter.return_value = q
            q.populate_existing.return_value = q
            q.with_for_update.return_value = q
            q.first.return_value = player
        elif model is Ship:
            q.filter.return_value = q
            q.first.return_value = ship
        elif model is Sector:
            q.filter.return_value = q
            q.first.return_value = sector
        else:
            # func.count(Planet.id) sector-limit check + the medal-dispatch count.
            q.filter.return_value = q
            q.scalar.return_value = existing_planet_count
        return q

    db.query.side_effect = route
    return db


def _make_player(reputation, credits=10_000_000):
    return SimpleNamespace(
        id=uuid4(),
        credits=credits,
        current_ship_id=uuid4(),
        current_sector_id=42,
        is_docked=False,
        is_landed=False,
        personal_reputation=reputation,
        settings={},
        quantum_crystals=0,
    )


def _make_ship():
    return SimpleNamespace(
        id=uuid4(),
        type=ShipType.CARGO_HAULER,  # GENESIS_CAPACITY_BY_SHIP[CARGO_HAULER] == 2 (> 0)
        genesis_devices=10,          # covers basic (1) and enhanced (3) device costs
        upgrades={},
        name="Test Hull",
    )


def _make_sector():
    return SimpleNamespace(id=uuid4(), sector_id=42, region_id=uuid4())


# Player.personal_reputation is `nullable=False, default=0` (src/models/player.py) --
# a persisted player is never actually None, so this integration matrix sticks to
# realistic values. The None-defaults-to-zero behavior of compute_genesis_costs is
# already covered by test_chartered_fee_scales_with_reputation_and_clamps above.
REP_BOUNDARIES = [0, 1, 249, 250, 500, 999, 1000, 1500]


@pytest.mark.parametrize("tier", ["basic", "enhanced"])
@pytest.mark.parametrize("registration", ["clandestine", "registered", "chartered"])
@pytest.mark.parametrize("reputation", REP_BOUNDARIES)
def test_deploy_charges_exactly_the_quote(monkeypatch, tier, registration, reputation):
    # Isolate the cost/fee delegation from the (orthogonal) deploy-eligibility
    # gates -- distance-from-Federation / planet-spacing / anti-monopoly --
    # which is what the WO scopes this proof to.
    monkeypatch.setattr(GenesisService, "_enforce_deploy_restrictions", lambda self, player, sector: None)
    monkeypatch.setattr("src.services.structures.seed", lambda planet, db=None: {})

    player = _make_player(reputation)
    ship = _make_ship()
    sector = _make_sector()
    db = _make_deploy_db(player=player, ship=ship, sector=sector)
    service = GenesisService(db)

    expected = compute_genesis_costs(tier, registration, reputation)
    starting_credits = player.credits

    result = service.deploy_genesis_device(
        player_id=player.id,
        sector_id=sector.sector_id,
        tier=tier,
        registration=registration,
    )

    assert result["credits_spent"] == expected["total_cost"]
    assert result["registration_fee"] == expected["registration_fee"]
    assert starting_credits - player.credits == expected["total_cost"]
    assert player.credits == result["credits_remaining"]


def test_deploy_rejects_insufficient_credits_at_the_quoted_total():
    """The credits gate compares against compute_genesis_costs' total_cost --
    one credit short of the quote must still be rejected."""
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(GenesisService, "_enforce_deploy_restrictions", lambda self, player, sector: None)

        expected = compute_genesis_costs("basic", "chartered", 250)
        player = _make_player(reputation=250, credits=expected["total_cost"] - 1)
        ship = _make_ship()
        sector = _make_sector()
        db = _make_deploy_db(player=player, ship=ship, sector=sector)
        service = GenesisService(db)

        with pytest.raises(ValueError, match="Insufficient credits"):
            service.deploy_genesis_device(
                player_id=player.id,
                sector_id=sector.sector_id,
                tier="basic",
                registration="chartered",
            )
    finally:
        monkeypatch.undo()


# --------------------------------------------------------------------------- #
#  GET /genesis/quote route -- Pydantic-level tier/registration validation
#  (cipher LOW: mirrors the deploy route's Field pattern so both paths reject
#  a bogus tier/registration the same way, at the edge, before the service).
# --------------------------------------------------------------------------- #

QUOTE_URL = "/api/v1/genesis/quote"


def _route_player():
    return SimpleNamespace(id=uuid4(), credits=100000, personal_reputation=1000)


def _route_db():
    """Routes GET /genesis/quote's own db.query(Player)...first() lookup back
    to the SAME player the auth dependency was overridden with."""
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.first.return_value = _route_player()
    db.query.return_value = q
    return db


@pytest.fixture
def genesis_client():
    return TestClient(app, base_url="http://localhost")


@pytest.fixture(autouse=True)
def _isolate_genesis_route_overrides():
    saved_player = app.dependency_overrides.get(get_current_player)
    saved_db = app.dependency_overrides.get(get_db)
    yield
    for key, saved in ((get_current_player, saved_player), (get_db, saved_db)):
        if saved is not None:
            app.dependency_overrides[key] = saved
        else:
            app.dependency_overrides.pop(key, None)


class TestGenesisQuoteRouteValidation:
    def _authed(self):
        app.dependency_overrides[get_current_player] = _route_player
        app.dependency_overrides[get_db] = _route_db

    def test_valid_tier_and_registration_returns_200(self, genesis_client):
        self._authed()
        resp = genesis_client.get(QUOTE_URL, params={"tier": "basic", "registration": "chartered"})
        assert resp.status_code == 200
        body = resp.json()
        expected = compute_genesis_costs("basic", "chartered", 1000)
        assert body["device_cost"] == expected["device_cost"]
        assert body["registration_fee"] == expected["registration_fee"]
        assert body["total_cost"] == expected["total_cost"]

    def test_missing_tier_returns_422(self, genesis_client):
        self._authed()
        resp = genesis_client.get(QUOTE_URL)
        assert resp.status_code == 422

    def test_invalid_tier_returns_422_before_reaching_service(self, genesis_client):
        self._authed()
        resp = genesis_client.get(QUOTE_URL, params={"tier": "mythic"})
        assert resp.status_code == 422

    def test_invalid_registration_returns_422_before_reaching_service(self, genesis_client):
        self._authed()
        resp = genesis_client.get(QUOTE_URL, params={"tier": "basic", "registration": "anonymous"})
        assert resp.status_code == 422

    def test_omitted_registration_defaults_to_registered(self, genesis_client):
        self._authed()
        resp = genesis_client.get(QUOTE_URL, params={"tier": "basic"})
        assert resp.status_code == 200
        assert resp.json()["registration"] == "registered"
