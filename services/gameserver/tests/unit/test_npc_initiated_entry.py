"""WO-CMB-NPC-INITIATED-1 lane A — initiate_npc_combat (DB-free unit tests).

Two layers, deliberately: guard logic (defender/NPC validity, docked-or-
landed sanctuary, the Terran-Space law-enforcement bypass amendment) is
exercised against a real DB-free fake session, since it's pure short-
circuit logic with no dependency on the combat engine. The RESOLUTION path
mocks CombatService._resolve_ship_combat / _handle_ship_destruction /
_spawn_cargo_wreck / handle_npc_ship_destroyed at the class/module level —
this module's job is to prove ITS OWN orchestration wiring (CombatLog
construction, which consequence fires for which combat_result, the
returned dict's shape) is correct, not to re-simulate the round-based
combat math _resolve_ship_combat already owns and which the existing
regression suite (test_combat_escape.py, test_combat_core_pins.py, etc.)
already covers against the same symmetric attacker_ship extension.

Fixture-scoped assertions throughout — each test reads its own fixture,
never incidental state from a neighbor.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import AsyncMock, patch

from src.models.combat import CombatResult
from src.models.npc_character import NPCArchetype
from src.models.region import RegionType
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
    def __init__(self, ships=None, stations=None):
        self._by_table = {
            "ships": list(ships or []),
            "stations": list(stations or []),
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


def _ship(*, destroyed=False, name="Interceptor", ship_type=ShipType.FAST_COURIER):
    return SimpleNamespace(
        id=uuid.uuid4(), is_destroyed=destroyed, name=name, type=ship_type,
        current_value=0,
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


def _session_for(*, npc_ship=None, stations=(), defender_ship=None):
    ships = [s for s in (npc_ship, defender_ship) if s is not None]
    return _FakeSession(ships=ships, stations=list(stations))


# ---------------------------------------------------------------------------
# NPC-side guards
# ---------------------------------------------------------------------------

class TestNpcGuards:
    def test_npc_with_no_ship_id_fails(self):
        npc, _ = _npc()
        npc.ship_id = None
        defender = _player()
        db = _session_for(defender_ship=defender.current_ship)
        result = svc.initiate_npc_combat(
            db, npc, defender, _sector(), trigger="pirate_aggression"
        )
        assert result["success"] is False
        assert "ship" in result["message"].lower()

    def test_npc_ship_destroyed_fails(self):
        npc, npc_ship = _npc(ship=_ship(destroyed=True))
        defender = _player()
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        result = svc.initiate_npc_combat(
            db, npc, defender, _sector(), trigger="pirate_aggression"
        )
        assert result["success"] is False

    def test_npc_not_in_target_sector_fails(self):
        npc, npc_ship = _npc(sector_id=999)
        defender = _player()
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        result = svc.initiate_npc_combat(
            db, npc, defender, _sector(), trigger="pirate_aggression"
        )
        assert result["success"] is False
        assert "sector" in result["message"].lower()


# ---------------------------------------------------------------------------
# Defender-side guards
# ---------------------------------------------------------------------------

class TestDefenderGuards:
    def test_defender_no_ship_fails(self):
        npc, npc_ship = _npc()
        defender = _player()
        defender.current_ship = None
        db = _session_for(npc_ship=npc_ship)
        result = svc.initiate_npc_combat(
            db, npc, defender, _sector(), trigger="wanted_status_engagement"
        )
        assert result["success"] is False

    def test_defender_destroyed_ship_fails(self):
        npc, npc_ship = _npc()
        defender = _player(ship=_ship(destroyed=True))
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        result = svc.initiate_npc_combat(
            db, npc, defender, _sector(), trigger="wanted_status_engagement"
        )
        assert result["success"] is False

    def test_defender_not_in_target_sector_fails(self):
        npc, npc_ship = _npc()
        defender = _player(sector_id=999)
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        result = svc.initiate_npc_combat(
            db, npc, defender, _sector(), trigger="wanted_status_engagement"
        )
        assert result["success"] is False

    def test_docked_at_protected_tier_station_fails(self):
        station = _station(security_rank=1)  # basic and above is protected
        npc, npc_ship = _npc()
        defender = _player(is_docked=True, current_port_id=station.id)
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship, stations=[station])
        result = svc.initiate_npc_combat(
            db, npc, defender, _sector(), trigger="wanted_status_engagement"
        )
        assert result["success"] is False
        assert result["message"] == "ERR_DOCKED_SHIP_PROTECTED"
        assert result["error"] == "ERR_DOCKED_SHIP_PROTECTED"

    def test_landed_defender_fails(self):
        npc, npc_ship = _npc()
        defender = _player(is_landed=True)
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        result = svc.initiate_npc_combat(
            db, npc, defender, _sector(), trigger="wanted_status_engagement"
        )
        assert result["success"] is False


# ---------------------------------------------------------------------------
# Terran-Space gate amendment — LAW_ENFORCEMENT bypasses; others don't.
# ---------------------------------------------------------------------------

class TestTerranSpaceGateAmendment:
    def _resolved(self, db, npc, defender, sector, trigger):
        """Runs initiate_npc_combat with the combat engine + the SQLAlchemy
        flag_modified() call (needs a real ORM-mapped instance, not our
        SimpleNamespace fakes) both stubbed out — this class only proves
        the gate's allow/block decision, not resolution mechanics."""
        with patch.object(CombatService, "_resolve_ship_combat", return_value=_combat_result()), \
             patch("src.services.npc_combat_initiation_service.flag_modified"):
            return svc.initiate_npc_combat(db, npc, defender, sector, trigger=trigger)

    def test_law_enforcement_bypasses_terran_space_block(self):
        npc, npc_ship = _npc(archetype=NPCArchetype.LAW_ENFORCEMENT)
        defender = _player()
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        sector = _sector(region_type=RegionType.TERRAN_SPACE)
        result = self._resolved(db, npc, defender, sector, "wanted_status_engagement")
        assert result["success"] is True

    def test_non_law_enforcement_blocked_in_terran_space(self):
        npc, npc_ship = _npc(archetype=NPCArchetype.HOSTILE_RAIDER)
        defender = _player()
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        sector = _sector(region_type=RegionType.TERRAN_SPACE)
        result = self._resolved(db, npc, defender, sector, "pirate_aggression")
        assert result["success"] is False
        assert result["message"] == "Combat is not allowed in this sector"

    def test_non_law_enforcement_allowed_outside_terran_space(self):
        npc, npc_ship = _npc(archetype=NPCArchetype.HOSTILE_RAIDER)
        defender = _player()
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        sector = _sector(region_type=RegionType.PLAYER_OWNED)
        result = self._resolved(db, npc, defender, sector, "pirate_aggression")
        assert result["success"] is True

    def test_unresolved_region_defaults_to_allowed(self):
        """No cluster/region chain resolves (region_type stays None) —
        mirrors combat_service._is_combat_allowed's own frontier-default
        behavior."""
        npc, npc_ship = _npc(archetype=NPCArchetype.HOSTILE_RAIDER)
        defender = _player()
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        result = self._resolved(db, npc, defender, _sector(), "pirate_aggression")
        assert result["success"] is True


