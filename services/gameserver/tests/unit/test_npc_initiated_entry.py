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
  3. ONE true end-to-end test — real (never-flushed) Ship ORM instances +
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
# Guards — _load_attackable_defender-equivalent (_guard_failure) / npc
# ---------------------------------------------------------------------------

class TestGuardFailure:
    def test_npc_ship_missing_fails(self):
        npc, _ = _npc()
        npc.ship_id = None
        defender = _player()
        db = _session_for(defender_ship=defender.current_ship)
        npc_ship, failure = svc._guard_failure(db, npc, defender, _sector())
        assert npc_ship is None
        assert failure["success"] is False
        assert "NPC" in failure["message"]

    def test_npc_ship_destroyed_fails(self):
        npc, npc_ship = _npc(ship=_ship(destroyed=True))
        defender = _player()
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        _, failure = svc._guard_failure(db, npc, defender, _sector())
        assert failure["success"] is False

    def test_npc_not_in_target_sector_fails(self):
        npc, npc_ship = _npc(sector_id=999)
        defender = _player()
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        _, failure = svc._guard_failure(db, npc, defender, _sector())
        assert failure["success"] is False
        assert "sector" in failure["message"].lower()

    def test_defender_no_ship_fails(self):
        npc, npc_ship = _npc()
        defender = _player()
        defender.current_ship = None
        db = _session_for(npc_ship=npc_ship)
        _, failure = svc._guard_failure(db, npc, defender, _sector())
        assert failure["success"] is False

    def test_defender_destroyed_ship_fails(self):
        npc, npc_ship = _npc()
        defender = _player(ship=_ship(destroyed=True))
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        _, failure = svc._guard_failure(db, npc, defender, _sector())
        assert failure["success"] is False

    def test_defender_not_in_target_sector_fails(self):
        npc, npc_ship = _npc()
        defender = _player(sector_id=999)
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        _, failure = svc._guard_failure(db, npc, defender, _sector())
        assert failure["success"] is False

    def test_docked_at_protected_tier_station_fails(self):
        station = _station(security_rank=1)  # basic and above is protected
        npc, npc_ship = _npc()
        defender = _player(is_docked=True, current_port_id=station.id)
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship, stations=[station])
        _, failure = svc._guard_failure(db, npc, defender, _sector())
        assert failure["success"] is False
        assert failure["message"] == "ERR_DOCKED_SHIP_PROTECTED"
        assert failure["error"] == "ERR_DOCKED_SHIP_PROTECTED"

    def test_docked_at_unprotected_tier_station_passes(self):
        station = _station(security_rank=0)
        npc, npc_ship = _npc()
        defender = _player(is_docked=True, current_port_id=station.id)
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship, stations=[station])
        npc_ship_result, failure = svc._guard_failure(db, npc, defender, _sector())
        assert failure is None
        assert npc_ship_result is npc_ship

    def test_landed_defender_fails(self):
        npc, npc_ship = _npc()
        defender = _player(is_landed=True)
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        _, failure = svc._guard_failure(db, npc, defender, _sector())
        assert failure["success"] is False

    def test_healthy_pair_passes(self):
        npc, npc_ship = _npc()
        defender = _player()
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        npc_ship_result, failure = svc._guard_failure(db, npc, defender, _sector())
        assert failure is None
        assert npc_ship_result is npc_ship


# ---------------------------------------------------------------------------
# Terran-Space gate amendment — LAW_ENFORCEMENT bypasses; others don't.
# ---------------------------------------------------------------------------

