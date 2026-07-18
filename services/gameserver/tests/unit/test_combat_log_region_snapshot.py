"""Unit tests for WO-CMB-CLOG-SNAP-1 -- CombatLog.region_id_snapshot.

Region deletion previously orphaned combat audit rows: CombatLog carries a
sector_uuid FK (ondelete=SET NULL) but had no region column, despite
DATA_MODELS/combat.md:59 (ADR-0050 SK24) requiring a region snapshot
populated at row creation. This WO adds a plain (non-FK) UUID column and
wires it into every CombatLog(...) constructor site in combat_service.py via
one shared helper, ``_combat_log_region_snapshot(sector)``.

Sections:
  (1) TestRegionSnapshotHelper -- the shared helper in complete isolation:
      None-sector and None-region (unassigned sector) tolerance, and the
      happy path. DB-free, no ORM involved.
  (2) TestEveryConstructorSiteCarriesSnapshot -- AST source-pin over
      combat_service.py: counts every ``CombatLog(...)`` call site and
      asserts EACH carries a ``region_id_snapshot=`` keyword. This is the
      guard for a future 6th site (e.g. POLICE-OUTCOMES, not built at HEAD)
      -- adding one trips the count assertion until it's deliberately
      updated, and a kwarg-less site trips the per-site assertion.
  (3) TestMigrationIsAdditiveOnly -- AST/text pins on the new migration:
      correct down_revision chain, add_column only (no backfill/DDL beyond
      the one column), and no ForeignKey (the column must survive a region
      being deleted, which an FK would defeat).
  (4)-(6) Functional, end-to-end proofs on three of the five real entry
      points (attack_player / attack_npc_ship / attack_sector_drones) via a
      lightweight fake Session -- pattern: test_combat_loot_history_nh3b.py's
      ``_FakeCombatDb`` / ``_resolve_ship_combat`` monkeypatch idiom. The
      other two sites (attack_planet, attack_port) carry a much larger
      fixture surface (ownership transfer, capture rewards, chartered-event
      reputation hooks) to stand up for a single kwarg already proven
      structurally by section (2); they are covered by the AST pin, not
      duplicated here (WO's explicit "at minimum pin via source/AST" floor).
"""
from __future__ import annotations

import ast
import pathlib
import types
import uuid

import pytest

from src.models.combat import CombatResult
from src.models.combat_log import CombatLog
from src.models.npc_character import NPCCharacter as NPCCharacterModel
from src.models.player import Player as PlayerModel
from src.models.sector import Sector as SectorModel
from src.models.ship import Ship as ShipModel
from src.models.ship import ShipStatus, ShipType
from src.services import combat_service as combat_service_module
from src.services.combat_service import CombatService

_POSE = {
    "x_pct": 50.0, "y_pct": 50.0, "heading_deg": 0.0,
    "phase": "idle", "burning": False, "leg": None,
}


@pytest.fixture(autouse=True)
def _no_kia_processing(monkeypatch):
    """WO-API-A1: attack_npc_ship's own engage-range backstop needs a REAL
    NPCCharacter row now (see _FakeCombatDb.query below) -- stub out
    handle_npc_ship_destroyed's separate internal NPCCharacter lookup so its
    much larger Sector-lock/NPCDeathLog/squad machinery (out of THIS file's
    scope) never has to be stood up. Same isolation-boundary convention as
    test_combat_loot_history_nh3b.py's own identical fixture."""
    monkeypatch.setattr(
        "src.services.npc_spawn_service.handle_npc_ship_destroyed",
        lambda *a, **k: None,
    )

_SRC_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src"
_COMBAT_SERVICE_PATH = _SRC_ROOT / "services" / "combat_service.py"
_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "alembic" / "versions"
_MIGRATION_PATH = _MIGRATIONS_DIR / "2d61e3b17ddd_add_combat_logs_region_id_snapshot.py"


# =========================================================================
# (1) The shared helper, in isolation
# =========================================================================


