"""Unit tests for WO-STN-SEC-1 (station-protection tier seeding).

``Station.security`` (FEATURES/economy/station-protection.md § Security
tiers) had ZERO writers anywhere in the codebase — every station read tier
"none" (the model's conservative unconfigured-default), so the live
``combat_service.py`` Guarantee #1 gate (``ERR_DOCKED_SHIP_PROTECTED`` at
``security_rank >= basic``) could never fire against ANY station, live or
historical.

Three layers, DB-free throughout:

1. ``_derive_station_security_tier`` — the pure tier-derivation rule shared
   by the worldgen seeder (``bang_import_service._apply_region``) and the
   backfill migration's SQL CASE expression, exercised directly.
2. The backfill migration (``b601fcdaca25_backfill_station_security_tier``)
   — AST/source pins: data-only, WHERE-guarded (idempotent), correctly
   chained onto the current head, documented reversible downgrade.
3. The INTEGRATION FALSIFIER — drives ``CombatService.attack_player``
   through a REAL ``Station`` ORM instance (its ``security_level`` /
   ``security_rank`` properties run for real, no mocking) via the
   fake-session combat-fixture idiom (test_combat_loot_history_nh3b.py):
   proves a standard/basic-tier docked defender is now actually protected,
   and a none-tier docked defender is not (negative control) — the
   behavioral change this WO exists to unlock. combat_service.py itself is
   untouched (read-only for this WO).
"""
from __future__ import annotations

import ast
import importlib.util
import types
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from src.models.cluster import ClusterType
from src.models.player import Player as PlayerModel
from src.models.ship import Ship as ShipModel
from src.models.ship import ShipStatus, ShipType
from src.models.station import Station, StationClass
from src.services.bang_import_service import _derive_station_security_tier
from src.services.combat_service import CombatService

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions"
MIGRATION_PATH = MIGRATIONS_DIR / "b601fcdaca25_backfill_station_security_tier.py"


# ---------------------------------------------------------------------------
# (1) Pure tier-derivation rule
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOperatorAnchors:
    """The three literal canon anchors: Federation Capital station (Terran
    Space CLASS_0), Nexus Starport Prime (Central Nexus CLASS_0), and
    "Terran Space hub stations" (SpaceDock / Tier-A TradeDock)."""

    def test_terran_space_class_0_is_standard(self) -> None:
        assert (
            _derive_station_security_tier(
                region_type="terran_space",
                cluster_type=ClusterType.STANDARD,
                station_class=StationClass.CLASS_0,
                is_spacedock=False,
                tradedock_tier=None,
            )
            == "standard"
        )

    def test_central_nexus_class_0_is_premium(self) -> None:
        assert (
            _derive_station_security_tier(
                region_type="central_nexus",
                cluster_type=ClusterType.STANDARD,
                station_class=StationClass.CLASS_0,
                is_spacedock=False,
                tradedock_tier=None,
            )
            == "premium"
        )

    @pytest.mark.parametrize("region_type", ["terran_space", "central_nexus"])
    def test_spacedock_hub_in_operator_region_is_standard(self, region_type: str) -> None:
        assert (
            _derive_station_security_tier(
                region_type=region_type,
                cluster_type=ClusterType.STANDARD,
                station_class=StationClass.CLASS_11,
                is_spacedock=True,
                tradedock_tier=None,
            )
            == "standard"
        )

    @pytest.mark.parametrize("region_type", ["terran_space", "central_nexus"])
    def test_tier_a_tradedock_in_operator_region_is_standard(self, region_type: str) -> None:
        assert (
            _derive_station_security_tier(
                region_type=region_type,
                cluster_type=ClusterType.STANDARD,
                station_class=StationClass.CLASS_11,
                is_spacedock=False,
                tradedock_tier="A",
            )
            == "standard"
        )

    @pytest.mark.parametrize("region_type", ["terran_space", "central_nexus"])
    def test_tier_b_tradedock_in_operator_region_is_not_an_anchor(self, region_type: str) -> None:
        """Only Tier-A TradeDocks are "hub stations" -- Tier-B falls through
        to the ordinary NO-CANON default like any other non-anchor port."""
        assert (
            _derive_station_security_tier(
                region_type=region_type,
                cluster_type=ClusterType.STANDARD,
                station_class=StationClass.CLASS_11,
                is_spacedock=False,
                tradedock_tier="B",
            )
            == "basic"
        )


