"""Server-gated engage-range proximity (WO-API-A1).

Trust-fix: POST /combat/engage previously only checked SAME-SECTOR
(CombatService.attack_player / attack_npc_ship's own precondition) -- a
direct REST call could attack from anywhere in the sector, bypassing the
client's own ENGAGE-menu proximity gate (windshieldTableauHelpers.tsx's
ENGAGE_RANGE_EM). These tests prove the SERVER now enforces the same
distance itself, mirroring test_dock_land_proximity.py's / test_claim_
planet_proximity.py's DB-free convention: pure math first, then a
route-wiring proof via direct coroutine call (src.api.routes.player_combat.
engage_combat), no live Postgres, no HTTP.

The route places the gate BEFORE calling into CombatService (which, unlike
assert_dock_land_proximity's routes, never raises HTTPException itself --
every existing precondition in attack_player/attack_npc_ship returns a
dict). CombatService.attack_player/attack_npc_ship are monkeypatched to
canned stubs so "did the gate let this through" is provably distinguished
from "did the (very large, separately-tested) combat resolution succeed" --
a rejected engage never reaches the stub at all (asserted via a stub that
raises if called), an allowed one always does.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import HTTPException

from src.api.routes.player_combat import CombatEngageRequest, engage_combat
from src.services.combat_service import CombatService
from src.services.intrasystem_layout import LAYOUT_BAND_REM_PX
from src.services.intrasystem_movement_service import (
    DOCK_LAND_PROXIMITY_RANGE_EM,
    ENGAGE_RANGE_EM,
    current_npc_pose_xy,
    is_within_engage_range,
)


class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self, *args, **kwargs):
        return self

    def populate_existing(self):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _FakeSession:
    """Routes db.query(Model) by model name -- condition-blind filter(), same
    idiom test_dock_land_proximity.py / test_claim_planet_proximity.py use.
    Each scenario seeds exactly one row per model, so filter()'s inability
    to actually evaluate the SQLAlchemy expression can't mask a wiring bug."""

    def __init__(self, *, ships=None, npcs=None, players=None):
        self._ships = ships or []
        self._npcs = npcs or []
        self._players = players or []

    def query(self, model):
        name = getattr(model, "__name__", "")
        if name == "Ship":
            return _FakeQuery(self._ships)
        if name == "NPCCharacter":
            return _FakeQuery(self._npcs)
        if name == "Player":
            return _FakeQuery(self._players)
        return _FakeQuery([])


def _fake_ship(ship_id, *, owner_id=None, is_npc=False, sector_id=1, name="Target", is_destroyed=False):
    return _Row(
        id=ship_id, owner_id=owner_id, is_npc=is_npc, sector_id=sector_id,
        name=name, is_destroyed=is_destroyed,
    )


def _fake_npc(*, ship_id, sector_id: int, x_pct: float, y_pct: float):
    return _Row(
        id=uuid.uuid4(), ship_id=ship_id, current_sector_id=sector_id,
        intrasystem_pose={
            "x_pct": x_pct, "y_pct": y_pct, "heading_deg": 0.0,
            "phase": "idle", "burning": False, "leg": None,
        },
    )


def _fake_player(*, player_id, sector_id: int, x_pct: float, y_pct: float):
    return _Row(
        id=player_id, current_sector_id=sector_id, current_ship_id=None,
        intrasystem_pose={
            "x_pct": x_pct, "y_pct": y_pct, "heading_deg": 0.0,
            "phase": "idle", "burning": False, "leg": None,
        },
    )


def _run(coro):
    return asyncio.run(coro)


def _stub_success(monkeypatch, method_name: str):
    """Replaces CombatService.<method_name> with a canned success stub and
    returns a call-count list, so a test can assert whether the REAL combat
    resolution surface was reached at all."""
    calls = []

    def _stub(self, *args, **kwargs):
        calls.append(args)
        return {"success": True, "combat_log_id": "stub-log-id", "message": "ok"}

    monkeypatch.setattr(CombatService, method_name, _stub)
    return calls