class TestTerranSpaceGateAmendment:
    def _resolved(self, db, npc, defender, sector, trigger):
        with patch.object(CombatService, "_resolve_ship_combat", return_value=_combat_result()), \
             patch("src.services.combat_service.flag_modified"):
            return svc.initiate_npc_combat(db, npc, defender, sector, trigger=trigger)

    def test_law_enforcement_bypasses_terran_space_block(self):
        npc, npc_ship = _npc(archetype=NPCArchetype.LAW_ENFORCEMENT)
        defender = _player()
        sector = _sector(region_type=RegionType.TERRAN_SPACE)
        db = _session_for(
            npc_ship=npc_ship, defender_ship=defender.current_ship,
            defender=defender, sector=sector,
        )
        result = self._resolved(db, npc, defender, sector, "wanted_status_engagement")
        assert result["success"] is True

    def test_non_law_enforcement_blocked_in_terran_space(self):
        npc, npc_ship = _npc(archetype=NPCArchetype.HOSTILE_RAIDER)
        defender = _player()
        sector = _sector(region_type=RegionType.TERRAN_SPACE)
        db = _session_for(
            npc_ship=npc_ship, defender_ship=defender.current_ship,
            defender=defender, sector=sector,
        )
        result = self._resolved(db, npc, defender, sector, "pirate_aggression")
        assert result["success"] is False
        assert result["message"] == "Combat is not allowed in this sector"

    def test_non_law_enforcement_allowed_outside_terran_space(self):
        npc, npc_ship = _npc(archetype=NPCArchetype.HOSTILE_RAIDER)
        defender = _player()
        sector = _sector(region_type=RegionType.PLAYER_OWNED)
        db = _session_for(
            npc_ship=npc_ship, defender_ship=defender.current_ship,
            defender=defender, sector=sector,
        )
        result = self._resolved(db, npc, defender, sector, "pirate_aggression")
        assert result["success"] is True

    def test_unresolved_region_defaults_to_allowed(self):
        npc, npc_ship = _npc(archetype=NPCArchetype.HOSTILE_RAIDER)
        defender = _player()
        sector = _sector()
        db = _session_for(
            npc_ship=npc_ship, defender_ship=defender.current_ship,
            defender=defender, sector=sector,
        )
        result = self._resolved(db, npc, defender, sector, "pirate_aggression")
        assert result["success"] is True


# ---------------------------------------------------------------------------
# Resolution orchestration — npc_attack_player mocked at the class boundary,
# proving initiate_npc_combat's OWN enrichment/never-raises contract and
# that it calls the PUBLIC method, not a private reach-around.
# ---------------------------------------------------------------------------

class TestOrchestration:
    def _setup(self):
        npc, npc_ship = _npc(display_name="Marshal Vance")
        defender = _player()
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        return npc, npc_ship, defender, db

    def _npc_attack_player_result(self, npc_ship, defender, **overrides):
        base = {
            "success": True,
            "message": "Combat resolved",
            "combat_result": "ATTACKER_VICTORY",
            "combat_log_id": str(uuid.uuid4()),
            "npc_ship_destroyed": False,
            "defender_ship_destroyed": True,
            "dead_npc": None,
            "npc_ship_id": str(npc_ship.id),
            "npc_ship_name": npc_ship.name,
            "npc_ship_type": npc_ship.type.name,
            "defender_id": str(defender.id),
            "defender_ship_id": str(defender.current_ship_id),
            "sector_id": SECTOR_ID,
            "cargo_stolen": {},
        }
        base.update(overrides)
        return base

    def test_calls_the_public_npc_attack_player_method(self):
        """Confirms the no-private-reach-around fix: initiate_npc_combat
        calls CombatService.npc_attack_player(npc_ship_id, defender_id),
        NOT self._resolve_ship_combat directly."""
        npc, npc_ship, defender, db = self._setup()
        with patch.object(
            CombatService, "npc_attack_player",
            return_value=self._npc_attack_player_result(npc_ship, defender),
        ) as mock_call:
            svc.initiate_npc_combat(
                db, npc, defender, _sector(), trigger="wanted_status_engagement"
            )
        mock_call.assert_called_once_with(npc_ship_id=npc_ship.id, defender_id=defender.id)

    def test_success_enriches_result_with_npc_identity_and_trigger(self):
        npc, npc_ship, defender, db = self._setup()
        with patch.object(
            CombatService, "npc_attack_player",
            return_value=self._npc_attack_player_result(npc_ship, defender),
        ):
            result = svc.initiate_npc_combat(
                db, npc, defender, _sector(),
                trigger="wanted_status_engagement", trigger_context={"engagement_id": "abc"},
            )
        assert result["npc_id"] == str(npc.id)
        assert result["npc_display_name"] == "Marshal Vance"
        assert result["trigger"] == "wanted_status_engagement"
        assert result["trigger_context"] == {"engagement_id": "abc"}
        assert result["combat_result"] == "ATTACKER_VICTORY"

    def test_npc_attack_player_failure_returns_its_dict_unenriched_further(self):
        npc, npc_ship, defender, db = self._setup()
        with patch.object(
            CombatService, "npc_attack_player",
            return_value={"success": False, "message": "Target is not in your sector"},
        ):
            result = svc.initiate_npc_combat(
                db, npc, defender, _sector(), trigger="pirate_aggression"
            )
        assert result["success"] is False
        assert result["message"] == "Target is not in your sector"
        assert "npc_id" not in result

    def test_never_raises_on_unexpected_db_error(self):
        class _ExplodingSession:
            def query(self, *a, **k):
                raise RuntimeError("connection lost")

        npc, _npc_ship = _npc()
        defender = _player()
        result = svc.initiate_npc_combat(
            _ExplodingSession(), npc, defender, _sector(), trigger="pirate_aggression"
        )
        assert result == {"success": False, "message": "NPC-initiated combat failed unexpectedly"}

    def test_npc_attack_player_raising_is_swallowed(self):
        npc, npc_ship, defender, db = self._setup()
        with patch.object(
            CombatService, "npc_attack_player", side_effect=RuntimeError("boom"),
        ):
            result = svc.initiate_npc_combat(
                db, npc, defender, _sector(), trigger="pirate_aggression"
            )
        assert result["success"] is False