class TestRegionSnapshotHelper:
    def test_none_sector_returns_none_without_raising(self):
        assert combat_service_module._combat_log_region_snapshot(None) is None

    def test_sector_with_region_returns_its_region_id(self):
        region_id = uuid.uuid4()
        sector = types.SimpleNamespace(region_id=region_id)
        assert combat_service_module._combat_log_region_snapshot(sector) == region_id

    def test_sector_without_region_returns_none(self):
        """A real Sector row that has never been assigned a region
        (region_id column is itself nullable) -- must not raise, must not
        fabricate a value."""
        sector = types.SimpleNamespace(region_id=None)
        assert combat_service_module._combat_log_region_snapshot(sector) is None


# =========================================================================
# (2) AST source-pin: every CombatLog(...) constructor site
# =========================================================================


class TestEveryConstructorSiteCarriesSnapshot:
    def test_exactly_six_combat_log_constructor_sites_at_head(self):
        """WO-CMB-CLOG-SNAP-1 audit found exactly 5 CombatLog(...) call
        sites in combat_service.py at HEAD (attack_player, attack_npc_ship,
        attack_sector_drones, attack_planet, attack_port). WO-CMB-NPC-
        INITIATED-1 (Max ruling, 2026-07-10) deliberately added a 6th --
        npc_attack_player, the symmetric NPC-initiated-attack mirror of
        attack_npc_ship -- and it carries region_id_snapshot= (see the
        test below), so this pin is bumped 5 -> 6 as its own reviewed
        change. A changed count means a site was added or removed --
        either way this must be a deliberate, reviewed change to this
        pin, not a silent drift."""
        tree = ast.parse(_COMBAT_SERVICE_PATH.read_text())
        call_sites = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "CombatLog"
        ]
        assert len(call_sites) == 6, (
            f"expected 6 CombatLog(...) constructor sites in combat_service.py, "
            f"found {len(call_sites)} -- a new site must carry "
            "region_id_snapshot= too; update this pin deliberately once it's "
            "covered."
        )

    def test_every_combat_log_constructor_site_carries_region_snapshot(self):
        tree = ast.parse(_COMBAT_SERVICE_PATH.read_text())
        call_sites = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "CombatLog"
        ]
        assert call_sites, "no CombatLog(...) constructor sites found -- pin is inert"
        for node in call_sites:
            kwarg_names = {kw.arg for kw in node.keywords if kw.arg is not None}
            assert "region_id_snapshot" in kwarg_names, (
                f"CombatLog(...) at combat_service.py:{node.lineno} is missing "
                "region_id_snapshot= (ADR-0050 SK24 requires every combat-log "
                "audit row to carry a region snapshot)"
            )

    def test_snapshot_kwarg_calls_the_shared_helper_not_a_reimplementation(self):
        """Every site should route through _combat_log_region_snapshot(sector)
        rather than a hand-rolled `sector.region_id if sector else None` at
        each call site -- keeps the None-sector/None-region tolerance in
        exactly one place."""
        tree = ast.parse(_COMBAT_SERVICE_PATH.read_text())
        call_sites = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "CombatLog"
        ]
        for node in call_sites:
            snapshot_kwargs = [kw for kw in node.keywords if kw.arg == "region_id_snapshot"]
            assert len(snapshot_kwargs) == 1
            value = snapshot_kwargs[0].value
            assert isinstance(value, ast.Call) and isinstance(value.func, ast.Name), (
                f"CombatLog(...) at combat_service.py:{node.lineno}: "
                "region_id_snapshot= should call the shared helper"
            )
            assert value.func.id == "_combat_log_region_snapshot"


# =========================================================================
# (3) Migration: additive-only, no FK, correct chain
# =========================================================================