def _stub_forbid(monkeypatch, method_name: str):
    """Replaces CombatService.<method_name> with a stub that fails the test
    if ever called -- proves a rejected engage never reaches it."""

    def _stub(self, *args, **kwargs):
        raise AssertionError(f"CombatService.{method_name} must not be called for a rejected engage")

    monkeypatch.setattr(CombatService, method_name, _stub)


# ---------------------------------------------------------------------------
# Pure math
# ---------------------------------------------------------------------------


def test_engage_range_em_is_derived_from_dock_land_not_hand_copied():
    """The dial must be DERIVED (3x the server's own dock/land constant),
    exactly mirroring the client's ENGAGE_RANGE_EM = DOCK_RANGE_EM*3 -- both
    DOCK constants are already verified-matching (WO-ISP-DOCKPROX), so this
    derivation is what keeps engage in lockstep with dock/land automatically
    if that dial is ever retuned, instead of silently drifting."""
    assert ENGAGE_RANGE_EM == DOCK_LAND_PROXIMITY_RANGE_EM * 3
    assert ENGAGE_RANGE_EM == pytest.approx(15.0)


def test_is_within_engage_range_boundary_is_at_the_threshold():
    threshold_px = ENGAGE_RANGE_EM * LAYOUT_BAND_REM_PX
    just_inside_pct = ((threshold_px - 0.5) / 1440.0) * 100.0
    just_outside_pct = ((threshold_px + 0.5) / 1440.0) * 100.0
    assert is_within_engage_range(0.0, 50.0, just_inside_pct, 50.0) is True
    assert is_within_engage_range(0.0, 50.0, just_outside_pct, 50.0) is False


def test_current_npc_pose_xy_reads_stored_pose():
    npc = _fake_npc(ship_id=uuid.uuid4(), sector_id=7, x_pct=61.0, y_pct=24.0)
    assert current_npc_pose_xy(npc) == (61.0, 24.0)


def test_current_npc_pose_xy_falls_back_without_mutating_the_row():
    """A not-yet-ticked NPC (no stored pose) still resolves to a real
    sector-anchored position -- computed, never written back onto the row
    (a combat precondition peek is not the place to seed persistent state)."""
    npc = _Row(id=uuid.uuid4(), ship_id=uuid.uuid4(), current_sector_id=3, intrasystem_pose=None)
    x, y = current_npc_pose_xy(npc)
    assert 0.0 <= x <= 100.0 and 0.0 <= y <= 100.0
    assert npc.intrasystem_pose is None  # untouched


# ---------------------------------------------------------------------------
# Route wiring -- PvNPC ('ship' target, owner_id None / is_npc True)
# ---------------------------------------------------------------------------


def test_engage_npc_rejects_out_of_range(monkeypatch):
    sector_id = 11
    ship_id = uuid.uuid4()
    ship = _fake_ship(ship_id, owner_id=None, is_npc=True, sector_id=sector_id, name="Raider Skiff")
    npc = _fake_npc(ship_id=ship_id, sector_id=sector_id, x_pct=90.0, y_pct=50.0)
    attacker = _fake_player(player_id=uuid.uuid4(), sector_id=sector_id, x_pct=0.0, y_pct=50.0)
    session = _FakeSession(ships=[ship], npcs=[npc])
    _stub_forbid(monkeypatch, "attack_npc_ship")

    with pytest.raises(HTTPException) as exc:
        _run(engage_combat(
            request=CombatEngageRequest(targetType="ship", targetId=str(ship_id)),
            player=attacker, db=session,
        ))
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "ERR_TARGET_OUT_OF_RANGE"
    assert "Raider Skiff" in exc.value.detail["message"]