@pytest.mark.unit
class TestLawlessClusters:
    """Frontier/lawless CLUSTERS ("frontier outposts...lawless ports") seed
    "none" -- checked in every region type, anchors excepted."""

    @pytest.mark.parametrize("cluster_type", [ClusterType.FRONTIER_OUTPOST, ClusterType.CONTESTED])
    @pytest.mark.parametrize("region_type", ["terran_space", "central_nexus", "player_owned"])
    def test_lawless_cluster_type_is_none(self, region_type: str, cluster_type: ClusterType) -> None:
        assert (
            _derive_station_security_tier(
                region_type=region_type,
                cluster_type=cluster_type,
                station_class=StationClass.CLASS_5,
                is_spacedock=False,
                tradedock_tier=None,
            )
            == "none"
        )

    def test_lawless_cluster_never_downgrades_an_operator_anchor(self) -> None:
        """An anchor check wins even if the anchor's cluster somehow rolled
        FRONTIER_OUTPOST/CONTESTED -- the anchor branches return before the
        cluster-type check ever runs."""
        assert (
            _derive_station_security_tier(
                region_type="central_nexus",
                cluster_type=ClusterType.FRONTIER_OUTPOST,
                station_class=StationClass.CLASS_0,
                is_spacedock=False,
                tradedock_tier=None,
            )
            == "premium"
        )

    def test_unknown_cluster_type_none_value_falls_through_to_basic(self) -> None:
        assert (
            _derive_station_security_tier(
                region_type="player_owned",
                cluster_type=None,
                station_class=StationClass.CLASS_5,
                is_spacedock=False,
                tradedock_tier=None,
            )
            == "basic"
        )


@pytest.mark.unit
class TestOrdinaryPortDefault:
    """NO-CANON WO-STN-SEC-1 default: every ordinary CLASS_1-11 NPC port (in
    ANY region, not just player-owned) that isn't a named anchor or in a
    lawless cluster gets a uniform "basic" floor."""

    @pytest.mark.parametrize("station_class", list(StationClass))
    @pytest.mark.parametrize("region_type", ["terran_space", "central_nexus", "player_owned"])
    def test_non_anchor_non_lawless_port_defaults_to_basic(
        self, region_type: str, station_class: StationClass
    ) -> None:
        # CLASS_0 in an operator-managed region IS the anchor -- skip that
        # combination here, it's covered by TestOperatorAnchors.
        if region_type in ("terran_space", "central_nexus") and station_class == StationClass.CLASS_0:
            pytest.skip("covered by TestOperatorAnchors")
        assert (
            _derive_station_security_tier(
                region_type=region_type,
                cluster_type=ClusterType.STANDARD,
                station_class=station_class,
                is_spacedock=False,
                tradedock_tier=None,
            )
            == "basic"
        )

    def test_player_owned_class_0_capital_is_also_basic(self) -> None:
        """Canon only says operator-managed CLASS_0 anchors get elevated
        tiers; a player-owned region's own CLASS_0 "regional Capital" is
        player-ownable and gets the same "Player-owned...default to Basic"
        treatment as everything else in that region."""
        assert (
            _derive_station_security_tier(
                region_type="player_owned",
                cluster_type=ClusterType.STANDARD,
                station_class=StationClass.CLASS_0,
                is_spacedock=False,
                tradedock_tier=None,
            )
            == "basic"
        )

    def test_return_value_is_always_a_valid_rank_key(self) -> None:
        """Every branch must emit a string Station.security_tier_rank
        actually recognizes -- a typo'd tier would silently rank as "none"
        via the model's unknown-tier fallback, defeating the seed."""
        from src.models.station import SECURITY_TIER_RANK

        for region_type in ("terran_space", "central_nexus", "player_owned"):
            for cluster_type in (
                None,
                ClusterType.STANDARD,
                ClusterType.FRONTIER_OUTPOST,
                ClusterType.CONTESTED,
            ):
                for station_class in StationClass:
                    for is_spacedock in (True, False):
                        for tradedock_tier in (None, "A", "B"):
                            tier = _derive_station_security_tier(
                                region_type=region_type,
                                cluster_type=cluster_type,
                                station_class=station_class,
                                is_spacedock=is_spacedock,
                                tradedock_tier=tradedock_tier,
                            )
                            assert tier in SECURITY_TIER_RANK


