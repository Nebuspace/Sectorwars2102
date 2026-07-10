"""WO-CMB-NPC-INITIATED-1 lane A — initiate_npc_combat (unit + one true
end-to-end test through the real resolution path).

Three layers:
  1. Guards/orchestration (npc_combat_initiation_service._guard_failure /
     initiate_npc_combat) — DB-free fake session (SimpleNamespace fixtures),
     CombatService.npc_attack_player mocked at the class boundary. Proves
     the guard stack (defender/NPC validity, docked-or-landed sanctuary,
     the Terran-Space law-enforcement bypass amendment, result-dict
     enrichment) independent of combat math.
  2. CombatService.npc_attack_player's own guards — same fake-session
     style, pinning its defense-in-depth checks directly (a caller other
     than initiate_npc_combat could invoke this public method too).
  3. build_npc_combat_initiated_event / emit_npc_combat_initiated — the
     pure payload builder and the live-context WS push built on top of it.
  4. ONE true end-to-end test — real (never-flushed) Ship ORM instances +
     SimpleNamespace Player/Sector + scripted `random`, calling all the way
     through initiate_npc_combat -> CombatService.npc_attack_player -> the
     REAL _resolve_ship_combat (not mocked). Mirrors the established
     test_combat_core_pins.py pattern: flag_modified() needs a real mapped
     Ship instance; Player/Sector are never flag_modified()'d so
     SimpleNamespace is sufficient.

Fixture-scoped assertions throughout — each test reads its own fixture,
never incidental state from a neighbor.
"""
from __future__ import annotations

import asyncio
import itertools
import uuid
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import AsyncMock, patch

import src.services.combat_service as combat_service_module
from src.models.combat import CombatResult
from src.models.npc_character import NPCArchetype
from src.models.region import RegionType
from src.models.ship import Ship as ShipModel
from src.models.ship import ShipType
from src.services import npc_combat_initiation_service as svc
from src.services.combat_service import CombatService

# ---------------------------------------------------------------------------
# Generic fake Session — decodes real db.query(Model).filter(Model.col == x)
# BinaryExpressions against in-memory rows, per
# .claude/agent-memory/monk/reference_fake_query_sqlalchemy_binexpr.md
# (worked example: test_pirate_ecosystem_foundation.py).
# ---------------------------------------------------------------------------


def _table_name(entity):
    tbl = getattr(entity, "__table__", None)
    if tbl is not None:
        return tbl.name
    cls = getattr(entity, "class_", None)
    if cls is not None:
        return cls.__table__.name
    tbl2 = getattr(entity, "table", None)
    if tbl2 is not None and hasattr(tbl2, "name"):
        return tbl2.name
    for child in entity.get_children():
        found = _table_name(child)
        if found:
            return found
    return None


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows
        self._criteria: List[Any] = []

    def filter(self, *criteria):
        self._criteria.extend(criteria)
        return self

    def _matches(self, row) -> bool:
        for cond in self._criteria:
            key = cond.left.key
            value = getattr(row, key, None)
            rhs = cond.right.value if hasattr(cond.right, "value") else cond.right
            opname = getattr(cond.operator, "__name__", None)
            if opname == "eq":
                if value != rhs:
                    return False
            else:
                raise NotImplementedError(f"fake query: unsupported operator {cond.operator!r}")
        return True

    def first(self):
        for row in self._rows:
            if self._matches(row):
                return row
        return None


class _FakeSession:
    def __init__(self, ships=None, stations=None, players=None, sectors=None):
        self._by_table = {
            "ships": list(ships or []),
            "stations": list(stations or []),
            "players": list(players or []),
            "sectors": list(sectors or []),
        }
        self.added: List[Any] = []
        self.flush_count = 0

    def query(self, *entities):
        name = _table_name(entities[0])
        return _FakeQuery(self._by_table.get(name, []))

    def add(self, obj):
        # Simulate the client-generated-UUID default every domain model in
        # this call path uses (CombatLog.id = Column(..., default=uuid4)),
        # which a real session only fires at INSERT — this fake has no real
        # INSERT, so fake it here for a realistic combat_log_id in results.
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self.added.append(obj)

    def flush(self):
        self.flush_count += 1


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

SECTOR_ID = 4242


def _sector(sector_id: int = SECTOR_ID, region_type=None):
    cluster = None
    if region_type is not None:
        cluster = SimpleNamespace(region=SimpleNamespace(region_type=region_type))
    return SimpleNamespace(id=uuid.uuid4(), sector_id=sector_id, cluster=cluster)


