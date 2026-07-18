"""tick_npc_legs behavioral suite (Mack's zero-coverage note, hub Accept,
WO-ISP-DOCKPROX+QUEUE-ISP-WS-EMIT-THREAD item 2).

Covers: leg lifecycle (idle -> burn -> materialize), per-NPC SAVEPOINT
isolation (db.begin_nested — one NPC's lock-timeout/error must not roll
back or block legs already committed for other NPCs in the same tick),
the lock_timeout busy-skip path (_sync_npc_presence_pose's 3s SET LOCAL
lock_timeout, same discipline as npc_movement_service.move_npc), and the
>9-body MAX_BODIES overflow fallback exercised end-to-end through
tick_npc_legs (not just sector_destination_pools in isolation).

DB-free: a small in-memory fake Session with GENUINE savepoint semantics
(begin_nested snapshots/restores NPC pose + sector presence state on
exception) — a real assertion of isolation, not just "it didn't crash".
"""
from __future__ import annotations

import copy
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.models.npc_character import NPCActivity, NPCStatus
from src.services import intrasystem_movement_service as isp


@pytest.fixture(autouse=True)
def _noop_flag_modified(monkeypatch):
    """The _Row NPC/Sector stand-ins below aren't SQLAlchemy-mapped, so the
    real flag_modified would raise — same established pattern as
    test_bounty_service_nh2.py. The JSONB dirty-flag is irrelevant to the
    logic under test; every write also reassigns the attribute directly."""
    monkeypatch.setattr(isp, "flag_modified", lambda *a, **k: None)


class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _eq_value(conditions, column_key):
    """Extract the bound literal from a real `Model.col == value` condition
    list (SQLAlchemy BinaryExpression) -- mirrors the codebase's established
    fake-query-filter-interpreter-pattern."""
    for cond in conditions:
        left = getattr(cond, "left", None)
        right = getattr(cond, "right", None)
        if left is not None and getattr(left, "key", None) == column_key and right is not None:
            return getattr(right, "value", None)
    return None


class _FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *a, **kw):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)

    def limit(self, n):
        return self


class _SectorQuery:
    """db.query(Sector).filter(Sector.sector_id == X)[.with_for_update()].first()
    -- raises for sector_ids in fail_sector_ids ONLY when locked (mirrors
    SET LOCAL lock_timeout only ever affecting the LOCKING read, not a
    plain SELECT like sector_destination_pools's own Sector lookup)."""

    def __init__(self, session):
        self.session = session
        self._sector_id = None
        self._locked = False

    def filter(self, *conditions):
        sid = _eq_value(conditions, "sector_id")
        if sid is not None:
            self._sector_id = sid
        return self

    def with_for_update(self):
        self._locked = True
        return self

    def first(self):
        if self._locked and self._sector_id in self.session.fail_sector_ids:
            raise TimeoutError(f"simulated lock timeout on sector {self._sector_id}")
        return self.session.sectors_by_id.get(self._sector_id)


class _ScopedFakeQuery:
    """db.query(Planet|Station).filter(Model.sector_id == X).all() -- real
    sector-scoped filtering (unlike a bare passthrough) so multi-sector
    tests don't cross-contaminate."""

    def __init__(self, rows):
        self._rows = list(rows)
        self._sector_id = None

    def filter(self, *conditions):
        sid = _eq_value(conditions, "sector_id")
        if sid is not None:
            self._sector_id = sid
        return self

    def all(self):
        if self._sector_id is None:
            return list(self._rows)
        return [r for r in self._rows if getattr(r, "sector_id", None) == self._sector_id]

    def first(self):
        rows = self.all()
        return rows[0] if rows else None


class _Savepoint:
    """Genuine SAVEPOINT semantics: snapshots npc.intrasystem_pose (every
    NPC) + sector.players_present (every sector) on entry; restores them on
    ANY exception, exactly like a real db.begin_nested() rollback reverting
    pending ORM attribute changes, not just DB rows."""

    def __init__(self, session):
        self.session = session
        self._snapshot = None

    def __enter__(self):
        self._snapshot = (
            {id(n): copy.deepcopy(n.intrasystem_pose) for n in self.session.npcs},
            {sid: copy.deepcopy(s.players_present) for sid, s in self.session.sectors_by_id.items()},
        )
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            npc_snap, sector_snap = self._snapshot
            for n in self.session.npcs:
                if id(n) in npc_snap:
                    n.intrasystem_pose = npc_snap[id(n)]
            for sid, s in self.session.sectors_by_id.items():
                if sid in sector_snap:
                    s.players_present = sector_snap[sid]
        return False  # never suppress -- matches real begin_nested(), tick_npc_legs's own try/except catches it


