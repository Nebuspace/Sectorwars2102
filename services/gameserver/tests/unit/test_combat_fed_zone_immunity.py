"""Unit tests for the SUSPECT-LIFE-1 fed-zone-immunity diff -- combat_service
.py's ``attack_player`` x ``suspect_service.is_live_suspect`` (WO-CMB-SUSPECT-
LIFE-1 held item, applied per the pre-approved shape).

Canon: police-forces.md:36 (attack on an innocent is the Federation Suspect
trigger) + ADR-0059/suspect_service.py's own module doc-comment, which named
this exact diff as prepared-but-held pending an unrelated in-flight pass.
``attack_innocent`` carries NO zone gating anywhere in combat_service.py (the
suspension applies at the mechanic's actual universal scope, not a "fed
space only" framing -- both the canon fed-space framing and the nonexistent
fight-back-cost mechanic are pre-existing DOC-GAPs, not something this test
introduces or resolves).

Pattern: test_combat_log_region_snapshot.py's ``_FakeCombatDb`` /
``_make_player`` / ``_make_ship`` / ``_sector`` / ``_victory_result`` harness
(itself borrowed from test_combat_loot_history_nh3b.py; duplicated locally
per that file's own established precedent, not re-exported). ``attack_player``
's real code path (rep + bounty + grey-flag hooks) runs UNMOCKED against the
fake session -- only ``_resolve_ship_combat``, ``_handle_ship_destruction``,
and ``npc_engagement_service.route_engagement`` (a lazy import, patched at
its source module so the lazy `from src.services import
npc_engagement_service` binding inside attack_player picks it up) are
monkeypatched; combat resolution itself and police-engagement side-effects
are out of scope for this diff.

The defender is deliberately NOT good-standing (personal_reputation < 0) so
``defender_was_good_standing`` is False and GreyFlagService.set_grey is never
reached -- keeps the fixture surface to exactly what this diff touches.
"""
from __future__ import annotations

import types
import uuid
from datetime import datetime, timedelta, timezone

import src.services.npc_engagement_service as npc_engagement_service_module
from src.models.combat import CombatResult
from src.models.player import Player as PlayerModel
from src.models.sector import Sector as SectorModel
from src.models.ship import Ship as ShipModel
from src.models.ship import ShipStatus, ShipType
from src.services.combat_service import CombatService


def _make_ship(*, type_=ShipType.SCOUT_SHIP, sector_id=1, name="Test Hull"):
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


def _make_player(
    *, ship, personal_reputation=0, is_suspect=False, suspect_until=None,
    grey_until=None, grey_kind=None,
):
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
        current_sector_id=1,
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
        grey_until=grey_until,
        grey_kind=grey_kind,
        settings={},
        team_id=None,
        is_suspect=is_suspect,
        suspect_until=suspect_until,
    )


def _sector(*, sector_id=1, region_id=None):
    return types.SimpleNamespace(
        id=uuid.uuid4(), sector_id=sector_id, cluster=None, last_combat=None,
        region_id=region_id,
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


class _PlayerQueryStub:
    def __init__(self, players_by_id):
        self._players = players_by_id
        self._pending_id = None

    def filter(self, cond):
        rhs = getattr(cond, "right", None)
        self._pending_id = getattr(rhs, "value", None)
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


class _FakeCombatDb:
    """Minimal synchronous Session double -- routes .query(Model) by class,
    records every .add()ed row, no-ops flush/begin_nested/commit."""

    def __init__(self, *, players, ship_first=None, sector=None):
        self._players = {p.id: p for p in players}
        self._ship_first = ship_first
        self._sector = sector
        self.added = []
        self.commits = 0

    def query(self, model):
        if model is PlayerModel:
            return _PlayerQueryStub(self._players)
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

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _setup(monkeypatch, *, defender_kwargs):
    sector = _sector()
    attacker = _make_player(ship=_make_ship(), personal_reputation=0)
    defender = _make_player(ship=_make_ship(), personal_reputation=-10, **defender_kwargs)

    db = _FakeCombatDb(players=[attacker, defender], sector=sector)
    cs = CombatService(db)
    monkeypatch.setattr(cs, "_resolve_ship_combat", lambda *a, **k: _victory_result())
    monkeypatch.setattr(cs, "_handle_ship_destruction", lambda *a, **k: None)
    monkeypatch.setattr(
        npc_engagement_service_module, "route_engagement", lambda *a, **k: None
    )
    return cs, attacker, defender


class TestLiveSuspectDefenderSuppressesAttackInnocent:
    def test_live_suspect_defender_no_attack_innocent_consequence(self, monkeypatch):
        """A defender whose suspect_until is still in the future -- the
        canon-lawful "bringing a suspect to justice" case. Neither the -100
        rep penalty nor the police attack_innocent routing should fire."""
        now = datetime.now(timezone.utc)
        cs, attacker, defender = _setup(
            monkeypatch,
            defender_kwargs={
                "is_suspect": True,
                "suspect_until": now + timedelta(minutes=30),
            },
        )

        result = cs.attack_player(attacker_id=attacker.id, defender_id=defender.id)

        assert result["success"] is True
        # No rep penalty applied to the attacker.
        assert attacker.personal_reputation == 0

    def test_expired_suspect_defender_normal_attack_innocent_fires(self, monkeypatch):
        """suspect_until in the PAST (is_suspect boolean still stale-True,
        clock says otherwise) -- the clock is what governs, not the
        boolean, so this must behave exactly like a non-suspect defender:
        normal attack_innocent consequences fire."""
        now = datetime.now(timezone.utc)
        cs, attacker, defender = _setup(
            monkeypatch,
            defender_kwargs={
                "is_suspect": True,
                "suspect_until": now - timedelta(minutes=5),
            },
        )

        result = cs.attack_player(attacker_id=attacker.id, defender_id=defender.id)

        assert result["success"] is True
        assert attacker.personal_reputation == -100

    def test_non_suspect_defender_regression_byte_identical(self, monkeypatch):
        """A plain, never-suspect defender -- the pre-existing behavior this
        diff must leave untouched."""
        cs, attacker, defender = _setup(
            monkeypatch,
            defender_kwargs={"is_suspect": False, "suspect_until": None},
        )

        result = cs.attack_player(attacker_id=attacker.id, defender_id=defender.id)

        assert result["success"] is True
        assert attacker.personal_reputation == -100
