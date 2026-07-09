"""WO-SHIP-INSURANCE-CANON: two canon-conformance gates on ship insurance
(DB-free, direct-route-call house pattern -- mirrors
tests/unit/test_trading_core_pins.py's _FakeQuery/_FakeSession convention).

  1. Non-insurability is now a REGISTRY flag (ShipSpecification.insurable),
     not a route-level ShipType set. The purchase/upgrade route reads
     spec.insurable off the DB row -- proven by flipping the fixture row on
     an arbitrary type (not just the canon Warp Jumper / Escape Pod pair) and
     observing the identical reject with zero code change.

  2. "Friendly port" (ship-insurance.md:48): buying or upgrading insurance
     requires >= NEUTRAL reputation with the station's controlling faction.
     Both the fresh-purchase (NONE -> tier) and upgrade (tier -> higher tier)
     paths run through the SAME route and hit the SAME gate, checked before
     any credits move. A factionless station (no faction_affiliation, or one
     that doesn't resolve to a seeded Faction row) has no faction to be
     unfriendly with and always passes -- NO-CANON, flagged to DECISIONS.

Two test tiers:
  TestInsuranceStatusInsurableField -- pure _insurance_status(ship, spec)
    calls. Fully DB-free: no route, no session.
  TestNonInsurableFlagGate / TestFriendlyPortReputationGate -- call the REAL
    purchase_ship_insurance / get_ship_insurance route coroutines directly
    against a hand-built fake Session (mirrors test_trading_core_pins.py's
    _FakeQuery/_FakeSession, keyed by model class; a query for an
    unspecified model raises AssertionError -- deliberate, proves a
    factionless station never queries Faction/Reputation at all).
"""
from __future__ import annotations

import ast
import pathlib
import uuid
from typing import Any, Dict
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

from src.api.routes.ship_upgrades import (
    INSURANCE_NET_PAYOUT_PCT,
    INSURANCE_PREMIUM_PCT,
    INSURANCE_TIER_ORDER,
    InsurancePurchaseRequest,
    _insurance_status,
    get_ship_insurance,
    purchase_ship_insurance,
)
from src.models.faction import Faction, FactionType
from src.models.player import Player
from src.models.reputation import Reputation, ReputationLevel
from src.models.ship import Ship, ShipSpecification, ShipType
from src.models.station import Station, StationType

# ---------------------------------------------------------------------------
# Fixture builders -- real (unpersisted) ORM instances, never flushed.
# ---------------------------------------------------------------------------


def _player(*, id=None, credits=100_000, is_docked=True, current_port_id=None,
            current_sector_id=1) -> Player:
    return Player(
        id=id or uuid.uuid4(),
        credits=credits,
        is_docked=is_docked,
        current_port_id=current_port_id,
        current_sector_id=current_sector_id,
    )


def _ship(*, id=None, owner_id=None, ship_type=ShipType.LIGHT_FREIGHTER,
          purchase_value=80_000, is_destroyed=False, insurance=None) -> Ship:
    return Ship(
        id=id or uuid.uuid4(),
        name="Test Hull",
        type=ship_type,
        owner_id=owner_id,
        sector_id=1,
        base_speed=1.0,
        current_speed=1.0,
        turn_cost=1,
        maintenance={},
        cargo={},
        combat={},
        is_destroyed=is_destroyed,
        purchase_value=purchase_value,
        current_value=purchase_value,
        insurance=insurance,
    )


def _station(*, id=None, faction_affiliation=None, offers_insurance=True) -> Station:
    return Station(
        id=id or uuid.uuid4(),
        name="Test Station",
        sector_id=1,
        type=StationType.TRADING,
        faction_affiliation=faction_affiliation,
        services={"insurance": True} if offers_insurance else {},
    )


def _faction(*, id=None, name="Terran Federation") -> Faction:
    return Faction(id=id or uuid.uuid4(), name=name, faction_type=FactionType.FEDERATION)


def _reputation(*, player_id, faction_id, level: ReputationLevel) -> Reputation:
    return Reputation(player_id=player_id, faction_id=faction_id, current_level=level)


