"""WO-P3-galaxy-gate-destruction -- CombatService.attack_warp_gate() /
_resolve_warp_gate_combat() (src/services/combat_service.py).

ACCEPT bullets covered here (per the assigning WO's own numbering):
  1. resolver applies damage vs gate HP
  2. 75-turn charge debited
  3. shield/turret return-fire hits the attacker
  4. salvage yield on kill (ORE 500 / EQUIPMENT 250 / LUMEN_CRYSTALS 10)
     granted to attacker cargo
  5. invulnerable-window attack REJECTED
  6. on kill WarpTunnel.status=COLLAPSED
  7. gate HP default = 10,000 (active) with beacon/focus untouched at
     5,000

LIVE-PROOF-ONLY BOUNDARY: this suite proves the CODE PATH against a
DB-free fake session -- it does NOT and cannot prove the migration's
backfill actually runs against live rows, that a real commit persists
the HP reduction / COLLAPSED flip / salvage grant, or that the realtime
WS broadcast actually reaches a client. Those are the orchestrator's
windowed live-DB/live-HTTP leg. Each test that touches one of those
edges says so in its own docstring.

DB-free, same WHERE-clause interpreter convention as the beacon test
files (each file owns its fake, per this codebase's own test_contract_
service.py / test_message_beacon_*.py precedent).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest
from sqlalchemy.orm.exc import StaleDataError

from src.models.player import Player
from src.models.ship import Ship, ShipType
from src.models.warp_gate import WarpGate, WarpGateBeacon, WarpGateBeaconStatus, WarpGateStatus
from src.models.warp_tunnel import WarpTunnel, WarpTunnelStatus, WarpTunnelType
from src.services.combat_service import CombatService


# --- WHERE-clause interpreter ------------------------------------------- #

def _match(row: Any, cond: Any) -> bool:
    col_name = cond.left.key
    row_val = getattr(row, col_name, None)
    op_name = getattr(cond.operator, "__name__", None)
    if op_name == "eq":
        right = cond.right.value if hasattr(cond.right, "value") else cond.right
        return row_val == right
    raise NotImplementedError(f"unsupported operator {cond.operator!r}")


class _FakeQuery:
    def __init__(
        self, rows: List[Any], criteria: Optional[List[Any]] = None,
        session: Optional["_FakeSession"] = None, entity: Optional[str] = None,
    ) -> None:
        self._rows = rows
        self._criteria = criteria or []
        self._session = session
        self._entity = entity

    def filter(self, *conditions: Any) -> "_FakeQuery":
        return _FakeQuery(self._rows, self._criteria + list(conditions), self._session, self._entity)

    def with_for_update(self) -> "_FakeQuery":
        # WO-P3 revise (mack, concurrency) -- records WHICH entity got
        # FOR UPDATE'd so a test can prove the gate row is actually
        # locked, not just read.
        if self._session is not None:
            self._session.for_update_calls.append(self._entity)
        return self

    def _matching(self) -> List[Any]:
        return [row for row in self._rows if all(_match(row, c) for c in self._criteria)]

    def first(self) -> Any:
        matches = self._matching()
        return matches[0] if matches else None

    def all(self) -> List[Any]:
        return self._matching()


class _FakeSession:
    def __init__(
        self, *, players=None, ships=None, gates=None, beacons=None, tunnels=None,
        factions=None, reputations=None, fail_delete_ids: Optional[set] = None,
    ) -> None:
        self.players = players or []
        # Auto-derived from each player's own current_ship, matching the
        # beacon test suite's own convention, so every EXISTING
        # _FakeSession(players=[...]) call site keeps working without
        # individually passing ships= too.
        self.ships = ships if ships is not None else [
            p.current_ship for p in self.players if getattr(p, "current_ship", None) is not None
        ]
        self.gates = gates or []
        self.beacons = beacons or []
        self.tunnels = tunnels or []
        self.factions = factions or []
        self.reputations = reputations or []
        self.deleted: List[Any] = []
        self.flush_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.for_update_calls: List[Optional[str]] = []
        # WO-P3 revise test knob -- beacon ids in this set raise
        # StaleDataError on delete() (simulating a concurrent attacker
        # who already destroyed this gate, outside the locked lineage).
        self.fail_delete_ids = fail_delete_ids or set()

    def query(self, *entities: Any) -> Any:
        head = entities[0]
        if head is Player:
            return _FakeQuery(self.players, session=self, entity="Player")
        if head is Ship:
            return _FakeQuery(self.ships, session=self, entity="Ship")
        if head is WarpGate:
            return _FakeQuery(self.gates, session=self, entity="WarpGate")
        if head is WarpGateBeacon:
            return _FakeQuery(self.beacons, session=self, entity="WarpGateBeacon")
        if head is WarpTunnel:
            return _FakeQuery(self.tunnels, session=self, entity="WarpTunnel")
        # Faction / Reputation: no fixtures seeded in this file's tests --
        # empty result set makes dominant_reputation_faction_id() return
        # None early, so the best-effort faction-rep hook cleanly no-ops
        # without needing to fake apply_faction_rep_delta's and_()-based
        # query internals (that helper has its own test coverage in
        # faction_service's own suite -- not re-derived here).
        return _FakeQuery([])

    def add(self, obj: Any) -> None:
        pass

    def delete(self, obj: Any) -> None:
        if getattr(obj, "id", None) in self.fail_delete_ids:
            if obj in self.beacons:
                self.beacons.remove(obj)
            raise StaleDataError("simulated concurrent delete")
        self.deleted.append(obj)
        if obj in self.beacons:
            self.beacons.remove(obj)
        if obj in self.gates:
            self.gates.remove(obj)

    def flush(self) -> None:
        self.flush_calls += 1

    def commit(self) -> None:
        # attack_warp_gate self-commits (matches every sibling attack
        # method in this class -- see this WO's report for why that
        # deviates from the assigning WO's literal "route owns commit").
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1


# --- fixtures ------------------------------------------------------------ #

_NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freezes combat_service.py's OWN `datetime` reference to `_NOW` for
    every test in this file -- the invulnerability-window check calls
    datetime.now(timezone.utc) directly (not an injectable _now() seam
    the way message_beacon_service.py has). Without this, every fixture
    beacon dated relative to the fixed `_NOW` constant is "date-lucky"
    against REAL wall-clock time (the exact bug WO-P4's own REVISE fix 7
    caught and fixed in the beacon service) -- scoped to combat_service's
    module namespace only, so turn_service's own datetime.now() calls
    (a separate module-level import) are unaffected."""
    import src.services.combat_service as svc_module

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return _NOW if tz is not None else _NOW.replace(tzinfo=None)

    monkeypatch.setattr(svc_module, "datetime", _FrozenDatetime)