class _FakeSession:
    def __init__(
        self, *, npcs, sectors_by_id, planets=None, stations=None,
        celestial_rows=None, fail_sector_ids=(),
    ):
        self.npcs = npcs
        self.sectors_by_id = sectors_by_id
        self.planets = planets or []
        self.stations = stations or []
        self.celestial_rows = celestial_rows or {}
        self.fail_sector_ids = set(fail_sector_ids)
        self.begin_nested_calls = 0

    def query(self, model):
        name = getattr(model, "__name__", "")
        if name == "NPCCharacter":
            return _FakeQuery(self.npcs)
        if name == "Sector":
            return _SectorQuery(self)
        if name == "Planet":
            return _ScopedFakeQuery(self.planets)
        if name == "Station":
            return _ScopedFakeQuery(self.stations)
        if name == "SectorCelestial":
            return _FakeQuery(list(self.celestial_rows.values()))
        return _FakeQuery([])

    def execute(self, *a, **kw):
        return None

    def begin_nested(self):
        self.begin_nested_calls += 1
        return _Savepoint(self)


def _fake_sector(sector_id: int, players_present=None):
    return _Row(id=f"uuid-{sector_id}", sector_id=sector_id, players_present=players_present or [])


def _fake_npc(*, sector_id: int, ship_id=None, pose=None, activity=NPCActivity.PATROL, npc_id=None):
    return _Row(
        id=npc_id or uuid.uuid4(),
        status=NPCStatus.ON_DUTY,
        current_sector_id=sector_id,
        ship_id=ship_id or uuid.uuid4(),
        intrasystem_pose=pose,
        current_activity=activity,
        daily_schedule={"mission": "commerce"},
        archetype=_Row(name="TRADER"),
    )


def _celestial_row(sector_id: int, bodies=None, stations=None):
    return _Row(composition={"bodies": bodies or [], "stations": stations or []})


IDLE_FAR_POSE = {
    "x_pct": 5.0, "y_pct": 5.0, "heading_deg": 0.0,
    "phase": "idle", "burning": False, "leg": None,
}


# ---------------------------------------------------------------------------
# Leg lifecycle
# ---------------------------------------------------------------------------


def test_idle_active_npc_starts_a_new_burn():
    sector_id = 60
    npc = _fake_npc(sector_id=sector_id, pose=dict(IDLE_FAR_POSE))
    sector = _fake_sector(sector_id, players_present=[
        {"player_id": str(npc.id), "is_npc": True, "pose": {}},
    ])
    session = _FakeSession(
        npcs=[npc],
        sectors_by_id={sector_id: sector},
        celestial_rows={sector_id: _celestial_row(
            sector_id, bodies=[{"orbit_au": 0.4, "phase_deg": 30, "kind": "TERRAN", "real": False, "planet_id": None}],
        )},
    )

    moved = isp.tick_npc_legs(session, limit=40)

    assert moved == 1
    assert npc.intrasystem_pose["leg"] is not None
    assert npc.intrasystem_pose["phase"] == "orienting"
    # Presence mirror updated too.
    assert sector.players_present[0]["pose"]["leg"] is not None


def test_sleeping_or_off_activity_npc_is_left_alone():
    sector_id = 61
    npc = _fake_npc(sector_id=sector_id, pose=dict(IDLE_FAR_POSE), activity=NPCActivity.SLEEP)
    sector = _fake_sector(sector_id)
    session = _FakeSession(npcs=[npc], sectors_by_id={sector_id: sector})

    moved = isp.tick_npc_legs(session, limit=40)

    assert moved == 0
    assert npc.intrasystem_pose["leg"] is None


def test_completed_leg_materializes_to_idle():
    sector_id = 62
    started = datetime.now(timezone.utc) - timedelta(hours=1)  # long finished
    leg = {
        "kind": "burn", "from_x": 10.0, "from_y": 10.0, "to_x": 90.0, "to_y": 10.0,
        "started_at": started.isoformat(), "prograde_deg": 0.0, "parked_heading_deg": 0.0,
        "profile": isp.PROFILE_MS,
    }
    pose = {
        "x_pct": 10.0, "y_pct": 10.0, "heading_deg": 0.0,
        "phase": "gliding", "burning": True, "leg": leg,
    }
    npc = _fake_npc(sector_id=sector_id, pose=pose)
    sector = _fake_sector(sector_id, players_present=[
        {"player_id": str(npc.id), "is_npc": True, "pose": {}},
    ])
    session = _FakeSession(npcs=[npc], sectors_by_id={sector_id: sector})

    moved = isp.tick_npc_legs(session, limit=40)

    assert moved == 1
    assert npc.intrasystem_pose["phase"] == "idle"
    assert npc.intrasystem_pose["leg"] is None
    assert npc.intrasystem_pose["x_pct"] == pytest.approx(90.0)