class _FakeQuery:
    def __init__(self, *, first: Any = None) -> None:
        self._first = first

    def filter(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def with_for_update(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def first(self) -> Any:
        return self._first


class _FakeSession:
    """Keyed by model class. A query for an unspecified model raises
    AssertionError -- proves e.g. a factionless station never touches
    Faction/Reputation at all (zero extra queries)."""

    def __init__(self, specs: Dict[type, _FakeQuery]) -> None:
        self._specs = specs
        self.commit_calls = 0

    def query(self, target: Any) -> _FakeQuery:
        key = target if isinstance(target, type) else target.class_
        assert key in self._specs, f"unexpected query for {target!r}"
        return self._specs[key]

    def commit(self) -> None:
        self.commit_calls += 1

    def refresh(self, obj: Any) -> None:
        pass

    def rollback(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Tier 1: pure _insurance_status(ship, spec) -- DB-free
# ---------------------------------------------------------------------------


class TestInsuranceStatusInsurableField:
    def test_insurable_true_when_spec_flag_true(self):
        ship = _ship(ship_type=ShipType.LIGHT_FREIGHTER, purchase_value=80_000)
        spec = ShipSpecification(type=ShipType.LIGHT_FREIGHTER, insurable=True)
        payload = _insurance_status(ship, spec)
        assert payload["insurable"] is True
        assert payload["tiers"][0]["purchasable"] is True  # BASIC, no current policy

    def test_insurable_false_on_an_arbitrary_type_is_registry_driven(self):
        """Registry-driven proof: SCOUT_SHIP is NOT in the canon non-insurable
        pair (Warp Jumper / Escape Pod). Flipping its fixture spec.insurable
        to False and observing the identical zeroed-out tier list as WJ/Pod
        proves _insurance_status reads the DB column, not a hardcoded
        ShipType set."""
        ship = _ship(ship_type=ShipType.SCOUT_SHIP, purchase_value=30_000)
        spec = ShipSpecification(type=ShipType.SCOUT_SHIP, insurable=False)
        payload = _insurance_status(ship, spec)
        assert payload["insurable"] is False
        assert all(t["purchasable"] is False for t in payload["tiers"])
        assert all(t["upgrade_cost"] is None for t in payload["tiers"])

    def test_missing_spec_fails_closed(self):
        """A mis-seeded / absent spec row must never silently default to
        insurable -- fail closed."""
        ship = _ship(ship_type=ShipType.LIGHT_FREIGHTER, purchase_value=80_000)
        payload = _insurance_status(ship, None)
        assert payload["insurable"] is False


class TestPremiumMathUnchanged:
    """Byte-pin ADR-0081 premium / ADR-0061 net-payout tables -- this WO
    touches only the insurability + friendly-port gates, never the pricing."""

    def test_premium_pct_table_unchanged(self):
        assert INSURANCE_PREMIUM_PCT == {"BASIC": 0.10, "STANDARD": 0.17, "PREMIUM": 0.22}

    def test_net_payout_pct_table_unchanged(self):
        assert INSURANCE_NET_PAYOUT_PCT == {"BASIC": 0.45, "STANDARD": 0.65, "PREMIUM": 0.75}

    def test_tier_order_unchanged(self):
        assert INSURANCE_TIER_ORDER == ["NONE", "BASIC", "STANDARD", "PREMIUM"]


# ---------------------------------------------------------------------------
# Tier 2: full purchase_ship_insurance / get_ship_insurance route execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestNonInsurableFlagGate:
    @pytest.mark.parametrize(
        "ship_type", [ShipType.WARP_JUMPER, ShipType.ESCAPE_POD, ShipType.SCOUT_SHIP]
    )
    async def test_insurable_false_rejects_regardless_of_ship_type(self, ship_type):
        """WARP_JUMPER / ESCAPE_POD carry insurable=False per the real seeder
        classification; SCOUT_SHIP does not (canon: a freely-insurable
        civilian hull). Flipping ITS fixture row to insurable=False and
        observing the identical 400 proves the route gate is 100%
        data-driven -- same code path, only the DB value changed."""
        player_id = uuid.uuid4()
        player = _player(id=player_id, current_port_id=uuid.uuid4())
        ship = _ship(id=uuid.uuid4(), owner_id=player_id, ship_type=ship_type, purchase_value=100_000)
        spec = ShipSpecification(type=ship_type, insurable=False)
        db = _FakeSession({
            Player: _FakeQuery(first=player),
            Ship: _FakeQuery(first=ship),
            ShipSpecification: _FakeQuery(first=spec),
        })

        with pytest.raises(HTTPException) as exc:
            await purchase_ship_insurance(
                ship_id=str(ship.id),
                request=InsurancePurchaseRequest(tier="BASIC"),
                player=player, db=db,
            )
        assert exc.value.status_code == 400
        assert "non-insurable" in exc.value.detail
        assert db.commit_calls == 0

    async def test_insurable_true_passes_the_flag_gate(self):
        """Complementary proof: insurable=True does NOT trip this gate --
        the route proceeds to the NEXT check (no purchase_value) instead,
        confirming the gate isn't accidentally rejecting everything."""
        player_id = uuid.uuid4()
        player = _player(id=player_id, current_port_id=uuid.uuid4())
        ship = _ship(id=uuid.uuid4(), owner_id=player_id, ship_type=ShipType.LIGHT_FREIGHTER, purchase_value=0)
        spec = ShipSpecification(type=ShipType.LIGHT_FREIGHTER, insurable=True)
        db = _FakeSession({
            Player: _FakeQuery(first=player),
            Ship: _FakeQuery(first=ship),
            ShipSpecification: _FakeQuery(first=spec),
        })

        with pytest.raises(HTTPException) as exc:
            await purchase_ship_insurance(
                ship_id=str(ship.id),
                request=InsurancePurchaseRequest(tier="BASIC"),
                player=player, db=db,
            )
        assert exc.value.status_code == 400
        assert "no insurable value" in exc.value.detail


@pytest.mark.asyncio
class TestFriendlyPortReputationGate:
    """ship-insurance.md:48: >= NEUTRAL reputation with the station's
    controlling faction, checked on BOTH the fresh-purchase (NONE -> tier)
    and upgrade (tier -> higher tier) paths -- same route, same gate."""

    def _rig(self, *, player_level_or_none, faction_affiliation="Terran Federation",
             current_policy=None):
        player_id = uuid.uuid4()
        station_id = uuid.uuid4()
        player = _player(id=player_id, current_port_id=station_id)
        ship = _ship(id=uuid.uuid4(), owner_id=player_id, ship_type=ShipType.LIGHT_FREIGHTER,
                     purchase_value=80_000, insurance=current_policy)
        spec = ShipSpecification(type=ShipType.LIGHT_FREIGHTER, insurable=True)
        station = _station(id=station_id, faction_affiliation=faction_affiliation)
        specs = {
            Player: _FakeQuery(first=player),
            Ship: _FakeQuery(first=ship),
            ShipSpecification: _FakeQuery(first=spec),
            Station: _FakeQuery(first=station),
        }
        if faction_affiliation is not None:
            faction = _faction(name=faction_affiliation)
            specs[Faction] = _FakeQuery(first=faction)
            if player_level_or_none is not None:
                rep = _reputation(player_id=player_id, faction_id=faction.id, level=player_level_or_none)
                specs[Reputation] = _FakeQuery(first=rep)
            else:
                specs[Reputation] = _FakeQuery(first=None)  # no Reputation row -> defaults NEUTRAL
        db = _FakeSession(specs)
        return player, ship, db

    async def test_below_neutral_rejects_fresh_purchase_naming_faction_and_standing(self):
        player, ship, db = self._rig(player_level_or_none=ReputationLevel.UNTRUSTWORTHY)

        with pytest.raises(HTTPException) as exc:
            await purchase_ship_insurance(
                ship_id=str(ship.id),
                request=InsurancePurchaseRequest(tier="BASIC"),
                player=player, db=db,
            )
        assert exc.value.status_code == 403
        assert "ERR_UNFRIENDLY_PORT" in exc.value.detail
        assert "Terran Federation" in exc.value.detail
        assert "UNTRUSTWORTHY" in exc.value.detail
        assert db.commit_calls == 0
        assert player.credits == 100_000  # untouched -- rejected before any spend

    async def test_below_neutral_rejects_upgrade_naming_faction_and_standing(self):
        player, ship, db = self._rig(
            player_level_or_none=ReputationLevel.UNTRUSTWORTHY,
            current_policy={"type": "BASIC", "mg_rep_awarded": ["BASIC"]},
        )

        with pytest.raises(HTTPException) as exc:
            await purchase_ship_insurance(
                ship_id=str(ship.id),
                request=InsurancePurchaseRequest(tier="STANDARD"),
                player=player, db=db,
            )
        assert exc.value.status_code == 403
        assert "ERR_UNFRIENDLY_PORT" in exc.value.detail
        assert "Terran Federation" in exc.value.detail
        assert db.commit_calls == 0
        assert ship.insurance == {"type": "BASIC", "mg_rep_awarded": ["BASIC"]}  # unchanged

    async def test_no_reputation_row_defaults_neutral_and_passes(self):
        player, ship, db = self._rig(player_level_or_none=None)

        with patch("src.api.routes.ship_upgrades.apply_emergent_action", new=Mock()) as mocked:
            result = await purchase_ship_insurance(
                ship_id=str(ship.id),
                request=InsurancePurchaseRequest(tier="BASIC"),
                player=player, db=db,
            )
        assert mocked.called
        assert result["current_tier"] == "BASIC"
        # BASIC premium 10% of 80,000 = 8,000 (ADR-0081) -- pinned here too.
        assert result["premium_paid"] == 8_000
        assert player.credits == 100_000 - 8_000
        assert db.commit_calls == 1

    async def test_above_neutral_passes_full_purchase_with_exact_premium(self):
        player, ship, db = self._rig(player_level_or_none=ReputationLevel.RESPECTED)

        with patch("src.api.routes.ship_upgrades.apply_emergent_action", new=Mock()):
            result = await purchase_ship_insurance(
                ship_id=str(ship.id),
                request=InsurancePurchaseRequest(tier="BASIC"),
                player=player, db=db,
            )
        assert result["premium_paid"] == 8_000
        assert result["credits_remaining"] == 100_000 - 8_000
        assert ship.insurance["type"] == "BASIC"
        assert db.commit_calls == 1

    async def test_factionless_station_passes_regardless_of_reputation(self):
        """NO-CANON: no controlling faction to be unfriendly with. A
        PUBLIC_ENEMY-tier player at an unaffiliated station still passes --
        and the fake session proves NO Faction/Reputation query ever fires
        (they're deliberately absent from the spec dict; an unexpected query
        would raise AssertionError)."""
        player, ship, db = self._rig(player_level_or_none=None, faction_affiliation=None)

        with patch("src.api.routes.ship_upgrades.apply_emergent_action", new=Mock()):
            result = await purchase_ship_insurance(
                ship_id=str(ship.id),
                request=InsurancePurchaseRequest(tier="BASIC"),
                player=player, db=db,
            )
        assert result["premium_paid"] == 8_000
        assert db.commit_calls == 1


@pytest.mark.asyncio
class TestGetInsuranceExposesInsurableField:
    async def test_get_response_exposes_insurable_false_for_non_insurable_hull(self):
        player_id = uuid.uuid4()
        player = _player(id=player_id)
        ship = _ship(id=uuid.uuid4(), owner_id=player_id, ship_type=ShipType.WARP_JUMPER,
                     purchase_value=1_000_000)
        spec = ShipSpecification(type=ShipType.WARP_JUMPER, insurable=False)
        db = _FakeSession({
            Ship: _FakeQuery(first=ship),
            ShipSpecification: _FakeQuery(first=spec),
        })

        result = await get_ship_insurance(ship_id=str(ship.id), player=player, db=db)
        assert result["insurable"] is False
        assert all(t["purchasable"] is False for t in result["tiers"])

    async def test_get_response_exposes_insurable_true_for_insurable_hull(self):
        player_id = uuid.uuid4()
        player = _player(id=player_id)
        ship = _ship(id=uuid.uuid4(), owner_id=player_id, ship_type=ShipType.LIGHT_FREIGHTER,
                     purchase_value=80_000)
        spec = ShipSpecification(type=ShipType.LIGHT_FREIGHTER, insurable=True)
        db = _FakeSession({
            Ship: _FakeQuery(first=ship),
            ShipSpecification: _FakeQuery(first=spec),
        })

        result = await get_ship_insurance(ship_id=str(ship.id), player=player, db=db)
        assert result["insurable"] is True


# ---------------------------------------------------------------------------
# Accept: migration is additive-only -- AST-pinned (mirrors
# test_formation_knowledge.py's TestMigrationAdditiveOnly convention).
# ---------------------------------------------------------------------------

_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "ba1e001a8e54_add_ship_specifications_insurable.py"
)


@pytest.mark.unit
class TestMigrationAdditiveOnly:
    def test_migration_file_exists(self) -> None:
        assert _MIGRATION_PATH.is_file()

    def test_upgrade_only_adds_column_and_backfills_two_rows(self) -> None:
        source = _MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        upgrade_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade")
        upgrade_src = ast.get_source_segment(source, upgrade_fn) or ""
        assert upgrade_src.count("op.add_column(") == 1
        assert upgrade_src.count("op.execute(") == 1
        assert "WARP_JUMPER" in upgrade_src and "ESCAPE_POD" in upgrade_src
        assert "nullable=False" in upgrade_src
        assert "server_default" in upgrade_src
        for banned in (
            "op.create_table(", "op.drop_column(", "op.drop_table(",
            "op.alter_column(", "op.create_index(", "op.drop_index(",
        ):
            assert banned not in upgrade_src

    def test_down_revision_is_the_current_head(self) -> None:
        source = _MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        assigns = {
            n.targets[0].id: n.value.value
            for n in tree.body
            if isinstance(n, ast.Assign)
            and isinstance(n.targets[0], ast.Name)
            and isinstance(n.value, ast.Constant)
        }
        assert assigns.get("down_revision") == "34d0fe6c1af1"
        assert assigns.get("revision") == "ba1e001a8e54"