def _player(**overrides: Any) -> SimpleNamespace:
    player_id = overrides.pop("id", None) or uuid.uuid4()
    ship_id = overrides.pop("current_ship_id", None) or uuid.uuid4()
    ship = overrides.pop("current_ship", None)
    if ship is None:
        ship = Ship(
            id=ship_id, name="Test Warship", type=ShipType.DEFENDER,
            owner_id=player_id, sector_id=42, is_destroyed=False,
            cargo={"capacity": 500, "used": 0, "contents": {}},
            combat={"shields": 100, "max_shields": 100, "hull": 200, "max_hull": 200},
        )
    base = dict(
        # WO-P3 revise (cipher, CRITICAL 2nd pass): defaults to the
        # DESTINATION sector (matches _beacon()'s own default
        # destination_sector_id=99 below) -- the structure attack_
        # warp_gate actually attacks (gate.hp) physically sits at the
        # destination, not the source. Every test in this file that
        # doesn't explicitly override current_sector_id relies on this
        # default matching _beacon()'s default destination.
        id=player_id, username="Voyager7", current_sector_id=99,
        current_ship_id=ship_id, current_ship=ship,
        turns=1000, max_turns=1000, is_docked=False, is_landed=False,
        attack_drones=0, military_rank=None,
        # Pinned to "now" so the real turn_service.regenerate_turns hook
        # (called unconditionally inside _regen_turns) can't silently
        # inflate turns beyond a fixture's deliberately-low override.
        last_turn_regeneration=datetime.now(UTC), lifetime_turns_spent=0,
        created_at=datetime.now(UTC) - timedelta(days=30),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _beacon(**overrides: Any) -> WarpGateBeacon:
    base = dict(
        id=uuid.uuid4(), player_id=uuid.uuid4(),
        source_sector_id=42, destination_sector_id=99,
        status=WarpGateBeaconStatus.MATCHED,
        invulnerable_until=None, hp=5000,
        created_at=_NOW - timedelta(hours=72),  # well past the 48h window by default
    )
    base.update(overrides)
    return WarpGateBeacon(**base)


def _gate(beacon: WarpGateBeacon, **overrides: Any) -> WarpGate:
    base = dict(
        id=uuid.uuid4(), beacon_id=beacon.id, player_id=beacon.player_id,
        warp_tunnel_id=uuid.uuid4(), status=WarpGateStatus.ACTIVE,
        hp=10_000, harmonization_completes_at=None, anchor_ship_id=None,
        construction_cost=0,
    )
    base.update(overrides)
    return WarpGate(**base)


def _tunnel(gate: WarpGate, **overrides: Any) -> WarpTunnel:
    base = dict(
        id=gate.warp_tunnel_id, name="Test Tunnel",
        origin_sector_id=uuid.uuid4(), destination_sector_id=uuid.uuid4(),
        type=WarpTunnelType.ARTIFICIAL, status=WarpTunnelStatus.ACTIVE,
        is_bidirectional=False,
    )
    base.update(overrides)
    return WarpTunnel(**base)


# --- ACCEPT 1: resolver applies damage vs gate HP -------------------------- #

@pytest.mark.unit
class TestResolverDamage:
    def test_attack_reduces_gate_hp(self) -> None:
        attacker = _player()
        beacon = _beacon()
        gate = _gate(beacon, hp=10_000)
        tunnel = _tunnel(gate)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])

        result = CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)

        assert result["success"] is True
        assert gate.hp < 10_000
        assert result["gate_hp_remaining"] == gate.hp

    def test_repeated_attacks_eventually_destroy_a_low_hp_gate(self) -> None:
        """A gate with a small HP remainder is destroyed by ONE attack
        pass -- proves the destroy branch is reachable, not just damage
        application, without depending on exact damage-roll magnitude."""
        attacker = _player()
        beacon = _beacon()
        gate = _gate(beacon, hp=1)
        tunnel = _tunnel(gate)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])

        result = CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)

        assert result["destroyed"] is True
        assert result["gate_hp_remaining"] == 0