# ---------------------------------------------------------------------------
# CombatService.npc_attack_player's own guards — direct calls, DB-free.
# ---------------------------------------------------------------------------

class TestNpcAttackPlayerDirectGuards:
    def test_unknown_npc_ship_id_fails(self):
        db = _FakeSession()
        result = CombatService(db).npc_attack_player(uuid.uuid4(), uuid.uuid4())
        assert result["success"] is False
        assert "NPC" in result["message"]

    def test_destroyed_npc_ship_fails(self):
        npc_ship = _ship(destroyed=True)
        db = _FakeSession(ships=[npc_ship])
        result = CombatService(db).npc_attack_player(npc_ship.id, uuid.uuid4())
        assert result["success"] is False

    def test_unknown_defender_id_fails(self):
        npc_ship = _ship()
        db = _FakeSession(ships=[npc_ship])
        result = CombatService(db).npc_attack_player(npc_ship.id, uuid.uuid4())
        assert result["success"] is False
        assert "Defender" in result["message"]

    def test_defender_destroyed_ship_fails(self):
        npc_ship = _ship()
        defender = _player(ship=_ship(destroyed=True))
        db = _session_for(npc_ship=npc_ship, defender_ship=defender.current_ship)
        result = CombatService(db).npc_attack_player(npc_ship.id, defender.id)
        assert result["success"] is False


# ---------------------------------------------------------------------------
# emit_npc_combat_initiated — WS transport + payload shape
# ---------------------------------------------------------------------------

class TestEmitNpcCombatInitiated:
    def test_sends_combat_update_and_broadcasts_with_correct_payload(self):
        npc, npc_ship = _npc(display_name="Marshal Vance")
        defender = _player()
        combat_log_id = uuid.uuid4()

        async def _run():
            with patch("src.services.websocket_service.connection_manager") as mock_cm:
                mock_cm.send_combat_update = AsyncMock()
                mock_cm.broadcast_to_sector = AsyncMock()
                svc.emit_npc_combat_initiated(
                    combat_log_id, npc, npc_ship, defender, _sector(),
                    trigger="wanted_status_engagement",
                )
                await asyncio.sleep(0)

                mock_cm.send_combat_update.assert_awaited_once()
                call_args = mock_cm.send_combat_update.call_args.args
                assert call_args[0] == str(combat_log_id)
                assert call_args[2] == [str(defender.user_id)]
                event = call_args[1]
                assert event["type"] == "npc_combat_initiated"
                assert event["combat_id"] == str(combat_log_id)
                assert event["npc_id"] == str(npc.id)
                assert event["npc_display_name"] == "Marshal Vance"
                assert event["npc_archetype"] == "LAW_ENFORCEMENT"
                assert event["npc_ship_name"] == npc_ship.name
                assert event["npc_ship_type"] == npc_ship.type.name
                assert event["defender_id"] == str(defender.id)
                assert event["defender_name"] == defender.username
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


# ---------------------------------------------------------------------------
# ONE true end-to-end test through the REAL resolution path.
#
# Real (unpersisted) Ship ORM instances + SimpleNamespace Player/Sector +
# scripted `random` module functions for determinism — the established
# pattern from test_combat_core_pins.py / test_siege_vulnerability_combat.py:
# _resolve_ship_combat flag_modified()s the ship's combat JSONB, which
# requires a real mapped instance; Player/Sector are never flag_modified()'d
# so SimpleNamespace is sufficient.
# ---------------------------------------------------------------------------