def test_engage_npc_allows_in_range(monkeypatch):
    sector_id = 12
    ship_id = uuid.uuid4()
    ship = _fake_ship(ship_id, owner_id=None, is_npc=True, sector_id=sector_id)
    npc = _fake_npc(ship_id=ship_id, sector_id=sector_id, x_pct=50.5, y_pct=50.0)
    attacker = _fake_player(player_id=uuid.uuid4(), sector_id=sector_id, x_pct=50.0, y_pct=50.0)
    session = _FakeSession(ships=[ship], npcs=[npc])
    calls = _stub_success(monkeypatch, "attack_npc_ship")

    response = _run(engage_combat(
        request=CombatEngageRequest(targetType="ship", targetId=str(ship_id)),
        player=attacker, db=session,
    ))
    assert response.status == "initiated"
    assert len(calls) == 1  # the real (stubbed) service call was reached


def test_engage_npc_cross_sector_skips_the_gate_and_falls_through(monkeypatch):
    """Different sectors -- the NEW gate must not fire at all (a cross-
    sector distance number is meaningless); CombatService's own pre-existing
    'Target is not in your sector' precondition is left to reject it,
    UNCHANGED. Proven here by observing the gate does not raise and the
    (stubbed) service is still reached -- attack_npc_ship's own real
    same-sector check is untouched code, not re-proven by this suite."""
    ship_id = uuid.uuid4()
    ship = _fake_ship(ship_id, owner_id=None, is_npc=True, sector_id=99, name="Elsewhere")
    npc = _fake_npc(ship_id=ship_id, sector_id=99, x_pct=0.0, y_pct=0.0)
    attacker = _fake_player(player_id=uuid.uuid4(), sector_id=1, x_pct=0.0, y_pct=0.0)
    session = _FakeSession(ships=[ship], npcs=[npc])
    calls = _stub_success(monkeypatch, "attack_npc_ship")

    response = _run(engage_combat(
        request=CombatEngageRequest(targetType="ship", targetId=str(ship_id)),
        player=attacker, db=session,
    ))
    assert response.status == "initiated"
    assert len(calls) == 1


def test_engage_npc_missing_npc_character_fails_closed(monkeypatch):
    """Single-pilot invariant (an is_npc Ship always has exactly one
    NPCCharacter) says this should never happen -- if it somehow does, the
    gate must reject (can't verify position), not silently allow."""
    sector_id = 13
    ship_id = uuid.uuid4()
    ship = _fake_ship(ship_id, owner_id=None, is_npc=True, sector_id=sector_id)
    attacker = _fake_player(player_id=uuid.uuid4(), sector_id=sector_id, x_pct=50.0, y_pct=50.0)
    session = _FakeSession(ships=[ship], npcs=[])  # no NPCCharacter row
    _stub_forbid(monkeypatch, "attack_npc_ship")

    with pytest.raises(HTTPException) as exc:
        _run(engage_combat(
            request=CombatEngageRequest(targetType="ship", targetId=str(ship_id)),
            player=attacker, db=session,
        ))
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "ERR_TARGET_POSITION_UNVERIFIED"


# ---------------------------------------------------------------------------
# Route wiring -- PvP ('ship' target, owner_id a real player)
# ---------------------------------------------------------------------------


def test_engage_pvp_rejects_out_of_range(monkeypatch):
    sector_id = 21
    ship_id = uuid.uuid4()
    defender_id = uuid.uuid4()
    ship = _fake_ship(ship_id, owner_id=defender_id, is_npc=False, sector_id=sector_id, name="Merchantman")
    defender = _fake_player(player_id=defender_id, sector_id=sector_id, x_pct=90.0, y_pct=50.0)
    attacker = _fake_player(player_id=uuid.uuid4(), sector_id=sector_id, x_pct=0.0, y_pct=50.0)
    session = _FakeSession(ships=[ship], players=[defender])
    _stub_forbid(monkeypatch, "attack_player")

    with pytest.raises(HTTPException) as exc:
        _run(engage_combat(
            request=CombatEngageRequest(targetType="ship", targetId=str(ship_id)),
            player=attacker, db=session,
        ))
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "ERR_TARGET_OUT_OF_RANGE"
    assert "Merchantman" in exc.value.detail["message"]