# --- ACCEPT 2: 75-turn charge debited -------------------------------------- #

@pytest.mark.unit
class TestTurnCharge:
    def test_attack_debits_exactly_75_turns(self) -> None:
        attacker = _player(turns=1000)
        beacon = _beacon()
        gate = _gate(beacon, hp=10_000)
        tunnel = _tunnel(gate)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])

        result = CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)

        assert CombatService.GATE_ATTACK_TURN_COST == 75
        assert attacker.turns == 1000 - 75
        assert result["turns_consumed"] == 75

    def test_attack_rejected_with_insufficient_turns(self) -> None:
        attacker = _player(turns=10)
        beacon = _beacon()
        gate = _gate(beacon, hp=10_000)
        tunnel = _tunnel(gate)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])

        result = CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)

        assert result["success"] is False
        assert attacker.turns == 10  # untouched -- no partial debit
        assert gate.hp == 10_000  # untouched -- rejected before any mutation


# --- ACCEPT 3: shield/turret return-fire ------------------------------------ #

@pytest.mark.unit
class TestTurretReturnFire:
    def test_no_turrets_means_no_return_fire(self) -> None:
        """Every currently-existing gate has no turret_count column/
        upgrade -- getattr(gate, 'turret_count', 0) defaults to 0, so
        return-fire structurally never fires today. Proves that's a
        clean no-op, not a crash."""
        attacker = _player()
        beacon = _beacon()
        gate = _gate(beacon, hp=10_000)
        tunnel = _tunnel(gate)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])

        result = CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)

        assert result["success"] is True
        # attacker ship combat state untouched (no return-fire applied)
        assert attacker.current_ship.combat["hull"] == 200

    def test_turret_count_present_actually_damages_the_attacker(self) -> None:
        """Proves the return-fire MECHANISM genuinely works when the hook
        IS populated (a future Upgrades WO would set turret_count for
        real) -- not a hardcoded stub. Sets turret_count via a plain
        attribute (getattr-readable) on the WarpGate instance.

        Asserts SHIELDS (not hull) took the hit: the fixture ship's
        shields (100) exceed the turret damage (10 turrets * 3 = 30), so
        the canon damage stack's own "shields absorb first" ordering
        means the hit lands entirely on shields, never reaching hull --
        this is the CORRECT behavior to pin, not a test bug."""
        attacker = _player()
        beacon = _beacon()
        gate = _gate(beacon, hp=10_000)
        gate.turret_count = 10  # simulates a future Upgrades column
        tunnel = _tunnel(gate)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])

        starting_shields = attacker.current_ship.combat["shields"]
        result = CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)

        assert result["success"] is True
        assert attacker.current_ship.combat["shields"] < starting_shields
        assert attacker.current_ship.combat["hull"] == 200  # shields absorbed it all

    def test_heavy_turret_fire_overflows_shields_into_hull(self) -> None:
        """A large enough turret count overwhelms the fixture ship's
        100-point shield pool, proving the residual correctly bleeds into
        hull (the SAME shields-first-then-hull ordering combat-resolver.md
        specifies for every other damage exchange in this codebase)."""
        attacker = _player()
        beacon = _beacon()
        gate = _gate(beacon, hp=10_000)
        gate.turret_count = 50  # 50 * 3 = 150 damage > 100 shield pool
        tunnel = _tunnel(gate)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])

        result = CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)

        # _apply_weapon_damage's shield_hit = absorbed * shield_effectiveness
        # (0.8 for laser) -- 150 damage against 100 shields absorbs the full
        # 100, but only depletes 80 of it (100 * 0.8); the UNABSORBED
        # residual (150 - 100 = 50) is what bleeds into hull, scaled by
        # hull_effectiveness (1.0 for laser).
        assert result["success"] is True
        assert attacker.current_ship.combat["shields"] < 100  # took damage
        assert attacker.current_ship.combat["hull"] < 200  # residual bled through