class TestMigrationIsAdditiveOnly:
    def test_migration_file_exists(self):
        assert _MIGRATION_PATH.is_file(), f"expected migration at {_MIGRATION_PATH}"

    def test_revision_chain(self):
        module_ns: dict = {}
        exec(compile(_MIGRATION_PATH.read_text(), str(_MIGRATION_PATH), "exec"), module_ns)
        assert module_ns["revision"] == "2d61e3b17ddd"
        assert module_ns["down_revision"] == "ba1e001a8e54"
        assert module_ns["branch_labels"] is None
        assert module_ns["depends_on"] is None

    def test_upgrade_adds_exactly_one_nullable_column_no_foreign_key(self):
        source = _MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        upgrade_fn = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
        )
        add_column_calls = [
            node for node in ast.walk(upgrade_fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_column"
        ]
        assert len(add_column_calls) == 1, "upgrade() must add exactly one column"

        # No backfill / data-mutating statements (additive-only, per the WO's
        # explicit "no backfill" constraint) -- upgrade() must not call
        # op.execute at all.
        execute_calls = [
            node for node in ast.walk(upgrade_fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
        ]
        assert not execute_calls, "upgrade() must not backfill existing rows"

        # No ForeignKey CALL anywhere in the migration -- the whole point of
        # this column is to survive the region row it snapshots being
        # deleted. AST-structural, not a raw text search: the module
        # docstring itself explains (in prose) why there's no FK, so a naive
        # substring check over the whole file would false-fail on its own
        # explanation.
        foreign_key_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "ForeignKey")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "ForeignKey")
            )
        ]
        assert not foreign_key_calls, "migration must not add a ForeignKey"

        # The added column must be named region_id_snapshot and nullable.
        column_call = next(
            node for node in ast.walk(upgrade_fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "Column"
        )
        column_name = column_call.args[0].value
        assert column_name == "region_id_snapshot"
        nullable_kwarg = next(
            kw for kw in column_call.keywords if kw.arg == "nullable"
        )
        assert nullable_kwarg.value.value is True

    def test_downgrade_drops_the_same_column(self):
        source = _MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        downgrade_fn = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
        )
        drop_column_calls = [
            node for node in ast.walk(downgrade_fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "drop_column"
        ]
        assert len(drop_column_calls) == 1
        args = drop_column_calls[0].args
        assert args[0].value == "combat_logs"
        assert args[1].value == "region_id_snapshot"

    def test_model_column_matches_migration(self):
        """The ORM column declaration (src/models/combat_log.py) has no
        ForeignKey and is nullable, matching the migration."""
        source = inspect_source_of_combat_log_model()
        assert "region_id_snapshot" in source
        # crude but sufficient: the declaration line itself must not
        # reference ForeignKey.
        for line in source.splitlines():
            if "region_id_snapshot = Column" in line:
                assert "ForeignKey" not in line
                assert "nullable=True" in line
                break
        else:
            pytest.fail("region_id_snapshot Column declaration not found")


def inspect_source_of_combat_log_model() -> str:
    path = _SRC_ROOT / "models" / "combat_log.py"
    return path.read_text()


# =========================================================================
# Shared functional fixtures (pattern: test_combat_loot_history_nh3b.py)
# =========================================================================


def _cargo(capacity, used, contents):
    return {"capacity": capacity, "used": used, "contents": dict(contents)}


def _make_ship(*, cargo=None, type_=ShipType.SCOUT_SHIP, sector_id=1, name="Test Hull"):
    ship = ShipModel()
    ship.id = uuid.uuid4()
    ship.type = type_
    ship.name = name
    ship.cargo = cargo if cargo is not None else _cargo(50, 0, {})
    ship.is_destroyed = False
    ship.is_active = True
    ship.is_npc = False
    ship.current_value = 0
    ship.hangar = None
    ship.tow_state = None
    ship.sector_id = sector_id
    ship.status = ShipStatus.IN_SPACE
    ship.attack_drones = 0
    return ship


def _make_player(*, ship, personal_reputation=0, turns=999_999, max_turns=1_000, attack_drones=0):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        username="pilot",
        credits=0,
        turns=turns,
        max_turns=max_turns,
        last_turn_regeneration=None,
        lifetime_turns_spent=0,
        current_ship=ship,
        current_ship_id=ship.id,
        current_sector_id=1,
        is_docked=False,
        is_landed=False,
        current_port_id=None,
        attack_drones=attack_drones,
        defense_drones=0,
        military_rank="__no_such_rank__",  # forces try/except-guarded fallbacks
        personal_reputation=personal_reputation,
        quantum_shards=0,
        quantum_crystals=0,
        aria_total_interactions=0,
        aria_consciousness_level=1,
        aria_bonus_multiplier=1.0,
        grey_until=None,
        grey_kind=None,
        settings={},
        team_id=None,
        # SUSPECT-LIFE-1 (post-dates this file): attack_player now reads
        # these unconditionally via suspect_service.is_live_suspect for the
        # fed-zone-immunity check. Real Player rows always carry them
        # (migration-backed, default False/NULL); this fake needs the same
        # completeness or attack_player raises AttributeError.
        is_suspect=False,
        suspect_until=None,
        # WO-API-A1 (post-dates this file too): attack_player/attack_npc_ship
        # now backstop on engage-range -- see module-level _POSE.
        intrasystem_pose=dict(_POSE),
    )