def _ship(*, destroyed=False, name="Interceptor", ship_type=ShipType.FAST_COURIER,
          sector_id=SECTOR_ID):
    return SimpleNamespace(
        id=uuid.uuid4(), is_destroyed=destroyed, name=name, type=ship_type,
        current_value=0, sector_id=sector_id,
    )


def _player(
    *,
    sector_id=SECTOR_ID,
    ship=None,
    is_docked=False,
    current_port_id=None,
    is_landed=False,
    defense_drones=0,
):
    ship = ship if ship is not None else _ship(name="Player Freighter")
    return SimpleNamespace(
        id=uuid.uuid4(),
        current_sector_id=sector_id,
        current_ship=ship,
        current_ship_id=ship.id if ship else None,
        is_docked=is_docked,
        current_port_id=current_port_id,
        is_landed=is_landed,
        user_id=uuid.uuid4(),
        username="TestPlayer",
        defense_drones=defense_drones,
        military_rank=None,
    )


def _npc(
    *,
    archetype=NPCArchetype.LAW_ENFORCEMENT,
    sector_id=SECTOR_ID,
    ship=None,
    display_name="Marshal Vance",
):
    ship = ship if ship is not None else _ship(name="Federation Marshal Interdictor")
    return SimpleNamespace(
        id=uuid.uuid4(),
        archetype=archetype,
        current_sector_id=sector_id,
        ship_id=ship.id,
        display_name=display_name,
    ), ship


def _station(*, security_rank=0):
    return SimpleNamespace(id=uuid.uuid4(), security_rank=security_rank)


def _combat_result(
    *,
    result=CombatResult.ATTACKER_VICTORY,
    attacker_ship_destroyed=False,
    defender_ship_destroyed=False,
    message="Combat resolved",
):
    return {
        "result": result,
        "message": message,
        "rounds": 3,
        "attacker_drones_lost": 0,
        "defender_drones_lost": 0,
        "attacker_damage_dealt": 10,
        "defender_damage_dealt": 5,
        "attacker_ship_destroyed": attacker_ship_destroyed,
        "defender_ship_destroyed": defender_ship_destroyed,
        "cargo_stolen": {},
        "combat_details": [{"round": 1, "message": "Combat Round 1"}],
    }


def _session_for(*, npc_ship=None, stations=(), defender_ship=None, defender=None, sector=None):
    ships = [s for s in (npc_ship, defender_ship) if s is not None]
    players = [defender] if defender is not None else []
    sectors = [sector] if sector is not None else []
    return _FakeSession(ships=ships, stations=list(stations), players=players, sectors=sectors)


# ---------------------------------------------------------------------------
# 1. Guards
# ---------------------------------------------------------------------------

class TestGuardFailure:
    def test_npc_with_no_ship_id_fails(self):
        npc, _ship_obj = _npc()
        npc.ship_id = None
        defender = _player()
        db = _session_for(defender_ship=defender.current_ship)
        npc_ship, failure = svc._guard_failure(db, npc, defender, _sector())
        assert npc_ship is None
        assert failure == {"success": False, "message": "NPC has no active ship"}

    def test_npc_ship_destroyed_fails(self):
        npc, npc_ship = _npc()
        npc_ship.is_destroyed = True
        defender = _player()
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        _, failure = svc._guard_failure(db, npc, defender, _sector())
        assert failure["message"] == "NPC has no active ship"

    def test_npc_wrong_sector_fails(self):
        npc, npc_ship = _npc(sector_id=9999)
        defender = _player()
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        _, failure = svc._guard_failure(db, npc, defender, _sector())
        assert failure["message"] == "NPC is not in the target sector"

    def test_defender_with_no_ship_fails(self):
        npc, npc_ship = _npc()
        defender = _player()
        defender.current_ship = None
        db = _session_for(npc_ship=npc_ship)
        _, failure = svc._guard_failure(db, npc, defender, _sector())
        assert failure["message"] == "Defender has no active ship"

    def test_defender_ship_destroyed_fails(self):
        npc, npc_ship = _npc()
        defender = _player()
        defender.current_ship.is_destroyed = True
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        _, failure = svc._guard_failure(db, npc, defender, _sector())
        assert failure["message"] == "Defender has no active ship"

    def test_defender_wrong_sector_fails(self):
        npc, npc_ship = _npc()
        defender = _player(sector_id=9999)
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        _, failure = svc._guard_failure(db, npc, defender, _sector())
        assert failure["message"] == "Target is not in your sector"

    def test_docked_at_protected_station_fails(self):
        npc, npc_ship = _npc()
        station = _station(security_rank=1)  # >= SECURITY_TIER_PROTECTED_MIN_RANK
        defender = _player(is_docked=True, current_port_id=station.id)
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship, stations=[station])
        _, failure = svc._guard_failure(db, npc, defender, _sector())
        assert failure["error"] == "ERR_DOCKED_SHIP_PROTECTED"

    def test_docked_at_unprotected_station_passes_this_guard(self):
        npc, npc_ship = _npc()
        station = _station(security_rank=-1)  # below the protected floor
        defender = _player(is_docked=True, current_port_id=station.id)
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship, stations=[station])
        npc_ship_out, failure = svc._guard_failure(db, npc, defender, _sector())
        assert failure is None
        assert npc_ship_out is npc_ship

    def test_landed_defender_fails(self):
        npc, npc_ship = _npc()
        defender = _player(is_landed=True)
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        _, failure = svc._guard_failure(db, npc, defender, _sector())
        assert failure["message"] == "Defender is landed and cannot be attacked"

    def test_all_guards_pass_returns_npc_ship_none_failure(self):
        npc, npc_ship = _npc()
        defender = _player()
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        npc_ship_out, failure = svc._guard_failure(db, npc, defender, _sector())
        assert failure is None
        assert npc_ship_out is npc_ship