# --- ACCEPT 4: salvage yield on kill ---------------------------------------- #

@pytest.mark.unit
class TestSalvageGrant:
    def test_kill_grants_canon_exact_salvage_to_attacker_cargo(self) -> None:
        attacker = _player()
        beacon = _beacon()
        gate = _gate(beacon, hp=1)  # guaranteed destroyed by any positive damage
        tunnel = _tunnel(gate)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])

        result = CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)

        assert result["destroyed"] is True
        assert CombatService.GATE_SALVAGE_YIELD == {"ore": 500, "equipment": 250, "lumen_crystals": 10}
        contents = attacker.current_ship.cargo["contents"]
        assert contents.get("ore") == 500
        assert contents.get("equipment") == 250
        assert contents.get("lumen_crystals") == 10
        assert result["salvage_granted"] == CombatService.GATE_SALVAGE_YIELD

    def test_no_kill_grants_no_salvage(self) -> None:
        attacker = _player()
        beacon = _beacon()
        gate = _gate(beacon, hp=10_000)  # survives one attack pass
        tunnel = _tunnel(gate)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])

        result = CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)

        assert result["destroyed"] is False
        assert result["salvage_granted"] == {}
        assert attacker.current_ship.cargo["contents"] == {}


# --- ACCEPT 5: invulnerable-window attack REJECTED -------------------------- #

