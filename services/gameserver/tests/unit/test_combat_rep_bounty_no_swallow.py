"""Regression for cipher's friendly-fire gate finding (WO-CMB-SWALLOW-NARROW):
``combat_service.py``'s Personal-reputation + bounty hooks block used to sit
inside a single ``try/except Exception: logger.error(...)`` that wrapped BOTH
money-moving ledger mutations (``BountyService.collect_bounty``,
``PersonalReputationService.adjust_reputation``, ``GreyFlagService.set_grey``)
AND the two decorative side-effect dispatches (``_dispatch_bounty_medals``,
``_emit_bounty_collected``). A failure in a money-moving call was silently
swallowed to a log line -- a bounty could go unpaid or a rep adjustment
skipped with no player-facing signal and no alerting.

The fix removed the outer wrapper entirely: the two decorative dispatches are
each ALREADY self-isolated with their own internal try/except (this is what
makes deleting the outer wrapper safe rather than a behavior change for
them), so a money-moving failure now propagates to the caller (``attack_player``
owns the single commit further below -- an uncaught exception means that
commit never runs, so nothing lands half-applied) while a decorative failure
stays exactly as invisible to the caller as before.

Pattern: test_combat_fed_zone_immunity.py's ``_FakeCombatDb`` / ``_make_player``
/ ``_make_ship`` / ``_sector`` / ``_victory_result`` harness (duplicated
locally per that file's own established precedent, not re-exported).
``attack_player``'s real rep + bounty + grey-flag code path runs UNMOCKED
against the fake session -- only ``_resolve_ship_combat``,
``_handle_ship_destruction``, and ``npc_engagement_service.route_engagement``
are monkeypatched.
"""
from __future__ import annotations

import types
import uuid

import pytest

