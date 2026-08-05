"""Unit tests for WO-PROG-SECTOR-SCAN-1 -- MovementService.scan_adjacent_sector.

Two layers, per the house pure-core-plus-DB-wrapper pattern (mirrors
pirate_ecosystem_service.py / test_pirate_ecosystem_foundation.py this same
session):

  * Pure-core tests (no session at all) for the tier ladder
    (_scan_tier), the misread formula (_scan_misread_pct), and the patrol
    band (_patrol_band) -- all @classmethod/@staticmethod on MovementService.
  * End-to-end tests for scan_adjacent_sector itself, against a minimal
    fake Session (regenerate_turns never touches ``db`` on the path these
    fixtures exercise -- confirmed by reading turn_service.py -- so no
    Player-rank query support is needed) with get_available_moves
    monkeypatched at the INSTANCE level to avoid faking the whole
    sector_warps/WarpTunnel adjacency-graph machinery that method owns
    separately (already covered by its own existing tests).
"""
import types
import uuid
from datetime import datetime, timezone

import pytest

from src.services.movement_service import MovementService


# ---------------------------------------------------------------------------
# Pure-core tests -- no session, no fixtures beyond plain values.
# ---------------------------------------------------------------------------

class TestScanTurnCost:
    def test_two_turns_exact_per_canon(self):
        assert MovementService.SCAN_TURN_COST == 2


class TestScanTier:
    def test_tier_0_below_tier1_threshold(self):
        assert MovementService._scan_tier(effective_range=0, aria_level=1) == 0
        assert MovementService._scan_tier(effective_range=1, aria_level=5) == 0

    def test_tier_1_at_and_below_tier2_threshold(self):
        assert MovementService._scan_tier(effective_range=2, aria_level=1) == 1
        assert MovementService._scan_tier(effective_range=3, aria_level=1) == 1

    def test_tier_2_requires_range_and_aria_both(self):
        # Range alone (>=4) without the ARIA gate caps at tier 1.
        assert MovementService._scan_tier(effective_range=4, aria_level=1) == 1
        assert MovementService._scan_tier(effective_range=4, aria_level=2) == 1
        # Both gates clear -> tier 2.
        assert MovementService._scan_tier(effective_range=4, aria_level=3) == 2
        assert MovementService._scan_tier(effective_range=10, aria_level=5) == 2

    def test_aria_gate_never_applies_below_tier2_range(self):
        # A weak-sensor ship with a Transcendent pilot still only gets tier 1
        # -- the ARIA gate is tier-2's SECOND gate, not a bypass for range.
        assert MovementService._scan_tier(effective_range=2, aria_level=5) == 1
        assert MovementService._scan_tier(effective_range=0, aria_level=5) == 0


class TestScanMisreadPct:
    def test_base_with_no_sensor_no_aria(self):
        assert MovementService._scan_misread_pct(sensor_level=0, aria_level=1) == 15

    def test_sensor_reduction_only(self):
        # 15 - 5*3 = 0
        assert MovementService._scan_misread_pct(sensor_level=3, aria_level=1) == 0

    def test_aria_reduction_only(self):
        # 15 - 2*(3-1) = 11
        assert MovementService._scan_misread_pct(sensor_level=0, aria_level=3) == 11

    def test_combined_floors_at_zero_never_negative(self):
        # 15 - 5*5 - 2*4 = 15 - 25 - 8 = -18 -> floored to 0
        assert MovementService._scan_misread_pct(sensor_level=5, aria_level=5) == 0

    def test_partial_combined_reduction(self):
        # 15 - 5*1 - 2*(2-1) = 15 - 5 - 2 = 8
        assert MovementService._scan_misread_pct(sensor_level=1, aria_level=2) == 8


class TestPatrolBand:
    def test_bands_never_expose_exact_counts(self):
        assert MovementService._patrol_band(0) == "none"
        assert MovementService._patrol_band(1) == "light"
        assert MovementService._patrol_band(2) == "light"
        assert MovementService._patrol_band(3) == "moderate"
        assert MovementService._patrol_band(4) == "moderate"
        assert MovementService._patrol_band(5) == "heavy"
        assert MovementService._patrol_band(50) == "heavy"