@pytest.mark.unit
class TestInvulnerabilityWindow:
    def test_attack_within_48h_of_deploy_is_rejected(self) -> None:
        attacker = _player()
        beacon = _beacon(created_at=_NOW - timedelta(hours=1))  # freshly deployed
        gate = _gate(beacon, hp=10_000)
        tunnel = _tunnel(gate)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])

        result = CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)

        assert result["success"] is False
        assert "ERR_GATE_INVULNERABLE" in result["message"]
        assert gate.hp == 10_000  # untouched -- rejected before any mutation
        assert attacker.turns == 1000  # no partial debit

    def test_attack_after_48h_window_succeeds(self) -> None:
        attacker = _player()
        beacon = _beacon(created_at=_NOW - timedelta(hours=49))
        gate = _gate(beacon, hp=10_000)
        tunnel = _tunnel(gate)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])

        result = CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)

        assert result["success"] is True

    def test_exactly_48h_boundary_the_window_has_just_elapsed(self) -> None:
        """Strict less-than in the code (`now < invuln_until` = still
        invulnerable) means AT exactly 48h elapsed, `now == invuln_until`
        is False -- the window has just fully elapsed, attack succeeds.
        The module-wide clock freeze (_frozen_clock, autouse) makes `_NOW`
        the service's own idea of "now", so created_at = _NOW - 48h lands
        exactly on the boundary deterministically."""
        attacker = _player()
        beacon = _beacon(created_at=_NOW - timedelta(hours=CombatService.GATE_INVULNERABILITY_HOURS))
        gate = _gate(beacon, hp=10_000)
        tunnel = _tunnel(gate)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])

        result = CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)

        assert result["success"] is True

    def test_one_second_before_48h_is_still_invulnerable(self) -> None:
        attacker = _player()
        beacon = _beacon(
            created_at=_NOW - timedelta(hours=CombatService.GATE_INVULNERABILITY_HOURS) + timedelta(seconds=1)
        )
        gate = _gate(beacon, hp=10_000)
        tunnel = _tunnel(gate)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])

        result = CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)

        assert result["success"] is False
        assert "ERR_GATE_INVULNERABLE" in result["message"]

    def test_harmonizing_focus_is_also_gated_by_the_window(self) -> None:
        """Canon: 'Anyone can attack a beacon, focus, or active gate after
        the 48-hour invulnerability window' -- the window applies
        uniformly regardless of the gate's phase, not just ACTIVE."""
        attacker = _player()
        beacon = _beacon(status=WarpGateBeaconStatus.DEPLOYED, created_at=_NOW - timedelta(hours=1))
        gate = _gate(beacon, status=WarpGateStatus.HARMONIZING, hp=5000)
        tunnel = _tunnel(gate, status=WarpTunnelStatus.FORMING)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])

        result = CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)

        assert result["success"] is False
        assert "ERR_GATE_INVULNERABLE" in result["message"]

    def test_harmonization_hour_extends_protection_past_the_48h_mark(self) -> None:
        """CONFIRMED against canon's own dedicated section (warp-gates.md
        :161): 'The focus inherits the same invulnerability flag for the
        REMAINDER of that 48h window plus the Phase 3 harmonization hour.'
        Models the edge case where Phase 2 travel eats nearly the full 48h
        and Phase 3 harmonization starts right as the base window would
        otherwise expire -- the gate must stay protected through
        harmonization_completes_at even though beacon.created_at + 48h has
        already elapsed by the time of this attack attempt."""
        attacker = _player()
        # Base 48h window already elapsed...
        beacon = _beacon(created_at=_NOW - timedelta(hours=50))
        gate = _gate(
            beacon, status=WarpGateStatus.HARMONIZING, hp=5000,
            # ...but harmonization (started late, near the 48h mark) isn't
            # due to complete for another hour.
            harmonization_completes_at=_NOW + timedelta(hours=1),
        )
        tunnel = _tunnel(gate, status=WarpTunnelStatus.FORMING)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])

        result = CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)

        assert result["success"] is False
        assert "ERR_GATE_INVULNERABLE" in result["message"]

    def test_active_gate_is_not_extended_by_a_stale_harmonization_timestamp(self) -> None:
        """The harmonization-hour extension applies ONLY while status is
        still HARMONIZING -- an ACTIVE gate's attack window is governed
        purely by the 48h-from-deploy base, even if a stale
        harmonization_completes_at somehow lingered on the row (it
        shouldn't -- advance_gate clears it to None on activation -- but
        the guard should not depend on that cleanup happening)."""
        attacker = _player()
        beacon = _beacon(created_at=_NOW - timedelta(hours=50))
        gate = _gate(
            beacon, status=WarpGateStatus.ACTIVE, hp=10_000,
            harmonization_completes_at=_NOW + timedelta(hours=1),  # stale/leftover
        )
        tunnel = _tunnel(gate)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])

        result = CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)

        assert result["success"] is True