def test_engage_pvp_allows_in_range(monkeypatch):
    sector_id = 22
    ship_id = uuid.uuid4()
    defender_id = uuid.uuid4()
    ship = _fake_ship(ship_id, owner_id=defender_id, is_npc=False, sector_id=sector_id)
    defender = _fake_player(player_id=defender_id, sector_id=sector_id, x_pct=50.5, y_pct=50.0)
    attacker = _fake_player(player_id=uuid.uuid4(), sector_id=sector_id, x_pct=50.0, y_pct=50.0)
    session = _FakeSession(ships=[ship], players=[defender])
    calls = _stub_success(monkeypatch, "attack_player")

    response = _run(engage_combat(
        request=CombatEngageRequest(targetType="ship", targetId=str(ship_id)),
        player=attacker, db=session,
    ))
    assert response.status == "initiated"
    assert len(calls) == 1


def test_engage_pvp_cross_sector_skips_the_gate_and_falls_through(monkeypatch):
    ship_id = uuid.uuid4()
    defender_id = uuid.uuid4()
    ship = _fake_ship(ship_id, owner_id=defender_id, is_npc=False, sector_id=99)
    defender = _fake_player(player_id=defender_id, sector_id=99, x_pct=0.0, y_pct=0.0)
    attacker = _fake_player(player_id=uuid.uuid4(), sector_id=1, x_pct=0.0, y_pct=0.0)
    session = _FakeSession(ships=[ship], players=[defender])
    calls = _stub_success(monkeypatch, "attack_player")

    response = _run(engage_combat(
        request=CombatEngageRequest(targetType="ship", targetId=str(ship_id)),
        player=attacker, db=session,
    ))
    assert response.status == "initiated"
    assert len(calls) == 1


def test_engage_pvp_boundary_is_exact(monkeypatch):
    """Same threshold the pure-math test proves, exercised through the
    route (not the raw math) -- just inside passes the gate (reaches the
    stub), just outside is rejected by it."""
    threshold_px = ENGAGE_RANGE_EM * LAYOUT_BAND_REM_PX
    just_inside_x = 50.0 + (((threshold_px - 0.5) / 1440.0) * 100.0)
    just_outside_x = 50.0 + (((threshold_px + 0.5) / 1440.0) * 100.0)

    sector_id = 31
    ship_id = uuid.uuid4()
    defender_id = uuid.uuid4()
    ship = _fake_ship(ship_id, owner_id=defender_id, is_npc=False, sector_id=sector_id)
    attacker = _fake_player(player_id=uuid.uuid4(), sector_id=sector_id, x_pct=50.0, y_pct=50.0)

    inside_defender = _fake_player(player_id=defender_id, sector_id=sector_id, x_pct=just_inside_x, y_pct=50.0)
    session = _FakeSession(ships=[ship], players=[inside_defender])
    calls = _stub_success(monkeypatch, "attack_player")
    response = _run(engage_combat(
        request=CombatEngageRequest(targetType="ship", targetId=str(ship_id)),
        player=attacker, db=session,
    ))
    assert response.status == "initiated"
    assert len(calls) == 1

    outside_defender = _fake_player(player_id=defender_id, sector_id=sector_id, x_pct=just_outside_x, y_pct=50.0)
    session2 = _FakeSession(ships=[ship], players=[outside_defender])
    _stub_forbid(monkeypatch, "attack_player")
    with pytest.raises(HTTPException) as exc:
        _run(engage_combat(
            request=CombatEngageRequest(targetType="ship", targetId=str(ship_id)),
            player=attacker, db=session2,
        ))
    assert exc.value.detail["code"] == "ERR_TARGET_OUT_OF_RANGE"