# ---------------------------------------------------------------------------
# Resolution orchestration — CombatLog + consequence wiring, mocked at the
# CombatService boundary.
# ---------------------------------------------------------------------------

class TestResolutionOrchestration:
    def _run(self, combat_result, **extra_mocks):
        npc, npc_ship = _npc()
        defender = _player()
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        with patch.object(CombatService, "_resolve_ship_combat", return_value=combat_result), \
             patch.object(CombatService, "_handle_ship_destruction") as mock_destroy_defender, \
             patch.object(CombatService, "_spawn_cargo_wreck") as mock_wreck, \
             patch("src.services.npc_combat_initiation_service.flag_modified"), \
             patch(
                 "src.services.npc_spawn_service.handle_npc_ship_destroyed",
                 return_value=SimpleNamespace(id=uuid.uuid4()),
             ) as mock_kia:
            result = svc.initiate_npc_combat(
                db, npc, defender, _sector(), trigger="wanted_status_engagement",
                trigger_context={"engagement_id": "abc"},
            )
        return result, npc, npc_ship, defender, mock_destroy_defender, mock_wreck, mock_kia

    def test_neither_ship_destroyed_no_consequences_fire(self):
        result, *_rest, mock_destroy_defender, mock_wreck, mock_kia = self._run(
            _combat_result(result=CombatResult.DRAW)
        )
        assert result["success"] is True
        assert result["npc_ship_destroyed"] is False
        assert result["defender_ship_destroyed"] is False
        assert result["dead_npc"] is None
        mock_destroy_defender.assert_not_called()
        mock_wreck.assert_not_called()
        mock_kia.assert_not_called()

    def test_defender_destroyed_calls_handle_ship_destruction_only(self):
        result, npc, npc_ship, defender, mock_destroy_defender, mock_wreck, mock_kia = self._run(
            _combat_result(result=CombatResult.ATTACKER_VICTORY, defender_ship_destroyed=True)
        )
        assert result["defender_ship_destroyed"] is True
        assert result["npc_ship_destroyed"] is False
        mock_destroy_defender.assert_called_once_with(defender, None, "npc_combat")
        mock_wreck.assert_not_called()  # _handle_ship_destruction spawns its own wreck internally
        mock_kia.assert_not_called()

    def test_npc_destroyed_marks_ship_and_calls_kia_processing(self):
        result, npc, npc_ship, defender, mock_destroy_defender, mock_wreck, mock_kia = self._run(
            _combat_result(result=CombatResult.DEFENDER_VICTORY, attacker_ship_destroyed=True)
        )
        assert result["npc_ship_destroyed"] is True
        assert result["defender_ship_destroyed"] is False
        assert npc_ship.is_destroyed is True
        assert npc_ship.is_active is False
        mock_destroy_defender.assert_not_called()
        mock_wreck.assert_called_once()
        wreck_kwargs = mock_wreck.call_args.kwargs
        assert wreck_kwargs["destroyed_ship"] is npc_ship
        assert wreck_kwargs["original_owner"] is None
        assert wreck_kwargs["killing_blow_pilot"] is defender
        mock_kia.assert_called_once()
        assert mock_kia.call_args.args[1] == npc_ship.id
        assert mock_kia.call_args.kwargs["killed_by_player_id"] == defender.id
        assert result["dead_npc"] is not None

    def test_mutual_destruction_fires_both_consequences(self):
        result, npc, npc_ship, defender, mock_destroy_defender, mock_wreck, mock_kia = self._run(
            _combat_result(
                result=CombatResult.MUTUAL_DESTRUCTION,
                attacker_ship_destroyed=True, defender_ship_destroyed=True,
            )
        )
        assert result["npc_ship_destroyed"] is True
        assert result["defender_ship_destroyed"] is True
        mock_destroy_defender.assert_called_once()
        mock_wreck.assert_called_once()
        mock_kia.assert_called_once()

    def test_return_dict_shape_and_values(self):
        result, npc, npc_ship, defender, *_rest = self._run(
            _combat_result(result=CombatResult.ATTACKER_FLED, message="NPC fled")
        )
        assert result["success"] is True
        assert result["message"] == "NPC fled"
        assert result["combat_result"] == "ATTACKER_FLED"
        assert uuid.UUID(result["combat_log_id"])  # a real UUID string
        assert result["npc_id"] == str(npc.id)
        assert result["npc_display_name"] == npc.display_name
        assert result["npc_ship_id"] == str(npc_ship.id)
        assert result["npc_ship_name"] == npc_ship.name
        assert result["defender_id"] == str(defender.id)
        assert result["sector_id"] == SECTOR_ID
        assert result["trigger"] == "wanted_status_engagement"
        assert result["trigger_context"] == {"engagement_id": "abc"}
        assert result["cargo_stolen"] == {}

    def test_resolve_ship_combat_called_with_attacker_none_and_attacker_ship(self):
        npc, npc_ship = _npc()
        defender = _player()
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        with patch.object(
            CombatService, "_resolve_ship_combat", return_value=_combat_result()
        ) as mock_resolve, patch("src.services.npc_combat_initiation_service.flag_modified"):
            svc.initiate_npc_combat(
                db, npc, defender, _sector(), trigger="pirate_aggression"
            )
        mock_resolve.assert_called_once()
        call_kwargs = mock_resolve.call_args.kwargs
        assert call_kwargs["attacker"] is None
        assert call_kwargs["defender"] is defender
        assert call_kwargs["attacker_ship"] is npc_ship