# --- ACCEPT 6: on kill WarpTunnel.status=COLLAPSED -------------------------- #

@pytest.mark.unit
class TestTunnelCollapseOnKill:
    def test_kill_flips_tunnel_to_collapsed(self) -> None:
        attacker = _player()
        beacon = _beacon()
        gate = _gate(beacon, hp=1)
        tunnel = _tunnel(gate, status=WarpTunnelStatus.ACTIVE)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])

        result = CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)

        assert result["destroyed"] is True
        assert tunnel.status == WarpTunnelStatus.COLLAPSED

    def test_non_kill_leaves_tunnel_untouched(self) -> None:
        attacker = _player()
        beacon = _beacon()
        gate = _gate(beacon, hp=10_000)
        tunnel = _tunnel(gate, status=WarpTunnelStatus.ACTIVE)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])

        CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)

        assert tunnel.status == WarpTunnelStatus.ACTIVE

    def test_kill_deletes_the_beacon_structure_row(self) -> None:
        """Canon: 'The structure rows (beacon/focus) are deleted.'
        WarpGate.beacon_id is ondelete=CASCADE, so deleting the beacon
        removes the WarpGate row too in one statement -- deliberately
        NOT cascade_region_gate_teardown's row-preserving COLLAPSED-flip
        pattern (see this WO's report)."""
        attacker = _player()
        beacon = _beacon()
        gate = _gate(beacon, hp=1)
        tunnel = _tunnel(gate)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])

        CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)

        assert beacon not in db.beacons
        assert beacon in db.deleted


# --- WO-P3 revise: location check (cipher, CRITICAL) ----------------------- #
# Mirrors attack_planet/attack_port's own sibling guard. Without this, a
# player could destroy any gate from anywhere with zero counterplay --
# guts the whole "gather defenders at the destination sector" strategic
# model canon describes (warp-gates.md:164).

@pytest.mark.unit
class TestLocationGate:
    def test_attack_from_the_source_sector_is_now_rejected(self) -> None:
        """WO-P3 revise (cipher, CRITICAL 2nd pass): regression pin for
        the original build's mistake -- the source sector is the public,
        undefended side (canon's defense story is entirely about the
        DESTINATION). Standing at the source must no longer be enough."""
        attacker = _player(current_sector_id=42)  # source, not destination
        beacon = _beacon(source_sector_id=42, destination_sector_id=99)
        gate = _gate(beacon, hp=10_000)
        tunnel = _tunnel(gate)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])

        result = CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)

        assert result["success"] is False
        assert "sector" in result["message"].lower()
        assert gate.hp == 10_000  # untouched -- rejected before any mutation
        assert attacker.turns == 1000  # no partial debit

    def test_attack_from_the_destination_sector_succeeds(self) -> None:
        attacker = _player(current_sector_id=99)  # the gate's actual location
        beacon = _beacon(source_sector_id=42, destination_sector_id=99)
        gate = _gate(beacon, hp=10_000)
        tunnel = _tunnel(gate)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])

        result = CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)

        assert result["success"] is True

    def test_attack_from_neither_sector_is_rejected(self) -> None:
        """Rejected-before-mutation, even for a player who could otherwise
        afford the attack -- the guard is a pure early return, not a
        late-stage validation."""
        attacker = _player(current_sector_id=1, turns=1000)
        beacon = _beacon(source_sector_id=42, destination_sector_id=99)
        gate = _gate(beacon, hp=10_000)
        tunnel = _tunnel(gate)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])

        CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)

        assert attacker.turns == 1000
        assert db.commit_calls == 0  # never reached the mutating section

    def test_destroyed_gate_broadcast_targets_the_destination_sector(self) -> None:
        """WO-P3 revise (cipher, sanity-check confirmed + fixed): the
        WS broadcast target must match where the structure actually is --
        the destination, the same field the location check now uses.
        Proven via the emit call-site source (the FakeSession can't
        observe the WS call itself, same live-proof-only boundary as the
        rest of this file's broadcast-adjacent tests)."""
        import inspect
        from src.services import combat_service as svc_module

        source = inspect.getsource(svc_module.CombatService.attack_warp_gate)
        assert "target_sector_id = beacon.destination_sector_id" in source
        assert "_emit_warp_gate_destroyed(gate_id, target_sector_id," in source