# ---------------------------------------------------------------------------
# End-to-end tests -- fake Session + instance-level get_available_moves stub.
# ---------------------------------------------------------------------------

TARGET_SECTOR_ID = 4242


class _FakeQuery:
    """Ignores the queried model's filter args (house pattern, mirrors
    test_bounty_service_nh2.py) -- routing happens in _FakeSession.query by
    model identity instead, since each model here maps to exactly one
    registered fixture value per test."""

    def __init__(self, value, is_count=False):
        self._value = value
        self._is_count = is_count

    def filter(self, *a, **k):
        return self

    def populate_existing(self, *a, **k):
        # WO-MONEY-REREAD-SERVICES: no-op passthrough, matches real
        # SQLAlchemy Query's chainable-and-returns-self shape.
        return self

    def with_for_update(self, *a, **k):
        return self

    def first(self):
        return self._value

    def count(self):
        return int(self._value) if self._is_count else 0


class _EmptyJoinQuery:
    """Stands in for the multi-column .query(A, B).join(...).filter(...).all()
    chain regenerate_turns' medal-bonus lookup issues -- always resolves to
    zero rows (no medals held), which is all that hook needs to degrade
    cleanly. See _FakeSession.query."""

    def join(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def all(self):
        return []


class _FakeSession:
    def __init__(self, *, player, target_sector=None, spec=None,
                 other_ship_count=0, planet=None, station=None):
        self._player = player
        self._target_sector = target_sector
        self._spec = spec
        self._other_ship_count = other_ship_count
        self._planet = planet
        self._station = station
        self.committed = False

    def query(self, *models):
        # regenerate_turns' medal-regen-bonus lookup (turn_service.py) issues
        # a multi-column db.query(Medal.category, Medal.effect).join(...) --
        # unrelated to what this WO tests, so route it to an always-empty
        # chain (no medals held) rather than leaving it NotImplementedError,
        # which would fire on every single scan call via the regen hook.
        if len(models) != 1:
            return _EmptyJoinQuery()
        model = models[0]
        name = model.__name__
        if name == "Player":
            return _FakeQuery(self._player)
        if name == "Sector":
            return _FakeQuery(self._target_sector)
        if name == "ShipSpecification":
            return _FakeQuery(self._spec)
        if name == "Ship":
            return _FakeQuery(self._other_ship_count, is_count=True)
        if name == "Planet":
            return _FakeQuery(self._planet)
        if name == "Station":
            return _FakeQuery(self._station)
        raise NotImplementedError(f"fake session: unhandled model {name}")

    def commit(self):
        self.committed = True


def _player(*, turns=100, aria_level=1, ship=None):
    now = datetime.now(timezone.utc)
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        turns=turns,
        max_turns=1000,
        last_turn_regeneration=now,  # anchored to "now" -- no regen delta in-test
        created_at=now,
        aria_bonus_multiplier=1.0,
        aria_consciousness_level=aria_level,
        rank=None,
        current_ship=ship,
        is_docked=False,
        is_landed=False,
        lifetime_turns_spent=0,
    )


def _ship(*, ship_type="SCOUT_SHIP", sensor_level=0):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        type=ship_type,
        upgrades={"SENSOR": sensor_level} if sensor_level else {},
    )


def _sector(*, sector_id=TARGET_SECTOR_ID, hazard=3, radiation=0.5,
            has_asteroids=False, gas_clouds=None, mines=0, patrol_ships=None):
    return types.SimpleNamespace(
        sector_id=sector_id,
        name="Testbed",
        type=types.SimpleNamespace(name="STANDARD"),
        hazard_level=hazard,
        radiation_level=radiation,
        resources={"has_asteroids": has_asteroids, "gas_clouds": gas_clouds or []},
        defenses={"mines": mines, "patrol_ships": patrol_ships or []},
    )


def _spec(*, scanner_range=0):
    return types.SimpleNamespace(scanner_range=scanner_range)