def test_engage_own_ship_short_circuits_before_the_gate():
    """Pre-existing precondition (attacking your own ship), UNCHANGED --
    must still short-circuit with the same dict-wrapped 200 response, never
    reaching the range gate or CombatService at all."""
    sector_id = 41
    ship_id = uuid.uuid4()
    attacker_id = uuid.uuid4()
    ship = _fake_ship(ship_id, owner_id=attacker_id, is_npc=False, sector_id=sector_id)
    attacker = _fake_player(player_id=attacker_id, sector_id=sector_id, x_pct=50.0, y_pct=50.0)
    session = _FakeSession(ships=[ship])

    response = _run(engage_combat(
        request=CombatEngageRequest(targetType="ship", targetId=str(ship_id)),
        player=attacker, db=session,
    ))
    assert response.status == "error"
    assert response.message == "Cannot attack your own ship"


# ---------------------------------------------------------------------------
# Consolidated revision (cipher MEDIUM, hub-ruled Option B) -- the LOCKED
# service-side backstop. Everything above proves the ROUTE's own optimistic
# pre-check; these tests call CombatService.attack_player / attack_npc_ship
# DIRECTLY -- engage_combat (the route, and its own proximity gate) is NEVER
# invoked at all. This is the literal "route check was bypassed" scenario
# the DoD asks for: a scripted client (or any other future caller) that
# skips the route entirely still cannot skip the gate, because attack_player
# / attack_npc_ship now re-evaluate the SAME is_within_engage_range
# predicate themselves, under the SAME row locks combat resolution uses.
#
# A full two-thread/two-connection TOCTOU race (route-check-passes-then-
# target-moves-before-the-lock) needs live Postgres for genuine concurrent
# commits -- out of reach on the Mac (see test_dock_land_proximity.py's own
# precedent: "Live-host REST proof was deliberately NOT used"). What DOES
# provably close on the Mac, DB-free: this backstop reads pose off the
# object CombatService holds AT THE MOMENT IT RUNS -- calling it directly
# with an out-of-range pose (route entirely absent from the call stack) is
# the strongest available proof that no route-level gate is load-bearing for
# this property; if these tests failed, a route bypass genuinely would
# bypass the whole system, which is exactly the risk cipher flagged.
# ---------------------------------------------------------------------------


class _BackstopQuery:
    """Routes Model.col == literal / Model.col.in_([...]) filters against
    in-memory rows -- pattern: test_money_nolock_rmw_mack.py's own
    _OrderLogQuery, trimmed to what this file's backstop tests need (no
    lock-order logging, this suite doesn't test acquisition order)."""

    def __init__(self, rows):
        self._rows = list(rows)
        self._criteria = []

    def filter(self, *criteria):
        self._criteria.extend(criteria)
        return self

    def _matches(self, row):
        for cond in self._criteria:
            key = cond.left.key
            value = getattr(row, key, None)
            rhs = cond.right.value if hasattr(cond.right, "value") else cond.right
            opname = getattr(cond.operator, "__name__", None)
            if opname == "eq" and value != rhs:
                return False
            if opname == "in_op" and value not in rhs:
                return False
        return True

    def order_by(self, *a, **k):
        return self

    def populate_existing(self):
        return self

    def with_for_update(self, *a, **k):
        return self

    def first(self):
        rows = [r for r in self._rows if self._matches(r)]
        return rows[0] if rows else None

    def all(self):
        return [r for r in self._rows if self._matches(r)]

    def scalar(self):
        # attack_player's friendly-fire pre-lock guard:
        # db.query(Player.team_id).filter(...).scalar() -- every fixture
        # below is teamless (no team_id attr), matching test_money_nolock_
        # rmw_mack.py's own _OrderLogQuery.scalar() precedent.
        return None