# ---------------------------------------------------------------------------
# (1b) Wiring pin: _apply_region writes station_kwargs["security"] as a
# dict-shaped {"tier": ...} value for every freshly-constructed Station, not
# a bare string/None -- and never needs flag_modified (fresh ORM
# constructions, not mutations of an already-flushed row).
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApplyRegionWiresSecurityDictShaped:
    def _apply_region_source(self) -> str:
        import inspect

        from src.services.bang_import_service import BangImportService

        return inspect.getsource(BangImportService._apply_region)

    def test_station_kwargs_security_is_dict_shaped(self) -> None:
        source = self._apply_region_source()
        assert 'station_kwargs["security"] = {' in source
        assert '"tier": _derive_station_security_tier(' in source

    def test_no_flag_modified_call_near_the_security_write(self) -> None:
        """Every Station(**station_kwargs) construction in this loop is a
        fresh, unflushed object -- flag_modified is for mutating an
        already-persisted JSONB column in place, which never happens here.
        Scoped to the station-creation loop specifically: _apply_region also
        writes planets/formations elsewhere in the same method and those
        DO use flag_modified for unrelated reasons, so a whole-function
        check would false-fail."""
        source = self._apply_region_source()
        loop_start = source.index("for stsp in region_plan.stations:")
        loop_end = source.index("for ps in region_plan.planets:")
        station_loop_source = source[loop_start:loop_end]
        assert '"security"' in station_loop_source  # sanity: sliced the right block
        # Check for the CALL, not the bare word -- the loop's own comments
        # explain *why* flag_modified isn't needed here, so a bare-word
        # substring check trips on the comment itself (self-defeating).
        assert "flag_modified(" not in station_loop_source


# ---------------------------------------------------------------------------
# (2) Backfill migration AST/source pins
# ---------------------------------------------------------------------------


def _load_migration_module() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(
        "b601fcdaca25_backfill_station_security_tier", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestBackfillMigration:
    def test_file_exists(self) -> None:
        assert MIGRATION_PATH.is_file(), f"expected migration at {MIGRATION_PATH}"

    def test_chained_onto_current_head(self) -> None:
        module = _load_migration_module()
        assert module.revision == "b601fcdaca25"
        assert module.down_revision == "2d61e3b17ddd"

    def test_upgrade_is_where_guarded_and_idempotent(self) -> None:
        module = _load_migration_module()
        import inspect

        source = inspect.getsource(module.upgrade)
        assert "security IS NULL" in source
        assert "UPDATE stations" in source

    def test_upgrade_is_data_only_no_ddl(self) -> None:
        """A DATA-ONLY migration must never CREATE/ALTER/DROP TABLE -- that's
        schema work and belongs to a prior/future schema migration, not this
        one-shot backfill."""
        module = _load_migration_module()
        import inspect

        source = inspect.getsource(module.upgrade).upper()
        for forbidden in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE", "CREATE INDEX", "DROP COLUMN", "ADD COLUMN"):
            assert forbidden not in source, f"upgrade() contains DDL ({forbidden}) -- not data-only"

    def test_downgrade_is_documented_and_reversible_not_a_bare_pass(self) -> None:
        module = _load_migration_module()
        import inspect

        source = inspect.getsource(module.downgrade)
        tree = ast.parse(source)
        func_def = tree.body[0]
        assert isinstance(func_def, ast.FunctionDef)
        body_without_docstring = [
            n for n in func_def.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))
        ]
        assert body_without_docstring, "downgrade() must not be a bare no-op pass"
        assert not (len(body_without_docstring) == 1 and isinstance(body_without_docstring[0], ast.Pass))
        assert "security = NULL" in source or "security=NULL" in source
        # Documented in the module docstring, not just the function body.
        module_source = MIGRATION_PATH.read_text()
        assert "blunt" in module_source.lower() or "documented" in module_source.lower()

    def test_upgrade_derivation_matches_the_python_helper_branches(self) -> None:
        """Sanity cross-check: the SQL CASE literal tier strings must be
        exactly the same 4 values the Python helper can emit (typo-guard
        against the two implementations drifting apart)."""
        module = _load_migration_module()
        import inspect

        source = inspect.getsource(module.upgrade)
        for tier in ("premium", "standard", "none", "basic"):
            assert f"'{tier}'" in source