def _sector(*, sector_id=1, region_id=None):
    return types.SimpleNamespace(
        id=uuid.uuid4(), sector_id=sector_id, cluster=None, last_combat=None,
        region_id=region_id,
    )


def _victory_result(*, cargo_stolen=None, rounds=1):
    return {
        "result": CombatResult.ATTACKER_VICTORY,
        "message": "attacker wins",
        "rounds": rounds,
        "attacker_drones_lost": 0,
        "defender_drones_lost": 0,
        "attacker_damage_dealt": 10,
        "defender_damage_dealt": 0,
        "attacker_ship_destroyed": False,
        "defender_ship_destroyed": True,
        "cargo_stolen": dict(cargo_stolen or {}),
        "combat_details": [],
    }


class _PlayerQueryStub:
    def __init__(self, players_by_id):
        self._players = players_by_id
        self._pending_id = None

    def filter(self, cond):
        rhs = getattr(cond, "right", None)
        self._pending_id = getattr(rhs, "value", None)
        return self

    def populate_existing(self, *a, **k):
        # WO-MONEY-REREAD-SERVICES: no-op passthrough, matches real
        # SQLAlchemy Query's chainable-and-returns-self shape.
        return self

    def with_for_update(self, *a, **k):
        return self

    def first(self):
        return self._players.get(self._pending_id)