class _BackstopSession:
    def __init__(self, *, players=(), ships=(), npcs=(), sectors=()):
        self._players = list(players)
        self._ships = list(ships)
        self._npcs = list(npcs)
        self._sectors = list(sectors)
        self.added = []
        self.commits = 0

    def query(self, model):
        from src.models.npc_character import NPCCharacter
        from src.models.player import Player
        from src.models.sector import Sector
        from src.models.ship import Ship

        if model is Player:
            return _BackstopQuery(self._players)
        if model is Ship:
            return _BackstopQuery(self._ships)
        if model is NPCCharacter:
            return _BackstopQuery(self._npcs)
        if model is Sector:
            return _BackstopQuery(self._sectors)
        return _BackstopQuery([])

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def commit(self):
        self.commits += 1


def _backstop_player(*, pid, ship, sector_id, x_pct, y_pct):
    return _Row(
        id=pid, current_ship=ship, current_ship_id=ship.id,
        current_sector_id=sector_id, turns=99, is_docked=False, is_landed=False,
        defense_drones=0, military_rank=None, current_port_id=None,
        is_suspect=False, suspect_until=None,
        intrasystem_pose={
            "x_pct": x_pct, "y_pct": y_pct, "heading_deg": 0.0,
            "phase": "idle", "burning": False, "leg": None,
        },
    )


def _backstop_ship(*, sid, sector_id):
    from src.models.ship import ShipStatus, ShipType
    return _Row(
        id=sid, type=ShipType.LIGHT_FREIGHTER, is_destroyed=False,
        sector_id=sector_id, hangar=None, tow_state=None,
        status=ShipStatus.IN_SPACE, attack_drones=0, name="Backstop Hull",
    )


def _backstop_npc(*, ship_id, sector_id, x_pct, y_pct):
    return _Row(
        id=uuid.uuid4(), ship_id=ship_id, current_sector_id=sector_id,
        credits=0, archetype=None, title=None,
        intrasystem_pose={
            "x_pct": x_pct, "y_pct": y_pct, "heading_deg": 0.0,
            "phase": "idle", "burning": False, "leg": None,
        },
    )


@pytest.fixture(autouse=True)
def _no_hangar_backstop(monkeypatch):
    monkeypatch.setattr(
        "src.services.hangar_service.HangarService.is_ship_hangared",
        lambda self, ship_id: False,
    )


class TestAttackPlayerBackstopFiresWithoutTheRoute:
    def test_out_of_range_rejected_even_though_the_route_never_ran(self):
        sector_id = 71
        attacker_ship = _backstop_ship(sid=uuid.uuid4(), sector_id=sector_id)
        defender_ship = _backstop_ship(sid=uuid.uuid4(), sector_id=sector_id)
        attacker = _backstop_player(pid=uuid.uuid4(), ship=attacker_ship, sector_id=sector_id, x_pct=0.0, y_pct=50.0)
        defender = _backstop_player(pid=uuid.uuid4(), ship=defender_ship, sector_id=sector_id, x_pct=95.0, y_pct=50.0)
        db = _BackstopSession(players=[attacker, defender], ships=[attacker_ship, defender_ship])

        # engage_combat / the route is NEVER called anywhere in this test --
        # this IS "the route check was bypassed".
        result = CombatService(db).attack_player(attacker.id, defender.id)

        assert result == {
            "success": False,
            "message": "Target is out of engagement range — move closer and try again",
            "error": "ERR_TARGET_OUT_OF_RANGE",
        }
        assert db.commits == 0  # rejected BEFORE turn cost / combat resolution
        assert db.added == []  # no CombatLog persisted

    def test_in_range_reaches_combat_resolution(self, monkeypatch):
        sector_id = 72
        attacker_ship = _backstop_ship(sid=uuid.uuid4(), sector_id=sector_id)
        defender_ship = _backstop_ship(sid=uuid.uuid4(), sector_id=sector_id)
        attacker = _backstop_player(pid=uuid.uuid4(), ship=attacker_ship, sector_id=sector_id, x_pct=50.0, y_pct=50.0)
        defender = _backstop_player(pid=uuid.uuid4(), ship=defender_ship, sector_id=sector_id, x_pct=50.5, y_pct=50.0)
        db = _BackstopSession(players=[attacker, defender], ships=[attacker_ship, defender_ship])
        cs = CombatService(db)
        reached = []
        monkeypatch.setattr(
            cs, "_is_combat_allowed", lambda sector, a, d: reached.append("resolution") or False,
        )

        result = cs.attack_player(attacker.id, defender.id)

        # Proves the backstop did NOT reject: execution reached PAST it, all
        # the way to combat-allowed evaluation (the very next real gate) --
        # a distinctly different rejection message than the range gate's own.
        assert reached == ["resolution"]
        assert result == {"success": False, "message": "Combat is not allowed in this sector"}