def _service_with_neighbors(db, neighbor_ids):
    """Build a MovementService(db) with get_available_moves stubbed at the
    instance level to the given adjacency set -- avoids faking the whole
    sector_warps/WarpTunnel graph, which that method's OWN tests already
    cover independently."""
    svc = MovementService(db)
    svc.get_available_moves = lambda player_id: {
        "warps": [{"sector_id": sid} for sid in neighbor_ids],
        "tunnels": [],
    }
    return svc


class TestScanAdjacentSectorEndToEnd:
    def test_rejects_when_not_adjacent(self):
        player = _player(ship=_ship())
        db = _FakeSession(player=player, target_sector=_sector())
        svc = _service_with_neighbors(db, neighbor_ids=[999])  # target NOT in this set

        result = svc.scan_adjacent_sector(player.id, TARGET_SECTOR_ID)

        assert result["success"] is False
        assert "adjacent" in result["message"].lower()
        assert db.committed is False
        assert player.turns == 100  # unchanged

    def test_adjacency_accepts_a_tunnel_neighbor_too(self):
        # Mirrors get_available_moves' own {warps, tunnels} union -- a target
        # reachable only via a tunnel must still be scannable.
        player = _player(ship=_ship())
        db = _FakeSession(player=player, target_sector=_sector(), spec=_spec(scanner_range=0))
        svc = MovementService(db)
        svc.get_available_moves = lambda player_id: {
            "warps": [],
            "tunnels": [{"sector_id": TARGET_SECTOR_ID}],
        }

        result = svc.scan_adjacent_sector(player.id, TARGET_SECTOR_ID)
        assert result["success"] is True

    def test_rejects_on_insufficient_turns(self):
        player = _player(turns=1, ship=_ship())  # SCAN_TURN_COST=2, can't afford
        db = _FakeSession(player=player, target_sector=_sector())
        svc = _service_with_neighbors(db, neighbor_ids=[TARGET_SECTOR_ID])

        result = svc.scan_adjacent_sector(player.id, TARGET_SECTOR_ID)

        assert result["success"] is False
        assert "turn" in result["message"].lower()
        assert db.committed is False
        assert player.turns == 1  # unchanged

    def test_success_spends_exactly_two_turns(self):
        player = _player(turns=100, ship=_ship())
        db = _FakeSession(player=player, target_sector=_sector(), spec=_spec(scanner_range=0))
        svc = _service_with_neighbors(db, neighbor_ids=[TARGET_SECTOR_ID])

        result = svc.scan_adjacent_sector(player.id, TARGET_SECTOR_ID)

        assert result["success"] is True
        assert player.turns == 98
        assert result["turns_remaining"] == 98
        assert db.committed is True

    def test_tier_0_payload_shape_omits_higher_tier_fields(self):
        player = _player(turns=100, aria_level=1, ship=_ship(sensor_level=0))
        db = _FakeSession(
            player=player,
            target_sector=_sector(hazard=7, radiation=1.2),
            spec=_spec(scanner_range=0),
        )
        svc = _service_with_neighbors(db, neighbor_ids=[TARGET_SECTOR_ID])

        result = svc.scan_adjacent_sector(player.id, TARGET_SECTOR_ID)

        assert result["tier"] == 0
        assert result["hazard_level"] == 7
        assert result["radiation_level"] == 1.2
        # Beats the free baseline (name/type/turn_cost only) with env data,
        # but nothing from tier 1+ leaks in.
        assert "has_asteroids" not in result
        assert "presence_echo" not in result
        assert "mines_present" not in result
        assert "patrol_band" not in result

    def test_tier_1_payload_adds_resources_and_echo_only(self):
        # sensor_level=3 -> misread_pct=0 (deterministic; see TestScanMisreadPct)
        # so the presence_echo assertion below can't flake on the misread roll.
        player = _player(turns=100, aria_level=1, ship=_ship(sensor_level=3))
        db = _FakeSession(
            player=player,
            target_sector=_sector(has_asteroids=True, gas_clouds=["hydrogen"]),
            spec=_spec(scanner_range=2),  # tier 1 threshold
            other_ship_count=0,
        )
        svc = _service_with_neighbors(db, neighbor_ids=[TARGET_SECTOR_ID])

        result = svc.scan_adjacent_sector(player.id, TARGET_SECTOR_ID)

        assert result["tier"] == 1
        assert result["has_asteroids"] is True
        assert result["has_gas_clouds"] is True
        assert result["presence_echo"] == "silent"  # no other ships
        # Still short of tier 2's defenses/planet/station reveal.
        assert "mines_present" not in result
        assert "patrol_band" not in result
        assert "has_planet" not in result
        assert "has_station" not in result

    def test_tier_1_echo_flags_non_self_ship_presence(self):
        # sensor_level=3 -> misread_pct=0 (deterministic), so this assertion
        # can't flake on the misread roll flipping presence_echo.
        player = _player(turns=100, ship=_ship(sensor_level=3))
        db = _FakeSession(
            player=player, target_sector=_sector(), spec=_spec(scanner_range=2),
            other_ship_count=1,
        )
        svc = _service_with_neighbors(db, neighbor_ids=[TARGET_SECTOR_ID])

        result = svc.scan_adjacent_sector(player.id, TARGET_SECTOR_ID)
        assert result["presence_echo"] == "faint motion"

    def test_tier_2_requires_aria_gate_even_with_max_sensor_range(self):
        # scanner_range=6 clears the range threshold, but aria_level=1 (the
        # default, un-advanced player) fails tier-2's second gate -> tier 1.
        player = _player(turns=100, aria_level=1, ship=_ship(sensor_level=0))
        db = _FakeSession(
            player=player, target_sector=_sector(), spec=_spec(scanner_range=6),
            other_ship_count=0,
        )
        svc = _service_with_neighbors(db, neighbor_ids=[TARGET_SECTOR_ID])

        result = svc.scan_adjacent_sector(player.id, TARGET_SECTOR_ID)
        assert result["tier"] == 1
        assert "mines_present" not in result

    def test_tier_2_payload_adds_defenses_and_structure_flags(self):
        player = _player(turns=100, aria_level=3, ship=_ship(sensor_level=0))  # Awakened
        db = _FakeSession(
            player=player,
            target_sector=_sector(mines=2, patrol_ships=[{"id": "a"}, {"id": "b"}, {"id": "c"}]),
            spec=_spec(scanner_range=6),
            other_ship_count=0,
            planet=types.SimpleNamespace(id=uuid.uuid4()),
            station=None,
        )
        svc = _service_with_neighbors(db, neighbor_ids=[TARGET_SECTOR_ID])

        result = svc.scan_adjacent_sector(player.id, TARGET_SECTOR_ID)

        assert result["tier"] == 2
        assert result["mines_present"] is True
        assert result["patrol_band"] == "moderate"  # 3 patrol ships -> moderate band, never "3"
        assert result["has_planet"] is True
        assert result["has_station"] is False

    def test_never_reveals_more_than_on_arrival_truth(self):
        """Fuzzy-disclosure discipline (hard constraint, mirrors
        quantum_service.scan()): no raw players_present list, no exact
        patrol/mine counts, no player or formation identity fields, no
        misread diagnostics -- at ANY tier, including the richest (tier 2)."""
        player = _player(turns=100, aria_level=5, ship=_ship(sensor_level=5))
        db = _FakeSession(
            player=player,
            target_sector=_sector(mines=1, patrol_ships=[{"id": "a"}] * 9),
            spec=_spec(scanner_range=10),
            other_ship_count=3,
            planet=types.SimpleNamespace(id=uuid.uuid4()),
            station=types.SimpleNamespace(id=uuid.uuid4()),
        )
        svc = _service_with_neighbors(db, neighbor_ids=[TARGET_SECTOR_ID])

        result = svc.scan_adjacent_sector(player.id, TARGET_SECTOR_ID)

        assert result["tier"] == 2
        forbidden_keys = {
            "players_present", "special_formations", "ships_present",
            "patrol_count", "mine_count", "misread", "misread_pct",
            "controlling_faction", "owner_id", "owner_name",
        }
        assert forbidden_keys.isdisjoint(result.keys())
        # The band, not the underlying count of 9.
        assert result["patrol_band"] == "heavy"
        assert isinstance(result["patrol_band"], str)