import src.services.medal_service as medal_service_module
import src.services.npc_engagement_service as npc_engagement_service_module
from src.models.combat import CombatResult
from src.models.player import Player as PlayerModel
from src.models.sector import Sector as SectorModel
from src.models.ship import Ship as ShipModel
from src.models.ship import ShipStatus, ShipType
from src.services.bounty_service import BountyService
from src.services.combat_service import CombatService
from src.services.personal_reputation_service import PersonalReputationService


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
    grey_until=None, grey_kind=None, team_id=None,
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
        team_id=team_id,
        is_suspect=is_suspect,
        suspect_until=suspect_until,
        # WO-API-A1: attack_player now backstops on engage-range -- every
        # fixture built through this shared helper shares this IDENTICAL
        # literal pose, so attacker and defender are always in range.
        intrasystem_pose={
            "x_pct": 50.0, "y_pct": 50.0, "heading_deg": 0.0,
            "phase": "idle", "burning": False, "leg": None,
        },
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
    records every .add()ed row, no-ops flush/begin_nested, COUNTS commit()
    calls (the discriminating signal: a propagated exception must leave
    commits == 0, proving attack_player's single commit never ran)."""

    def __init__(self, *, players, ship_first=None, sector=None):
        self._players = {p.id: p for p in players}
        self._ship_first = ship_first
        self._sector = sector
        self.added = []
        self.commits = 0

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

    def begin_nested(self):
        raise AssertionError("not exercised by this fixture's code paths")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _setup(monkeypatch):
    """Non-suspect, non-grey, good-standing-defender setup that lands
    attack_player squarely in the plain 'attacked a genuine innocent' branch
    -- collect_bounty naturally resolves had_bounty False against the fake
    session's empty Bounty query stub, so rep_service.adjust_reputation(-100,
    'attack_innocent') is the money-moving call this file exercises without
    any extra bounty-row fixture machinery."""
    sector = _sector()
    attacker = _make_player(ship=_make_ship(), personal_reputation=0)
    defender = _make_player(ship=_make_ship(), personal_reputation=0)

    db = _FakeCombatDb(players=[attacker, defender], sector=sector)
    cs = CombatService(db)
    monkeypatch.setattr(cs, "_resolve_ship_combat", lambda *a, **k: _victory_result())
    monkeypatch.setattr(cs, "_handle_ship_destruction", lambda *a, **k: None)
    monkeypatch.setattr(
        npc_engagement_service_module, "route_engagement", lambda *a, **k: None
    )
    return cs, attacker, defender, db


class TestMoneyMovingFailurePropagates:
    """A failure in a state/ledger-mutating call (rep adjust, bounty
    collect, grey-flag set) must now surface to the caller, not vanish
    behind a log line."""

    def test_adjust_reputation_failure_propagates_and_blocks_commit(self, monkeypatch):
        cs, attacker, defender, db = _setup(monkeypatch)

        boom = RuntimeError("ledger write boom")

        def _raise(*a, **k):
            raise boom

        monkeypatch.setattr(PersonalReputationService, "adjust_reputation", _raise)

        with pytest.raises(RuntimeError) as exc_info:
            cs.attack_player(attacker_id=attacker.id, defender_id=defender.id)

        assert exc_info.value is boom
        # The single commit further down attack_player never ran -- nothing
        # landed half-applied behind the now-uncaught exception.
        assert db.commits == 0

    def test_collect_bounty_failure_propagates_and_blocks_commit(self, monkeypatch):
        cs, attacker, defender, db = _setup(monkeypatch)

        boom = RuntimeError("bounty ledger boom")

        def _raise(self, *a, **k):
            raise boom

        monkeypatch.setattr(BountyService, "collect_bounty", _raise)

        with pytest.raises(RuntimeError) as exc_info:
            cs.attack_player(attacker_id=attacker.id, defender_id=defender.id)

        assert exc_info.value is boom
        assert db.commits == 0


class TestDecorativeFailureStillSwallowed:
    """The two decorative dispatches remain best-effort -- self-isolated by
    their OWN internal try/except, unaffected by removing the outer
    wrapper. A hiccup inside them must NOT block the money-moving call that
    already ran, and the attack must still complete + commit."""

    def test_bounty_medal_dispatch_failure_does_not_block_paid_rep_award(
        self, monkeypatch, caplog
    ):
        cs, attacker, defender, db = _setup(monkeypatch)

        # Force the "bounty paid out" branch without standing up real Bounty
        # rows: collect_bounty is itself a money-moving call, but its
        # RESULT here is what routes into the decorative medal dispatch --
        # patched at the class so the local `from ... import BountyService`
        # binding inside attack_player still picks it up.
        paid_result = {
            "success": True,
            "had_bounty": True,
            "total_collected": 500,
            "player_bounties_collected": 500,
            "system_bounties_collected": 0,
        }
        monkeypatch.setattr(
            BountyService, "collect_bounty", lambda self, *a, **k: paid_result
        )

        # The failure is injected INSIDE _dispatch_bounty_medals's own
        # internal try/except (its lazy-imported hook), not by replacing the
        # dispatcher itself -- that's what actually exercises "self-isolated
        # decorative code", rather than trivially avoiding the call.
        def _raise(*a, **k):
            raise RuntimeError("medal hiccup")

        monkeypatch.setattr(
            medal_service_module, "check_and_award_bounty_medals", _raise
        )

        with caplog.at_level("ERROR"):
            result = cs.attack_player(attacker_id=attacker.id, defender_id=defender.id)

        assert result["success"] is True
        # The money-moving rep award for a paid bounty collection landed
        # (ran BEFORE the now-raising decorative dispatch, and was not
        # rolled back by its swallowed failure).
        assert attacker.personal_reputation == 100
        assert db.commits == 1
        assert any("medal hiccup" in r.message for r in caplog.records) or any(
            "medal hiccup" in str(r.exc_info) for r in caplog.records if r.exc_info
        )

    def test_non_suspect_innocent_attack_regression_byte_identical(self, monkeypatch):
        """Baseline, no injected failures anywhere -- the pre-existing
        attack_innocent outcome this diff must leave untouched."""
        cs, attacker, defender, db = _setup(monkeypatch)

        result = cs.attack_player(attacker_id=attacker.id, defender_id=defender.id)

        assert result["success"] is True
        assert attacker.personal_reputation == -100
        assert db.commits == 1
