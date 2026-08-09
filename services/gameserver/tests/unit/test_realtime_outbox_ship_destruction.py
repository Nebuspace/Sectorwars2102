"""Unit tests for WO-BUILD-WIRE-REALTIME-OUTBOX-SHIP-DESTRUCTION -- wiring
``src/services/realtime_outbox.py``'s ``RealtimeOutbox`` (ADR-0054 X-V1)
into ``combat_service.py``'s player-ship destruction path, per ADR-0055
Group D / FEATURES/gameplay/ships.md:242 / SYSTEMS/combat-resolver.md:166
(previously a false claim -- zero ``RealtimeOutbox`` hits in
combat_service.py before this diff).

Two isolation levels:
  1. ``_handle_ship_destruction`` directly -- proves the queued
     ``ship.destroyed`` sector event carries a sane payload, and that a
     ``None`` outbox (every not-yet-wired caller) stays a no-op.
  2. ``attack_player`` end-to-end (harness borrowed from
     test_combat_fed_zone_immunity.py's ``_FakeCombatDb`` /
     ``_make_player`` / ``_make_ship`` / ``_sector`` / ``_victory_result``,
     itself borrowed from test_combat_loot_history_nh3b.py per that file's
     own established duplication precedent) -- proves ``RealtimeOutbox.
     flush()`` fires exactly once, strictly AFTER ``db.commit()``, never on
     an early-return/no-destruction path.
"""
from __future__ import annotations

import types
import uuid

import src.services.npc_engagement_service as npc_engagement_service_module
from src.models.combat import CombatResult
from src.models.player import Player as PlayerModel
from src.models.sector import Sector as SectorModel
from src.models.ship import Ship as ShipModel
from src.models.ship import ShipStatus, ShipType
from src.services.combat_service import CombatService
from src.services.realtime_outbox import RealtimeOutbox


def _make_ship(*, type_=ShipType.SCOUT_SHIP, sector_id=7, name="Test Hull"):
    ship = ShipModel()
    ship.id = uuid.uuid4()
    ship.type = type_
    ship.name = name
    ship.cargo = {"capacity": 50, "used": 0, "contents": {}}
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


def _make_player(*, ship, personal_reputation=0):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        username="pilot",
        credits=0,
        turns=999_999,
        max_turns=1_000,
        last_turn_regeneration=None,
        lifetime_turns_spent=0,
        current_ship=ship,
        current_ship_id=ship.id,
        current_sector_id=7,
        is_docked=False,
        is_landed=False,
        current_port_id=None,
        attack_drones=0,
        defense_drones=0,
        military_rank="__no_such_rank__",
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
        is_suspect=False,
        suspect_until=None,
        intrasystem_pose={
            "x_pct": 50.0, "y_pct": 50.0, "heading_deg": 0.0,
            "phase": "idle", "burning": False, "leg": None,
        },
    )


def _sector(*, sector_id=7):
    return types.SimpleNamespace(
        id=uuid.uuid4(), sector_id=sector_id, cluster=None, last_combat=None,
        region_id=None,
    )


def _victory_result():
    return {
        "result": CombatResult.ATTACKER_VICTORY,
        "message": "attacker wins",
        "rounds": 1,
        "attacker_drones_lost": 0,
        "defender_drones_lost": 0,
        "attacker_damage_dealt": 10,
        "defender_damage_dealt": 0,
        "attacker_ship_destroyed": False,
        "defender_ship_destroyed": True,
        "cargo_stolen": {},
        "combat_details": [],
    }


def _no_destruction_result():
    result = _victory_result()
    result["defender_ship_destroyed"] = False
    result["result"] = CombatResult.ATTACKER_VICTORY
    return result


# ---------------------------------------------------------------------------
# Level 1: _handle_ship_destruction directly.
# ---------------------------------------------------------------------------