# ---------------------------------------------------------------------------
# 2. Terran-Space law-enforcement bypass amendment
# ---------------------------------------------------------------------------

class TestTerranSpaceGateAmendment:
    def _run(self, *, archetype, region_type, mock_result):
        npc, npc_ship = _npc(archetype=archetype)
        defender = _player()
        sector = _sector(region_type=region_type)
        db = _session_for(
            npc_ship=npc_ship, defender_ship=defender.current_ship,
            defender=defender, sector=sector,
        )
        with patch.object(
            combat_service_module.CombatService, "npc_attack_player",
            return_value=mock_result,
        ) as mock_call:
            result = svc.initiate_npc_combat(db, npc, defender, sector, trigger="t")
        return result, mock_call

    def test_law_enforcement_bypasses_terran_space_block(self):
        result, mock_call = self._run(
            archetype=NPCArchetype.LAW_ENFORCEMENT, region_type=RegionType.TERRAN_SPACE,
            mock_result={"success": True, "npc_ship_id": str(uuid.uuid4())},
        )
        mock_call.assert_called_once()
        assert result["success"] is True

    def test_non_law_enforcement_blocked_in_terran_space(self):
        result, mock_call = self._run(
            archetype=NPCArchetype.HOSTILE_RAIDER, region_type=RegionType.TERRAN_SPACE,
            mock_result={"success": True},
        )
        mock_call.assert_not_called()
        assert result == {"success": False, "message": "Combat is not allowed in this sector"}

    def test_non_law_enforcement_allowed_outside_terran_space(self):
        result, mock_call = self._run(
            archetype=NPCArchetype.HOSTILE_RAIDER, region_type=RegionType.PLAYER_OWNED,
            mock_result={"success": True, "npc_ship_id": str(uuid.uuid4())},
        )
        mock_call.assert_called_once()
        assert result["success"] is True

    def test_no_region_type_never_blocks(self):
        result, mock_call = self._run(
            archetype=NPCArchetype.HOSTILE_RAIDER, region_type=None,
            mock_result={"success": True, "npc_ship_id": str(uuid.uuid4())},
        )
        mock_call.assert_called_once()
        assert result["success"] is True


# ---------------------------------------------------------------------------
# 3. Orchestration — resolution call-through + result enrichment
# ---------------------------------------------------------------------------