# --- ACCEPT 7: gate HP default = 10,000 (active); beacon/focus untouched --- #
# Model-inspection only (like the beacon WO's own CASCADE-declared test) --
# attack_warp_gate doesn't CREATE gates, so this can't be proven via a
# resolver call; the migration itself is the orchestrator's live leg.

@pytest.mark.unit
class TestGateHpDefaults:
    def test_fresh_warp_gate_column_default_is_5000_not_10000(self) -> None:
        """A freshly-created (HARMONIZING/'focus') WarpGate row must stay
        at the canon Focus tier (5,000) -- the migration must NOT have
        bumped the column DEFAULT to 10,000 (that would break every new
        gate going forward, not just backfill stale ACTIVE rows)."""
        assert WarpGate.__table__.columns["hp"].default.arg == 5000

    def test_warp_gate_beacon_column_default_is_5000_and_stays_there(self) -> None:
        assert WarpGateBeacon.__table__.columns["hp"].default.arg == 5000

    def test_advance_gate_still_bumps_active_gates_to_10000(self) -> None:
        """Regression pin (not this WO's own code -- pre-existing, verified
        during verify-first): warp_gate_service.advance_gate() already sets
        gate.hp = 10_000 on HARMONIZING->ACTIVE, which is WHY the migration
        is a historical-data repair and not a code fix. Reads the source
        rather than re-deriving it, to catch a future regression here."""
        import inspect
        from src.services import warp_gate_service

        source = inspect.getsource(warp_gate_service.advance_gate)
        assert "gate.hp = 10_000" in source or "gate.hp = 10000" in source


# --- WO-P3 revise: gate row locked, concurrent-kill race closed ----------- #
# LIVE-PROOF-ONLY BOUNDARY: a DB-free fake session cannot prove two
# transactions actually SERIALIZE on the gate row (that needs real
# concurrent Postgres connections -- the orchestrator's windowed live-DB
# leg). What IS provable here: the lock IS acquired on the WarpGate query,
# and the belt-and-suspenders StaleDataError catch produces a clean
# result rather than an uncaught 500.

@pytest.mark.unit
class TestGateRowLock:
    def test_attack_locks_the_gate_row(self) -> None:
        attacker = _player()
        beacon = _beacon()
        gate = _gate(beacon, hp=10_000)
        tunnel = _tunnel(gate)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])

        CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)

        assert "WarpGate" in db.for_update_calls

    def test_lock_order_is_gate_then_player_then_ship(self) -> None:
        """WO-P3 revise (cipher, HIGH): GATE first, matching
        warp_gate_service.py's own documented contract (module docstring
        :55-57 + 7 reaffirming call sites) -- "the BEACON/GATE row is
        locked first, the PLAYER row second". The original build's
        Player-then-Gate order (matching message_beacon_service's OWN
        Player-then-Sector convention) risked a deadlock against any
        concurrent warp_gate_service operation on the same gate, which
        ALWAYS locks gate-first. This is a different lock-family with its
        own documented order -- not a codebase-wide universal, and this
        test now pins THIS family's actual contract."""
        attacker = _player()
        beacon = _beacon()
        gate = _gate(beacon, hp=1)  # guaranteed kill -> also locks Ship
        tunnel = _tunnel(gate)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])

        CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)

        assert db.for_update_calls == ["WarpGate", "Player", "Ship"]