class TestHandleShipDestructionOutboxQueueing:
    def test_queues_ship_destroyed_with_sane_payload(self, monkeypatch):
        ship = _make_ship(sector_id=42, name="Wrecked Hull")
        player = _make_player(ship=ship)
        destroyer = _make_player(ship=_make_ship())

        cs = CombatService.__new__(CombatService)
        cs.db = types.SimpleNamespace()
        cs.ship_service = types.SimpleNamespace(
            is_ship_indestructible=lambda s: False,
            destroy_ship=lambda ship, destroyer, cause: _make_ship(
                type_=ShipType.ESCAPE_POD, name="Escape Pod",
            ),
        )
        monkeypatch.setattr(cs, "_spawn_cargo_wreck", lambda **k: None)

        outbox = RealtimeOutbox()
        cs._handle_ship_destruction(player, destroyer, "combat", outbox=outbox)

        assert len(outbox) == 1
        kind, event_type, payload, target_sector_id = outbox._events[0]
        assert kind == "sector"
        assert event_type == "ship.destroyed"
        assert target_sector_id == 42
        assert payload["ship_id"] == str(ship.id)
        assert payload["ship_name"] == "Wrecked Hull"
        assert payload["sector_id"] == 42
        assert payload["cause"] == "combat"
        assert payload["original_owner_id"] == str(player.id)
        assert payload["killing_blow_pilot_id"] == str(destroyer.id)

    def test_no_killing_blow_pilot_is_null(self, monkeypatch):
        """Non-PvP causes (drone/planet/port defense) pass destroyer=None --
        the payload's killing_blow_pilot_id must stay null, never crash on
        a None.id access."""
        ship = _make_ship(sector_id=9)
        player = _make_player(ship=ship)

        cs = CombatService.__new__(CombatService)
        cs.db = types.SimpleNamespace()
        cs.ship_service = types.SimpleNamespace(
            is_ship_indestructible=lambda s: False,
            destroy_ship=lambda ship, destroyer, cause: _make_ship(
                type_=ShipType.ESCAPE_POD,
            ),
        )
        monkeypatch.setattr(cs, "_spawn_cargo_wreck", lambda **k: None)

        outbox = RealtimeOutbox()
        cs._handle_ship_destruction(player, None, "drone_combat", outbox=outbox)

        assert len(outbox) == 1
        _, _, payload, _ = outbox._events[0]
        assert payload["killing_blow_pilot_id"] is None
        assert payload["cause"] == "drone_combat"

    def test_none_outbox_is_a_no_op(self, monkeypatch):
        """The default/backward-compatible case: a caller that has not been
        wired to the outbox pattern yet must see identical behavior to
        before this diff -- no AttributeError, nothing queued anywhere."""
        ship = _make_ship()
        player = _make_player(ship=ship)

        cs = CombatService.__new__(CombatService)
        cs.db = types.SimpleNamespace()
        cs.ship_service = types.SimpleNamespace(
            is_ship_indestructible=lambda s: False,
            destroy_ship=lambda ship, destroyer, cause: _make_ship(
                type_=ShipType.ESCAPE_POD,
            ),
        )
        spawn_calls = []
        monkeypatch.setattr(
            cs, "_spawn_cargo_wreck", lambda **k: spawn_calls.append(k)
        )

        # No outbox kwarg at all -- must not raise.
        cs._handle_ship_destruction(player, None, "combat")
        assert len(spawn_calls) == 1

    def test_indestructible_ship_never_queues(self, monkeypatch):
        ship = _make_ship(type_=ShipType.ESCAPE_POD)
        player = _make_player(ship=ship)

        cs = CombatService.__new__(CombatService)
        cs.db = types.SimpleNamespace()
        cs.ship_service = types.SimpleNamespace(
            is_ship_indestructible=lambda s: True,
            destroy_ship=lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("destroy_ship must not be called")
            ),
        )
        outbox = RealtimeOutbox()
        cs._handle_ship_destruction(player, None, "combat", outbox=outbox)

        assert len(outbox) == 0


# ---------------------------------------------------------------------------
# Level 2: attack_player end-to-end -- flush() ordering vs commit().
# ---------------------------------------------------------------------------

class _PlayerQueryStub:
    def __init__(self, players_by_id):
        self._players = players_by_id
        self._pending_id = None

    def filter(self, cond):
        rhs = getattr(cond, "right", None)
        self._pending_id = getattr(rhs, "value", None)
        return self

    def populate_existing(self, *a, **k):
        return self

    def with_for_update(self, *a, **k):
        return self

    def first(self):
        return self._players.get(self._pending_id)