class _StubQuery:
    def __init__(self, first=None, all_=None):
        self._first = first
        self._all = all_ if all_ is not None else []

    def filter(self, *a, **k):
        return self

    def with_for_update(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def populate_existing(self, *a, **k):
        return self

    def first(self):
        return self._first

    def all(self):
        return self._all

    def scalar(self):
        # WO-COMBAT-FRIENDLY-FIRE: attack_player's pre-lock guard does
        # db.query(Player.team_id).filter(...).scalar() -- a column-only
        # scalar read that falls through _FakeCombatDb.query()'s
        # model-is-PlayerModel branch (it's Player.team_id, not Player) to
        # this catch-all. Every player fixture in this file is genuinely
        # teamless (team_id=None, see _make_player above), so None here is
        # faithful -- not a guess. Every other real call shape in this file
        # (Ship/Sector/Drone) never calls .scalar(), so this is inert for
        # them.
        return None


class _FakeCombatDb:
    """Minimal synchronous Session double: routes .query(Model) by class,
    records every .add()ed row, no-ops flush/begin_nested/commit."""

    def __init__(self, *, players, ship_first=None, sector=None, drones=None, npc_char=None):
        self._players = {p.id: p for p in players}
        self._ship_first = ship_first
        self._sector = sector
        self._drones = drones or []
        self._npc_char = npc_char
        self.added = []
        self.commits = 0

    def query(self, model):
        if model is PlayerModel:
            return _PlayerQueryStub(self._players)
        if model is ShipModel:
            return _StubQuery(first=self._ship_first, all_=[])
        if model is SectorModel:
            return _StubQuery(first=self._sector, all_=[])
        if model is combat_service_module.Drone:
            return _StubQuery(first=None, all_=self._drones)
        if model is NPCCharacterModel:
            return _StubQuery(first=self._npc_char, all_=[])
        return _StubQuery(first=None, all_=[])

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _combat_logs(db):
    return [o for o in db.added if isinstance(o, CombatLog)]


# =========================================================================
# (4) attack_player (PvP leg)
# =========================================================================


class TestPvpLegPersistsRegionSnapshot:
    def test_pvp_combat_log_carries_the_sector_region_id(self, monkeypatch):
        region_id = uuid.uuid4()
        sector = _sector(region_id=region_id)
        attacker = _make_player(ship=_make_ship())
        defender = _make_player(ship=_make_ship())

        db = _FakeCombatDb(players=[attacker, defender], sector=sector)
        cs = CombatService(db)
        monkeypatch.setattr(cs, "_resolve_ship_combat", lambda *a, **k: _victory_result())
        monkeypatch.setattr(cs, "_handle_ship_destruction", lambda *a, **k: None)

        result = cs.attack_player(attacker_id=attacker.id, defender_id=defender.id)

        assert result["success"] is True
        logs = _combat_logs(db)
        assert len(logs) == 1
        assert logs[0].region_id_snapshot == region_id
        assert db.commits == 1

    def test_pvp_combat_log_snapshot_is_none_for_an_unassigned_sector(self, monkeypatch):
        """A real sector that has never been assigned a region -- the
        snapshot must be NULL, not raise, and combat must still succeed."""
        sector = _sector(region_id=None)
        attacker = _make_player(ship=_make_ship())
        defender = _make_player(ship=_make_ship())

        db = _FakeCombatDb(players=[attacker, defender], sector=sector)
        cs = CombatService(db)
        monkeypatch.setattr(cs, "_resolve_ship_combat", lambda *a, **k: _victory_result())
        monkeypatch.setattr(cs, "_handle_ship_destruction", lambda *a, **k: None)

        result = cs.attack_player(attacker_id=attacker.id, defender_id=defender.id)

        assert result["success"] is True
        logs = _combat_logs(db)
        assert len(logs) == 1
        assert logs[0].region_id_snapshot is None


# =========================================================================
# (5) attack_npc_ship (NPC leg)
# =========================================================================


class TestNpcLegPersistsRegionSnapshot:
    def test_npc_combat_log_carries_the_sector_region_id(self, monkeypatch):
        region_id = uuid.uuid4()
        sector = _sector(region_id=region_id)
        npc_ship = _make_ship(name="NPC Hull")
        npc_ship.sector = sector  # bypass the lazy relationship load on a transient row
        attacker = _make_player(ship=_make_ship())
        npc_char = types.SimpleNamespace(
            id=uuid.uuid4(), ship_id=npc_ship.id, current_sector_id=1,
            credits=0, archetype=None, title=None,
            intrasystem_pose=dict(_POSE),
        )

        db = _FakeCombatDb(players=[attacker], ship_first=npc_ship, sector=sector, npc_char=npc_char)
        cs = CombatService(db)
        monkeypatch.setattr(cs, "_resolve_ship_combat", lambda *a, **k: _victory_result())

        result = cs.attack_npc_ship(attacker_id=attacker.id, ship_id=npc_ship.id)

        assert result["success"] is True
        logs = _combat_logs(db)
        assert len(logs) == 1
        assert logs[0].region_id_snapshot == region_id


# =========================================================================
# (6) attack_sector_drones
# =========================================================================


def _drone_result():
    """Scripted _resolve_sector_drone_combat return -- no destroyed drones
    (keeps the DroneDeployment.update() branch and the reputation-award
    branch both un-exercised; those are out of THIS WO's scope and the fake
    Session's _StubQuery has no .update())."""
    return {
        "result": CombatResult.DRAW,
        "message": "drones repelled",
        "rounds": 1,
        "attacker_drones_lost": 0,
        "defender_drones_lost": 0,
        "attacker_damage_dealt": 5,
        "defender_damage_dealt": 5,
        "attacker_ship_destroyed": False,
        "destroyed_drone_ids": [],
        "winner_drone_id": None,
        "combat_details": [],
    }


class TestSectorDronesLegPersistsRegionSnapshot:
    def test_sector_drones_combat_log_carries_the_sector_region_id(self, monkeypatch):
        region_id = uuid.uuid4()
        sector = _sector(sector_id=7, region_id=region_id)
        attacker = _make_player(ship=_make_ship(sector_id=7))
        attacker.current_sector_id = 7
        hostile_drone = types.SimpleNamespace(id=uuid.uuid4())

        db = _FakeCombatDb(players=[attacker], sector=sector, drones=[hostile_drone])
        cs = CombatService(db)
        monkeypatch.setattr(cs, "_resolve_sector_drone_combat", lambda *a, **k: _drone_result())

        result = cs.attack_sector_drones(attacker_id=attacker.id, sector_id=7)

        assert result["success"] is True
        logs = _combat_logs(db)
        assert len(logs) == 1
        assert logs[0].region_id_snapshot == region_id