@pytest.mark.unit
class TestStaleDataOnConcurrentKill:
    def test_beacon_already_deleted_by_another_attacker_returns_clean_result(self) -> None:
        """Belt + suspenders: the gate lock above makes this structurally
        unreachable in steady state (a second attacker's gate query would
        block, then find no row at all -- see TestGateRowLock's own
        docstring) -- but simulates the residual race for any path
        outside the locked lineage. Must return a clean rejection, not
        propagate StaleDataError as an uncaught 500."""
        attacker = _player()
        beacon = _beacon()
        gate = _gate(beacon, hp=1)  # guaranteed kill
        tunnel = _tunnel(gate)
        db = _FakeSession(
            players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel],
            fail_delete_ids={beacon.id},
        )

        result = CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)

        assert result["success"] is False
        assert "ERR_GATE_ALREADY_DESTROYED" in result["message"]
        assert db.rollback_calls == 1

    def test_stale_delete_does_not_raise_past_the_service_call(self) -> None:
        """The exception must never propagate out of attack_warp_gate
        itself -- proven by the call above completing and returning a
        dict rather than pytest catching an unhandled StaleDataError."""
        attacker = _player()
        beacon = _beacon()
        gate = _gate(beacon, hp=1)
        tunnel = _tunnel(gate)
        db = _FakeSession(
            players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel],
            fail_delete_ids={beacon.id},
        )

        result = CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)
        assert isinstance(result, dict)


# --- WO-P3 revise: ObjectDeletedError on gate.id post-commit (mack, CRITICAL) --- #
# LIVE-PROOF-ONLY BOUNDARY: SQLAlchemy's expire_on_commit=True (SessionLocal's
# default) is what actually triggers this bug -- a DB-free FakeSession has no
# such expiration model, so it CANNOT reproduce the ObjectDeletedError itself
# (confirmed harness limitation; this is exactly why the original 29 tests
# were green despite the bug being real on every live kill). Pinned instead
# at the SOURCE level: the fix is "pass the gate_id parameter, never gate.id
# after commit", and that's what's verifiable without a real session.

@pytest.mark.unit
class TestEmitUsesGateIdParameter:
    def test_emit_call_passes_the_gate_id_parameter_not_gate_dot_id(self) -> None:
        """gate.id and the gate_id parameter are numerically IDENTICAL in
        every fixture here (gate_id=gate.id at call time), so no runtime
        assertion on the emitted value can distinguish "used the
        expiration-immune parameter" from "used the ORM attribute that
        would explode post-commit against a real session". Source-
        inspection is the correct tool for this exact class of fix,
        matching this file's own precedent (TestGateHpDefaults's
        advance_gate regression pin)."""
        import inspect
        from src.services import combat_service as svc_module

        source = inspect.getsource(svc_module.CombatService.attack_warp_gate)
        assert "_emit_warp_gate_destroyed(gate_id," in source
        assert "_emit_warp_gate_destroyed(gate.id," not in source


@pytest.mark.unit
class TestSingleCommitInvariant:
    """mack's own ask: pin exactly-one-commit-per-call against a future
    accidental second commit or an early-return added after the commit
    line that would silently double-commit or skip it."""

    def test_successful_kill_commits_exactly_once(self) -> None:
        attacker = _player()
        beacon = _beacon()
        gate = _gate(beacon, hp=1)
        tunnel = _tunnel(gate)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])
        CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)
        assert db.commit_calls == 1

    def test_successful_non_kill_attack_commits_exactly_once(self) -> None:
        attacker = _player()
        beacon = _beacon()
        gate = _gate(beacon, hp=10_000)
        tunnel = _tunnel(gate)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])
        CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)
        assert db.commit_calls == 1

    def test_rejected_attack_never_commits(self) -> None:
        attacker = _player(current_sector_id=1)
        beacon = _beacon(source_sector_id=2)  # wrong-sector rejection
        gate = _gate(beacon, hp=10_000)
        tunnel = _tunnel(gate)
        db = _FakeSession(players=[attacker], gates=[gate], beacons=[beacon], tunnels=[tunnel])
        CombatService(db).attack_warp_gate(attacker_id=attacker.id, gate_id=gate.id)
        assert db.commit_calls == 0