class TestAttackNpcShipBackstopFiresWithoutTheRoute:
    def test_out_of_range_rejected_even_though_the_route_never_ran(self):
        sector_id = 73
        ship_id = uuid.uuid4()
        npc_ship = _backstop_ship(sid=ship_id, sector_id=sector_id)
        attacker_ship = _backstop_ship(sid=uuid.uuid4(), sector_id=sector_id)
        attacker = _backstop_player(pid=uuid.uuid4(), ship=attacker_ship, sector_id=sector_id, x_pct=0.0, y_pct=50.0)
        npc = _backstop_npc(ship_id=ship_id, sector_id=sector_id, x_pct=95.0, y_pct=50.0)
        db = _BackstopSession(players=[attacker], ships=[npc_ship], npcs=[npc])

        result = CombatService(db).attack_npc_ship(attacker.id, ship_id)

        assert result == {
            "success": False,
            "message": "Target is out of engagement range — move closer and try again",
            "error": "ERR_TARGET_OUT_OF_RANGE",
        }
        assert db.commits == 0
        assert db.added == []

    def test_missing_npc_character_fails_closed(self):
        """Single-pilot invariant violated (should never happen for a real
        is_npc Ship) -- the service's OWN backstop must fail closed too,
        matching the route's own posture, not just when the route happens
        to be the one evaluating it."""
        sector_id = 74
        ship_id = uuid.uuid4()
        npc_ship = _backstop_ship(sid=ship_id, sector_id=sector_id)
        attacker_ship = _backstop_ship(sid=uuid.uuid4(), sector_id=sector_id)
        attacker = _backstop_player(pid=uuid.uuid4(), ship=attacker_ship, sector_id=sector_id, x_pct=50.0, y_pct=50.0)
        db = _BackstopSession(players=[attacker], ships=[npc_ship], npcs=[])  # no NPCCharacter row

        result = CombatService(db).attack_npc_ship(attacker.id, ship_id)

        assert result == {
            "success": False,
            "message": "Target position could not be verified — try again",
            "error": "ERR_TARGET_POSITION_UNVERIFIED",
        }

    def test_in_range_reaches_combat_resolution(self, monkeypatch):
        sector_id = 75
        ship_id = uuid.uuid4()
        npc_ship = _backstop_ship(sid=ship_id, sector_id=sector_id)
        attacker_ship = _backstop_ship(sid=uuid.uuid4(), sector_id=sector_id)
        attacker = _backstop_player(pid=uuid.uuid4(), ship=attacker_ship, sector_id=sector_id, x_pct=50.0, y_pct=50.0)
        npc = _backstop_npc(ship_id=ship_id, sector_id=sector_id, x_pct=50.5, y_pct=50.0)
        db = _BackstopSession(players=[attacker], ships=[npc_ship], npcs=[npc])
        cs = CombatService(db)
        reached = []
        monkeypatch.setattr(
            cs, "_is_combat_allowed", lambda sector, a, d: reached.append("resolution") or False,
        )

        result = cs.attack_npc_ship(attacker.id, ship_id)

        assert reached == ["resolution"]
        assert result == {"success": False, "message": "Combat is not allowed in this sector"}