class TestOrchestration:
    def test_calls_the_public_npc_attack_player_method(self):
        npc, npc_ship = _npc()
        defender = _player()
        sector = _sector()
        db = _session_for(
            npc_ship=npc_ship, defender_ship=defender.current_ship,
            defender=defender, sector=sector,
        )
        with patch.object(
            combat_service_module.CombatService, "npc_attack_player",
            return_value={"success": True},
        ) as mock_call:
            svc.initiate_npc_combat(db, npc, defender, sector, trigger="t")
        mock_call.assert_called_once_with(npc_ship_id=npc_ship.id, defender_id=defender.id)

    def test_guard_failure_short_circuits_before_resolution(self):
        npc, npc_ship = _npc()
        npc_ship.is_destroyed = True
        defender = _player()
        sector = _sector()
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        with patch.object(
            combat_service_module.CombatService, "npc_attack_player",
        ) as mock_call:
            result = svc.initiate_npc_combat(db, npc, defender, sector, trigger="t")
        mock_call.assert_not_called()
        assert result["success"] is False

    def test_failed_resolution_is_returned_unenriched(self):
        npc, npc_ship = _npc()
        defender = _player()
        sector = _sector()
        db = _session_for(
            npc_ship=npc_ship, defender_ship=defender.current_ship,
            defender=defender, sector=sector,
        )
        raw = {"success": False, "message": "Target is not in your sector"}
        with patch.object(combat_service_module.CombatService, "npc_attack_player", return_value=raw):
            result = svc.initiate_npc_combat(db, npc, defender, sector, trigger="t")
        assert result == raw

    def test_successful_resolution_is_enriched(self):
        npc, npc_ship = _npc()
        defender = _player()
        sector = _sector()
        db = _session_for(
            npc_ship=npc_ship, defender_ship=defender.current_ship,
            defender=defender, sector=sector,
        )
        raw = {"success": True, "combat_log_id": str(uuid.uuid4())}
        with patch.object(combat_service_module.CombatService, "npc_attack_player", return_value=dict(raw)):
            result = svc.initiate_npc_combat(
                db, npc, defender, sector, trigger="pirate_aggression",
                trigger_context={"patrol_id": "abc"},
            )
        assert result["npc_id"] == str(npc.id)
        assert result["npc_display_name"] == npc.display_name
        assert result["trigger"] == "pirate_aggression"
        assert result["trigger_context"] == {"patrol_id": "abc"}

    def test_trigger_context_defaults_to_empty_dict(self):
        npc, npc_ship = _npc()
        defender = _player()
        sector = _sector()
        db = _session_for(
            npc_ship=npc_ship, defender_ship=defender.current_ship,
            defender=defender, sector=sector,
        )
        with patch.object(
            combat_service_module.CombatService, "npc_attack_player",
            return_value={"success": True},
        ):
            result = svc.initiate_npc_combat(db, npc, defender, sector, trigger="t")
        assert result["trigger_context"] == {}

    def test_never_raises_on_unexpected_error(self):
        npc, _npc_ship = _npc()
        defender = _player()
        sector = _sector()

        class _ExplodingSession:
            def query(self, *a, **k):
                raise RuntimeError("connection lost")

        result = svc.initiate_npc_combat(_ExplodingSession(), npc, defender, sector, trigger="t")
        assert result["success"] is False
        assert "unexpectedly" in result["message"]


# ---------------------------------------------------------------------------
# 4. CombatService.npc_attack_player's own defense-in-depth guards
# ---------------------------------------------------------------------------

class TestNpcAttackPlayerDirectGuards:
    def test_missing_npc_ship_fails(self):
        defender = _player()
        db = _session_for(defender_ship=defender.current_ship, defender=defender, sector=_sector())
        result = CombatService(db).npc_attack_player(
            npc_ship_id=uuid.uuid4(), defender_id=defender.id,
        )
        assert result == {"success": False, "message": "NPC has no active ship"}

    def test_destroyed_npc_ship_fails(self):
        npc_ship = _ship(destroyed=True)
        defender = _player()
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship, defender=defender, sector=_sector())
        result = CombatService(db).npc_attack_player(npc_ship_id=npc_ship.id, defender_id=defender.id)
        assert result == {"success": False, "message": "NPC has no active ship"}

    def test_missing_defender_fails(self):
        npc_ship = _ship()
        db = _session_for(npc_ship=npc_ship)
        result = CombatService(db).npc_attack_player(npc_ship_id=npc_ship.id, defender_id=uuid.uuid4())
        assert result == {"success": False, "message": "Defender has no active ship"}

    def test_sector_mismatch_fails(self):
        npc_ship = _ship(sector_id=1)
        defender = _player(sector_id=2)
        db = _session_for(
            npc_ship=npc_ship, defender_ship=defender.current_ship,
            defender=defender, sector=_sector(sector_id=2),
        )
        result = CombatService(db).npc_attack_player(npc_ship_id=npc_ship.id, defender_id=defender.id)
        assert result == {"success": False, "message": "Target is not in your sector"}