def _real_combat_ship(*, ship_type=ShipType.LIGHT_FREIGHTER, shields=100, max_shields=100,
                       hull=100, max_hull=100):
    """A fresh, real (never-flushed) Ship instance — see test_combat_core_pins.py's
    _combat_ship() for the same fully-pre-seeded-combat-dict rationale
    (skips _ensure_combat_state's ShipSpecification db lookup branch)."""
    ship = ShipModel()
    ship.id = uuid.uuid4()
    ship.type = ship_type
    ship.combat = {"shields": shields, "max_shields": max_shields, "hull": hull, "max_hull": max_hull}
    ship.maintenance = None
    ship.equipment_slots = {}
    ship.tow_state = None
    ship.shield_resistance = 0.0
    ship.armor_rating = 0.0
    ship.is_destroyed = False
    ship.is_active = True
    ship.name = "Real Test Ship"
    ship.cargo = {"capacity": 50, "used": 0, "contents": {}}
    return ship


class TestEndToEndRealResolution:
    def test_defender_wins_through_the_real_resolve_ship_combat(self):
        """Scripted so the DEFENDER destroys the NPC (not the reverse):
        that path (npc_ship.is_destroyed + _spawn_cargo_wreck +
        handle_npc_ship_destroyed) is self-contained attribute sets, easy
        to keep DB-free by mocking the two consequence calls. The mirror
        case — the player's ship destroyed by the NPC — routes through
        _handle_ship_destruction -> ship_service.destroy_ship, which needs
        a real SAVEPOINT-capable session and is already covered by that
        subsystem's own dedicated test suite, not this WO's concern.

        npc_ship starts at hull=1/shields=0 (any connecting hit destroys
        it); defender_ship starts at hull=100000 (cannot be destroyed
        within the 10-round cap regardless of the attacker's rolls) —
        deterministic without hand-counting every random.random()/randint()
        call; itertools.cycle covers any uncounted extra calls (e.g. the
        per-hit crit roll inside _apply_weapon_damage).
        """
        npc_ship = _real_combat_ship(ship_type=ShipType.DEFENDER, hull=1, max_hull=100,
                                      shields=0, max_shields=0)
        npc_ship.name = "Federation Marshal Interdictor"
        npc_ship.sector_id = SECTOR_ID

        defender_ship = _real_combat_ship(ship_type=ShipType.SCOUT_SHIP, shields=0, max_shields=0,
                                           hull=100_000, max_hull=100_000)

        defender = SimpleNamespace(
            id=uuid.uuid4(),
            username="tester",
            current_ship=defender_ship,
            current_ship_id=defender_ship.id,
            current_sector_id=SECTOR_ID,
            attack_drones=0,
            defense_drones=0,
            military_rank="Recruit",
            is_docked=False,
            current_port_id=None,
            is_landed=False,
            user_id=uuid.uuid4(),
        )

        npc, _unused_fake_ship = _npc(archetype=NPCArchetype.LAW_ENFORCEMENT, sector_id=SECTOR_ID)
        npc.ship_id = npc_ship.id

        sector = SimpleNamespace(
            id=uuid.uuid4(), sector_id=SECTOR_ID, type=None, cluster=None, region_id=None,
        )

        db = _FakeSession(ships=[npc_ship, defender_ship], players=[defender], sectors=[sector])

        with patch.object(combat_service_module.random, "random",
                           lambda: next(itertools.cycle([0.0]))), \
             patch.object(combat_service_module.random, "randint",
                           lambda a, b: next(itertools.cycle([10]))), \
             patch.object(CombatService, "_spawn_cargo_wreck", return_value=None), \
             patch(
                 "src.services.npc_spawn_service.handle_npc_ship_destroyed",
                 return_value=None,
             ):
            result = svc.initiate_npc_combat(
                db, npc, defender, sector, trigger="wanted_status_engagement"
            )

        assert result["success"] is True
        assert result["combat_result"] == "DEFENDER_VICTORY"
        assert result["npc_ship_destroyed"] is True
        assert result["defender_ship_destroyed"] is False
        assert result["npc_id"] == str(npc.id)
        assert result["trigger"] == "wanted_status_engagement"
        assert npc_ship.is_destroyed is True
        # Real combat_log persisted via the fake session's .add() — proves
        # the CombatLog actually built and reached db.add(), not mocked away.
        assert any(
            getattr(obj, "attacker_ship_id", None) == npc_ship.id for obj in db.added
        )