# ---------------------------------------------------------------------------
# (3) Integration falsifier -- real Station ORM properties, fake Session
# ---------------------------------------------------------------------------


def _make_ship(*, sector_id: int = 1) -> ShipModel:
    ship = ShipModel()
    ship.id = uuid.uuid4()
    ship.type = ShipType.SCOUT_SHIP
    ship.status = ShipStatus.IN_SPACE
    ship.is_destroyed = False
    ship.is_active = True
    ship.sector_id = sector_id
    return ship


def _make_player(
    *, ship: ShipModel, sector_id: int = 1, is_docked: bool = False, current_port_id: Optional[uuid.UUID] = None
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        current_ship=ship,
        current_ship_id=ship.id,
        current_sector_id=sector_id,
        is_docked=is_docked,
        is_landed=False,
        current_port_id=current_port_id,
    )


def _make_station(tier: Optional[str]) -> Station:
    station = Station()
    station.id = uuid.uuid4()
    station.security = {"tier": tier} if tier is not None else None
    return station


class _AttackQueryStub:
    """Routes Player.id == <literal> to the matching seeded row; every other
    model returns a canned first()/all() answer (pattern:
    test_combat_loot_history_nh3b.py's _PlayerQueryStub / _StubQuery)."""

    def __init__(self, *, players_by_id: Dict[Any, Any] = None, first: Any = None, all_: Any = None):
        self._players = players_by_id
        self._first = first
        self._all = all_ if all_ is not None else []
        self._pending_id = None

    def filter(self, *args, **kwargs):
        if self._players is not None and args:
            cond = args[0]
            rhs = getattr(cond, "right", None)
            self._pending_id = getattr(rhs, "value", None)
        return self

    def order_by(self, *a, **k):
        return self

    def populate_existing(self, *a, **k):
        return self

    def with_for_update(self, *a, **k):
        return self

    def first(self):
        if self._players is not None:
            return self._players.get(self._pending_id)
        return self._first

    def all(self):
        return self._all


class _FakeAttackDb:
    """Minimal synchronous Session double covering exactly the queries
    CombatService.attack_player issues before/at the station-protection
    gate: Player (by id), Ship (lock-only, .all() unused), Station (by id,
    resolves the docked defender's station)."""

    def __init__(self, *, players, station: Optional[Station] = None):
        self._players = {p.id: p for p in players}
        self._station = station

    def query(self, model):
        if model is PlayerModel:
            return _AttackQueryStub(players_by_id=self._players)
        if model is ShipModel:
            return _AttackQueryStub(first=None, all_=[])
        if model is Station:
            return _AttackQueryStub(first=self._station, all_=[])
        return _AttackQueryStub(first=None, all_=[])