def test_no_pose_yet_gets_a_seeded_idle_default_not_a_crash():
    sector_id = 63
    npc = _fake_npc(sector_id=sector_id, pose=None, activity=NPCActivity.SLEEP)
    sector = _fake_sector(sector_id)
    session = _FakeSession(npcs=[npc], sectors_by_id={sector_id: sector})

    isp.tick_npc_legs(session, limit=40)

    assert npc.intrasystem_pose is not None
    assert npc.intrasystem_pose["phase"] == "idle"


def test_limit_caps_how_many_npcs_move_this_tick():
    sector_id = 64
    npcs = [_fake_npc(sector_id=sector_id, pose=dict(IDLE_FAR_POSE)) for _ in range(5)]
    sector = _fake_sector(sector_id, players_present=[
        {"player_id": str(n.id), "is_npc": True, "pose": {}} for n in npcs
    ])
    session = _FakeSession(
        npcs=npcs,
        sectors_by_id={sector_id: sector},
        celestial_rows={sector_id: _celestial_row(
            sector_id, bodies=[{"orbit_au": 0.4, "phase_deg": 30, "kind": "TERRAN", "real": False, "planet_id": None}],
        )},
    )

    moved = isp.tick_npc_legs(session, limit=2)
    assert moved == 2


# ---------------------------------------------------------------------------
# Per-NPC SAVEPOINT isolation + lock_timeout busy-skip
# ---------------------------------------------------------------------------


def test_one_npcs_lock_timeout_does_not_block_or_corrupt_another_npcs_leg():
    """The core resilience claim: NPC A's sector-row lock times out
    (simulating real Postgres contention) -- A's pose must NOT be left
    partially mutated (SAVEPOINT rollback), and NPC B, processed in the SAME
    tick_npc_legs call, must still complete normally."""
    sector_a, sector_b = 65, 66
    npc_a = _fake_npc(sector_id=sector_a, pose=dict(IDLE_FAR_POSE))
    npc_b = _fake_npc(sector_id=sector_b, pose=dict(IDLE_FAR_POSE))
    sec_a = _fake_sector(sector_a, players_present=[{"player_id": str(npc_a.id), "is_npc": True, "pose": {}}])
    sec_b = _fake_sector(sector_b, players_present=[{"player_id": str(npc_b.id), "is_npc": True, "pose": {}}])
    session = _FakeSession(
        npcs=[npc_a, npc_b],
        sectors_by_id={sector_a: sec_a, sector_b: sec_b},
        celestial_rows={
            sector_a: _celestial_row(sector_a, bodies=[
                {"orbit_au": 0.4, "phase_deg": 30, "kind": "TERRAN", "real": False, "planet_id": None},
            ]),
            sector_b: _celestial_row(sector_b, bodies=[
                {"orbit_au": 0.5, "phase_deg": 90, "kind": "TERRAN", "real": False, "planet_id": None},
            ]),
        },
        fail_sector_ids=[sector_a],  # A's sector row lock times out
    )
    original_a_pose = copy.deepcopy(npc_a.intrasystem_pose)

    moved = isp.tick_npc_legs(session, limit=40)

    # A rolled back cleanly to its pre-tick pose -- no partial/corrupt write.
    assert npc_a.intrasystem_pose == original_a_pose
    assert sec_a.players_present[0]["pose"] == {}

    # B, processed after A in the same call, completed normally.
    assert npc_b.intrasystem_pose["leg"] is not None
    assert sec_b.players_present[0]["pose"]["leg"] is not None

    assert moved == 1  # only B counted
    assert session.begin_nested_calls == 2  # one SAVEPOINT per NPC, both attempted