# ---------------------------------------------------------------------------
# Never-raises contract
# ---------------------------------------------------------------------------

class TestNeverRaises:
    def test_unexpected_db_error_returns_failure_dict_not_a_raise(self):
        class _ExplodingSession:
            def query(self, *a, **k):
                raise RuntimeError("connection lost")

        npc, _npc_ship = _npc()
        defender = _player()
        result = svc.initiate_npc_combat(
            _ExplodingSession(), npc, defender, _sector(), trigger="pirate_aggression"
        )
        assert result == {"success": False, "message": "NPC-initiated combat failed unexpectedly"}

    def test_resolve_ship_combat_raising_is_swallowed(self):
        npc, npc_ship = _npc()
        defender = _player()
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        with patch.object(
            CombatService, "_resolve_ship_combat", side_effect=RuntimeError("boom")
        ):
            result = svc.initiate_npc_combat(
                db, npc, defender, _sector(), trigger="pirate_aggression"
            )
        assert result["success"] is False


# ---------------------------------------------------------------------------
# emit_npc_combat_initiated — WS transport + payload shape
# ---------------------------------------------------------------------------

class TestEmitNpcCombatInitiated:
    def test_sends_personal_and_broadcasts_with_correct_payload(self):
        npc, npc_ship = _npc()
        defender = _player()
        combat_log_id = uuid.uuid4()

        async def _run():
            with patch(
                "src.services.websocket_service.connection_manager"
            ) as mock_cm:
                mock_cm.send_combat_update = AsyncMock()
                mock_cm.broadcast_to_sector = AsyncMock()
                svc.emit_npc_combat_initiated(
                    combat_log_id, npc, npc_ship, defender, _sector(),
                    trigger="wanted_status_engagement",
                )
                await asyncio.sleep(0)
                mock_cm.send_combat_update.assert_awaited_once()
                personal_args = mock_cm.send_combat_update.call_args.args
                assert personal_args[0] == str(combat_log_id)
                assert personal_args[2] == [str(defender.user_id)]
                event = personal_args[1]
                assert event["type"] == "npc_combat_initiated"
                assert event["combat_id"] == str(combat_log_id)
                assert event["npc_id"] == str(npc.id)
                assert event["npc_archetype"] == "LAW_ENFORCEMENT"
                assert event["npc_ship_name"] == npc_ship.name
                assert event["defender_id"] == str(defender.id)
                assert event["sector_id"] == SECTOR_ID
                assert event["trigger"] == "wanted_status_engagement"

                mock_cm.broadcast_to_sector.assert_awaited_once()
                sector_args = mock_cm.broadcast_to_sector.call_args.args
                assert sector_args[0] == SECTOR_ID

        asyncio.run(_run())

    def test_no_running_loop_is_silently_swallowed(self):
        npc, npc_ship = _npc()
        defender = _player()
        svc.emit_npc_combat_initiated(
            uuid.uuid4(), npc, npc_ship, defender, _sector(), trigger="x",
        )  # no raise