# ---------------------------------------------------------------------------
# 5. build_npc_combat_initiated_event / emit_npc_combat_initiated
# ---------------------------------------------------------------------------

class TestBuildAndEmitNpcCombatInitiated:
    def test_build_returns_the_full_contract_shape(self):
        npc, npc_ship = _npc()
        defender = _player()
        sector = _sector()
        combat_log_id = uuid.uuid4()
        event = svc.build_npc_combat_initiated_event(
            combat_log_id, npc, npc_ship, defender, sector, trigger="police_wanted_status",
        )
        assert event["type"] == "npc_combat_initiated"
        assert event["combat_id"] == str(combat_log_id)
        assert event["npc_id"] == str(npc.id)
        assert event["npc_display_name"] == npc.display_name
        assert event["npc_archetype"] == npc.archetype.value
        assert event["npc_ship_name"] == npc_ship.name
        assert event["npc_ship_type"] == npc_ship.type.name
        assert event["defender_id"] == str(defender.id)
        assert event["defender_name"] == defender.username
        assert event["sector_id"] == sector.sector_id
        assert event["trigger"] == "police_wanted_status"
        assert event["defender_user_id"] == str(defender.user_id)
        assert "timestamp" in event

    def test_emit_pushes_personal_and_sector_via_the_built_event(self):
        npc, npc_ship = _npc()
        defender = _player()
        sector = _sector()
        combat_log_id = uuid.uuid4()

        async def _run():
            with patch("src.services.websocket_service.connection_manager") as mock_cm:
                mock_cm.send_combat_update = AsyncMock()
                mock_cm.broadcast_to_sector = AsyncMock()
                svc.emit_npc_combat_initiated(
                    combat_log_id, npc, npc_ship, defender, sector, trigger="pirate_aggression",
                )
                await asyncio.sleep(0)  # let the created tasks run
                mock_cm.send_combat_update.assert_awaited_once()
                mock_cm.broadcast_to_sector.assert_awaited_once()

        asyncio.run(_run())

    def test_emit_never_raises_with_no_running_loop(self):
        npc, npc_ship = _npc()
        defender = _player()
        sector = _sector()
        svc.emit_npc_combat_initiated(
            uuid.uuid4(), npc, npc_ship, defender, sector, trigger="t",
        )  # called outside any event loop — must not raise


# ---------------------------------------------------------------------------
# 6. One true end-to-end test through the REAL resolver
# ---------------------------------------------------------------------------

class TestEndToEndRealResolution:
    def test_real_resolution_through_npc_attack_player(self):
        npc_ship = ShipModel(
            id=uuid.uuid4(), name="Federation Marshal Interdictor",
            type=ShipType.FAST_COURIER, is_destroyed=False, sector_id=SECTOR_ID,
            current_value=0,
        )
        defender_ship = ShipModel(
            id=uuid.uuid4(), name="Player Freighter",
            type=ShipType.LIGHT_FREIGHTER, is_destroyed=False, sector_id=SECTOR_ID,
            current_value=0,
        )
        npc, _ = _npc(ship=npc_ship)
        defender = _player(ship=defender_ship)
        sector = _sector()
        db = _session_for(
            npc_ship=npc_ship, defender_ship=defender_ship, defender=defender, sector=sector,
        )

        # hit_chance is capped at 0.8 — a constant 0.95 guarantees every
        # attack/counter-attack roll misses, so the fight runs to the
        # engine's own 10-round cap and ends in a DRAW rather than a ship
        # destruction. Deliberately avoids exercising ship_service.destroy_
        # ship's real DB-transactional internals (owner-relationship
        # lookups, tow/pioneer reabsorb SAVEPOINTs) here — those paths are
        # already covered by test_combat_core_pins.py; this test's own job
        # is proving the initiate_npc_combat -> npc_attack_player -> REAL
        # _resolve_ship_combat call chain doesn't blow up end-to-end.
        rolls = itertools.cycle([0.95])
        with patch("random.random", side_effect=lambda: next(rolls)):
            result = svc.initiate_npc_combat(db, npc, defender, sector, trigger="pirate_aggression")

        assert result["success"] is True
        assert result["trigger"] == "pirate_aggression"
        assert result["npc_id"] == str(npc.id)
        assert result["npc_ship_destroyed"] is False
        assert result["defender_ship_destroyed"] is False
        assert "combat_result" in result