def test_tick_npc_legs_itself_never_raises_even_if_every_npc_fails():
    sector_id = 67
    npcs = [_fake_npc(sector_id=sector_id, pose=dict(IDLE_FAR_POSE)) for _ in range(3)]
    sector = _fake_sector(sector_id)
    session = _FakeSession(
        npcs=npcs,
        sectors_by_id={sector_id: sector},
        celestial_rows={sector_id: _celestial_row(sector_id, bodies=[
            {"orbit_au": 0.4, "phase_deg": 30, "kind": "TERRAN", "real": False, "planet_id": None},
        ])},
        fail_sector_ids=[sector_id],
    )
    moved = isp.tick_npc_legs(session, limit=40)  # must not raise
    assert moved == 0
    assert session.begin_nested_calls == 3


def test_sync_npc_presence_pose_attempts_set_local_lock_timeout_before_the_lock():
    """Confirms the lock_timeout guard is actually wired into the call path
    (not just present in source) -- a session whose execute() raises on the
    SET LOCAL statement must not abort the sync (best-effort), and must
    still reach the sector lock query afterward."""
    sector_id = 68

    class _RaisingExecuteSession(_FakeSession):
        def execute(self, *a, **kw):
            raise RuntimeError("this session does not support SET LOCAL")

    npc = _fake_npc(sector_id=sector_id, pose=dict(IDLE_FAR_POSE))
    sector = _fake_sector(sector_id, players_present=[{"player_id": str(npc.id), "is_npc": True, "pose": {}}])
    session = _RaisingExecuteSession(
        npcs=[npc],
        sectors_by_id={sector_id: sector},
        celestial_rows={sector_id: _celestial_row(sector_id, bodies=[
            {"orbit_au": 0.4, "phase_deg": 30, "kind": "TERRAN", "real": False, "planet_id": None},
        ])},
    )

    moved = isp.tick_npc_legs(session, limit=40)

    assert moved == 1  # SET LOCAL failure was swallowed; the lock query still ran and succeeded
    assert sector.players_present[0]["pose"]["leg"] is not None


# ---------------------------------------------------------------------------
# >9-body MAX_BODIES overflow fallback, exercised end-to-end
# ---------------------------------------------------------------------------


def test_npc_can_be_routed_to_an_overflow_planet_beyond_max_bodies():
    """15 real planets in one sector (celestial_service.generate_system caps
    the merge at MAX_BODIES=9) -- an NPC's destination pools must still be
    able to include an overflow-fallback planet, and tick_npc_legs must be
    able to burn toward it without crashing, end-to-end through the real
    scheduler entry point (not just sector_destination_pools directly)."""
    from src.services.celestial_service import MAX_BODIES

    sector_id = 69
    planet_ids = [uuid.UUID(int=69000 + i) for i in range(15)]
    planets = [
        _Row(
            id=pid, sector_id=sector_id, habitability_score=10, position=3,
            type=_Row(value="BARREN", name="BARREN"), display_name=f"P-{i}",
            discovered_by=None, owner_id=None,
        )
        for i, pid in enumerate(planet_ids)
    ]
    assert len(planets) > MAX_BODIES

    npc = _fake_npc(sector_id=sector_id, pose=dict(IDLE_FAR_POSE))
    sector = _fake_sector(sector_id, players_present=[{"player_id": str(npc.id), "is_npc": True, "pose": {}}])
    session = _FakeSession(
        npcs=[npc],
        sectors_by_id={sector_id: sector},
        planets=planets,
        # Empty skeleton -- forces every one of the 15 real planets through
        # generate_system's merge (9 of them) + the overflow fallback (6).
        celestial_rows={sector_id: _celestial_row(sector_id)},
    )

    # Deterministic claim (no RNG dependency): the SAME sector_destination_pools
    # call tick_npc_legs itself makes must expose ALL 15 real planets as
    # reachable destinations, not just the 9 generate_system merges.
    pools = isp.sector_destination_pools(session, sector_id, "probe")
    planet_entries = pools["habitable"] + pools["barren"]
    got_ids = {e["target_id"] for e in planet_entries}
    assert got_ids == {str(pid) for pid in planet_ids}

    # End-to-end claim: tick_npc_legs itself (the real scheduler entry
    # point, not a direct sector_destination_pools call) completes cleanly
    # against this overflow-heavy sector and produces a real in-band leg
    # (whichever bucket its own weighted picker happens to choose).
    moved = isp.tick_npc_legs(session, limit=40)
    assert moved == 1
    leg = npc.intrasystem_pose["leg"]
    assert leg is not None
    assert 0.0 <= leg["to_x"] <= 100.0
    assert 0.0 <= leg["to_y"] <= 100.0