class _PlayerColumnQueryStub:
    def __init__(self, players_by_id, column_key):
        self._players = players_by_id
        self._column_key = column_key
        self._pending_id = None

    def filter(self, cond):
        rhs = getattr(cond, "right", None)
        self._pending_id = getattr(rhs, "value", None)
        return self

    def scalar(self):
        player = self._players.get(self._pending_id)
        if player is None:
            return None
        return getattr(player, self._column_key)


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


class _FakeCombatDb:
    """Minimal synchronous Session double -- routes .query(Model) by class,
    records every .add()ed row, no-ops flush/begin_nested/commit. Also
    records the interleaving of commit()/flush-order-sensitive calls for
    the outbox ordering assertion below."""

    def __init__(self, *, players, ship_first=None, sector=None):
        self._players = {p.id: p for p in players}
        self._ship_first = ship_first
        self._sector = sector
        self.added = []
        self.commits = 0
        self.event_log = []  # append "commit" here on every commit()

    def query(self, model):
        if model is PlayerModel:
            return _PlayerQueryStub(self._players)
        if model is PlayerModel.team_id:
            return _PlayerColumnQueryStub(self._players, "team_id")
        if model is ShipModel:
            return _StubQuery(first=self._ship_first, all_=[])
        if model is SectorModel:
            return _StubQuery(first=self._sector, all_=[])
        return _StubQuery(first=None, all_=[])

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def commit(self):
        self.commits += 1
        self.event_log.append("db.commit")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _setup(monkeypatch, *, combat_result):
    sector = _sector()
    attacker = _make_player(ship=_make_ship(sector_id=7))
    defender = _make_player(ship=_make_ship(sector_id=7))

    db = _FakeCombatDb(players=[attacker, defender], sector=sector)
    cs = CombatService(db)
    monkeypatch.setattr(cs, "_resolve_ship_combat", lambda *a, **k: combat_result)
    monkeypatch.setattr(cs, "_spawn_cargo_wreck", lambda **k: None)
    monkeypatch.setattr(
        cs.ship_service, "destroy_ship",
        lambda ship, destroyer, cause: _make_ship(type_=ShipType.ESCAPE_POD),
    )
    monkeypatch.setattr(
        npc_engagement_service_module, "route_engagement", lambda *a, **k: None
    )
    return cs, db, attacker, defender


class TestAttackPlayerOutboxFlushOrdering:
    def test_flush_fires_exactly_once_strictly_after_commit(self, monkeypatch):
        cs, db, attacker, defender = _setup(monkeypatch, combat_result=_victory_result())

        flush_calls = []
        real_flush = RealtimeOutbox.flush

        def _recording_flush(self):
            db.event_log.append("outbox.flush")
            flush_calls.append(len(self))
            return real_flush(self)

        monkeypatch.setattr(RealtimeOutbox, "flush", _recording_flush)

        result = cs.attack_player(attacker_id=attacker.id, defender_id=defender.id)

        assert result["success"] is True
        assert db.commits == 1
        assert len(flush_calls) == 1
        assert flush_calls[0] == 1  # exactly the one ship.destroyed event
        assert db.event_log == ["db.commit", "outbox.flush"]

    def test_no_destruction_flush_is_a_harmless_no_op(self, monkeypatch):
        """A combat round that resolves without destroying anyone must still
        call flush() exactly once (unconditionally, mirroring the existing
        unconditional db.commit()) but with zero queued events."""
        cs, db, attacker, defender = _setup(
            monkeypatch, combat_result=_no_destruction_result()
        )

        flush_calls = []
        real_flush = RealtimeOutbox.flush

        def _recording_flush(self):
            flush_calls.append(len(self))
            return real_flush(self)

        monkeypatch.setattr(RealtimeOutbox, "flush", _recording_flush)

        result = cs.attack_player(attacker_id=attacker.id, defender_id=defender.id)

        assert result["success"] is True
        assert db.commits == 1
        assert len(flush_calls) == 1
        assert flush_calls[0] == 0