@pytest.mark.unit
class TestCombatGateNowFiresOnSeededStations:
    """The behavioral unlock this WO exists to prove: before this WO EVERY
    station read tier "none" (ZERO writers), so this gate was permanently
    dead code. These tests exercise the REAL Station.security_level /
    security_rank properties against a REAL (unflushed) Station instance --
    no mocking of the model itself."""

    @pytest.mark.parametrize("tier", ["standard", "premium"])
    def test_docked_at_seeded_standard_or_premium_tier_rejects_attack(self, tier, monkeypatch) -> None:
        monkeypatch.setattr(
            "src.services.hangar_service.HangarService.is_ship_hangared", lambda self, ship_id: False
        )
        station = _make_station(tier)
        attacker = _make_player(ship=_make_ship(), sector_id=1)
        defender = _make_player(
            ship=_make_ship(), sector_id=1, is_docked=True, current_port_id=station.id
        )
        db = _FakeAttackDb(players=[attacker, defender], station=station)
        cs = CombatService(db)

        result = cs.attack_player(attacker_id=attacker.id, defender_id=defender.id)

        assert result["success"] is False
        assert result["message"] == "ERR_DOCKED_SHIP_PROTECTED"
        assert result["error"] == "ERR_DOCKED_SHIP_PROTECTED"

    def test_docked_at_seeded_basic_tier_also_rejects_attack(self, monkeypatch) -> None:
        """Canon's protected threshold is security_rank >= basic (the
        LOWEST protected tier), not just standard/premium."""
        monkeypatch.setattr(
            "src.services.hangar_service.HangarService.is_ship_hangared", lambda self, ship_id: False
        )
        station = _make_station("basic")
        attacker = _make_player(ship=_make_ship(), sector_id=1)
        defender = _make_player(
            ship=_make_ship(), sector_id=1, is_docked=True, current_port_id=station.id
        )
        db = _FakeAttackDb(players=[attacker, defender], station=station)
        cs = CombatService(db)

        result = cs.attack_player(attacker_id=attacker.id, defender_id=defender.id)

        assert result["message"] == "ERR_DOCKED_SHIP_PROTECTED"

    def test_docked_at_seeded_none_tier_station_proceeds_past_the_gate(self, monkeypatch) -> None:
        """Negative control: an explicitly-seeded tier="none" station grants
        no protection -- the attack must NOT be rejected with
        ERR_DOCKED_SHIP_PROTECTED. Attacker/defender are placed in different
        sectors so the very next check ("not in your sector") gives an
        unambiguous, distinct rejection reason -- proof the station-
        protection gate was passed through, not that combat fully resolved."""
        monkeypatch.setattr(
            "src.services.hangar_service.HangarService.is_ship_hangared", lambda self, ship_id: False
        )
        station = _make_station("none")
        attacker = _make_player(ship=_make_ship(), sector_id=1)
        defender = _make_player(
            ship=_make_ship(), sector_id=2, is_docked=True, current_port_id=station.id
        )
        db = _FakeAttackDb(players=[attacker, defender], station=station)
        cs = CombatService(db)

        result = cs.attack_player(attacker_id=attacker.id, defender_id=defender.id)

        assert result["success"] is False
        assert result["message"] != "ERR_DOCKED_SHIP_PROTECTED"
        assert result["message"] == "Target is not in your sector"

    def test_unconfigured_security_null_station_also_proceeds_past_the_gate(self, monkeypatch) -> None:
        """Pre-WO-STN-SEC-1 legacy rows (security IS NULL, not backfilled)
        must keep behaving exactly as before -- this WO is additive, the
        conservative NULL-reads-as-none default is untouched."""
        monkeypatch.setattr(
            "src.services.hangar_service.HangarService.is_ship_hangared", lambda self, ship_id: False
        )
        station = _make_station(None)
        attacker = _make_player(ship=_make_ship(), sector_id=1)
        defender = _make_player(
            ship=_make_ship(), sector_id=2, is_docked=True, current_port_id=station.id
        )
        db = _FakeAttackDb(players=[attacker, defender], station=station)
        cs = CombatService(db)

        result = cs.attack_player(attacker_id=attacker.id, defender_id=defender.id)

        assert result["message"] == "Target is not in your sector"

    def test_defender_not_docked_skips_the_gate_entirely(self, monkeypatch) -> None:
        """is_docked=False must never even resolve current_port_id -- an
        undocked defender is not protected by a station's tier regardless of
        how strong it is. Different sectors (the "not in your sector" trick
        used throughout this class) keeps the fixture minimal -- proceeding
        further into full combat resolution needs a much heavier player
        fixture that's out of scope for this gate-only proof."""
        monkeypatch.setattr(
            "src.services.hangar_service.HangarService.is_ship_hangared", lambda self, ship_id: False
        )
        station = _make_station("premium")
        attacker = _make_player(ship=_make_ship(), sector_id=1)
        defender = _make_player(
            ship=_make_ship(), sector_id=2, is_docked=False, current_port_id=station.id
        )
        db = _FakeAttackDb(players=[attacker, defender], station=station)
        cs = CombatService(db)

        result = cs.attack_player(attacker_id=attacker.id, defender_id=defender.id)

        assert result["message"] != "ERR_DOCKED_SHIP_PROTECTED"
        assert result["message"] == "Target is not in your sector"
