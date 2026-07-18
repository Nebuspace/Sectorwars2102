"""DB-free regression pins for the movement core loop (WO-QTI-CORELOOP-PINS
Lane 3): the turn-DEDUCTION primitive, _execute_movement's wiring of the
computed cost into that primitive, natural-warp-tunnel turn-cost calculation
(disjoint from test_warp_cost_canon's direct-warp uniform-cost pins), and the
latent-tunnel per-player knowledge gate (_player_knows_warp).

Dedupe (grepped tests/unit/ before writing):
  - test_warp_cost_canon.py pins MovementService._calculate_warp_cost -- the
    DIRECT-WARP uniform-per-hull-type cost + its maintenance-band factor.
    This file's TestWarpTunnelCostCalculation pins a DIFFERENT method,
    _check_warp_tunnel (the NATURAL-TUNNEL cost path), whose only shared
    ingredient is the same maintenance-band multiplier helper -- pinned
    here as an independent composition, not a re-pin of that file's cases.
  - test_movement_drone_encounters.py / test_patrol_encounters.py pin
    _check_for_encounters (post-move sector arrival legs) -- untouched here.
  - test_warp_gate_toll.py pins collect_toll / check_traversal_access and
    the player-gate branch's wiring order (turns-check before toll) inside
    move_player_to_sector -- this file does not re-pin the gate branch;
    TestExecuteMovementWiring's structural pins cover the gate/warp/tunnel
    branches' cost-forwarding as a DISJOINT concern (that the exact computed
    cost variable is what reaches _execute_movement / spend_turns).
  - No existing suite pins turn_service.spend_turns directly, or
    _player_knows_warp directly.

_execute_movement (the full post-cost-check movement executor) touches a
long chain of best-effort service hooks (docking release, hangar/tow ride-
along, mine detonation, ARIA exploration-map insert + nested rank-point
award, special-formation discovery, exploration medals, WS room-hop) that
would require faking out most of the service layer to drive end-to-end and
DB-free. Per the WO's "reachable DB-free" framing, this lane instead pins
the turn-deduction mechanics at two levels that together are equivalent in
strength without that fan-out:
  (1) spend_turns itself (the actual primitive _execute_movement calls) --
      pinned directly and exactly.
  (2) structural/AST pins (mirrors test_warp_gate_toll.py's inspect.getsource
      techniques) proving _execute_movement calls spend_turns EXACTLY ONCE,
      and that move_player_to_sector's three success branches each forward
      their own freshly-computed cost variable (gate_cost / warp_cost /
      tunnel_cost) into it -- so the amount actually deducted is provably
      the calculated cost, not a hardcoded or stale one.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import uuid
from types import SimpleNamespace

import pytest

from src.models.ship import ShipType
from src.models.warp_tunnel import WarpTunnelStatus, WarpTunnelType
from src.services import turn_service
from src.services.movement_service import MovementService, _player_knows_warp


# ---------------------------------------------------------------------------
# spend_turns / refund_turns primitives (turn_service.py)
# ---------------------------------------------------------------------------


def _player(*, turns=50, lifetime_turns_spent=0):
    return SimpleNamespace(turns=turns, lifetime_turns_spent=lifetime_turns_spent)


class TestSpendTurnsDeduction:
    def test_spend_turns_deducts_exact_amount(self):
        player = _player(turns=50)
        turn_service.spend_turns(player, 7)
        assert player.turns == 43

    def test_spend_turns_increments_lifetime_turns_spent(self):
        player = _player(turns=50, lifetime_turns_spent=100)
        turn_service.spend_turns(player, 7)
        assert player.lifetime_turns_spent == 107

    def test_spend_turns_treats_none_lifetime_turns_spent_as_zero_baseline(self):
        player = _player(turns=50, lifetime_turns_spent=None)
        turn_service.spend_turns(player, 3)
        assert player.lifetime_turns_spent == 3

    def test_refund_turns_reverses_spend_and_decrements_lifetime_clock(self):
        player = _player(turns=43, lifetime_turns_spent=107)
        turn_service.refund_turns(player, 7)
        assert player.turns == 50
        assert player.lifetime_turns_spent == 100

    def test_refund_turns_floors_lifetime_clock_at_zero(self):
        player = _player(turns=10, lifetime_turns_spent=2)
        turn_service.refund_turns(player, 5)
        assert player.lifetime_turns_spent == 0


# ---------------------------------------------------------------------------
# Structural pins: the computed cost is what actually reaches spend_turns
# ---------------------------------------------------------------------------


class TestExecuteMovementWiring:
    def test_execute_movement_has_exactly_one_spend_turns_call_site(self):
        source = inspect.getsource(MovementService._execute_movement)
        assert source.count("spend_turns(") == 1

    def test_execute_movement_spends_the_turn_cost_parameter_not_a_literal(self):
        """The single spend_turns call site must forward the method's own
        turn_cost parameter -- proving the deducted amount is the caller-
        computed cost, not a hardcoded constant."""
        source = inspect.getsource(MovementService._execute_movement)
        assert "spend_turns(player, turn_cost)" in source

    @pytest.mark.parametrize("branch_call", [
        "self._execute_movement(player, destination_sector_id, gate_cost)",
        "self._execute_movement(player, destination_sector_id, warp_cost)",
        "self._execute_movement(player, destination_sector_id, tunnel_cost)",
    ])
    def test_move_player_to_sector_forwards_its_own_computed_cost(self, branch_call):
        """Each of the three success branches (player-gate / direct-warp /
        natural-tunnel) in move_player_to_sector must forward ITS OWN
        computed cost variable into _execute_movement -- a copy-paste that
        forwarded the wrong branch's variable would silently charge the
        wrong cost."""
        source = inspect.getsource(MovementService.move_player_to_sector)
        assert branch_call in source

    def test_move_player_to_sector_has_exactly_three_execute_movement_call_sites(self):
        """One per success branch (gate / direct-warp / tunnel) -- a fourth
        call site would mean an undocumented cost-charging path exists."""
        module_path = (
            pathlib.Path(__file__).resolve().parents[2]
            / "src" / "services" / "movement_service.py"
        )
        tree = ast.parse(module_path.read_text())
        move_fn = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "move_player_to_sector"
        )

        def is_execute_movement_call(node: ast.AST) -> bool:
            return (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_execute_movement"
            )

        call_sites = [n for n in ast.walk(move_fn) if is_execute_movement_call(n)]
        assert len(call_sites) == 3


# ---------------------------------------------------------------------------
# Natural warp-tunnel cost calculation (_check_warp_tunnel) -- disjoint from
# test_warp_cost_canon.py's direct-warp uniform-cost pins
# ---------------------------------------------------------------------------


def _sector(sector_id):
    return SimpleNamespace(id=uuid.uuid4(), sector_id=sector_id)


def _tunnel(*, turn_cost, is_latent=False, status=WarpTunnelStatus.ACTIVE,
            is_bidirectional=True, tunnel_type=WarpTunnelType.NATURAL,
            created_by_player_id=None, properties=None, expires_at=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        turn_cost=turn_cost,
        status=status,
        is_bidirectional=is_bidirectional,
        is_latent=is_latent,
        type=tunnel_type,
        created_by_player_id=created_by_player_id,
        properties=properties or {},
        expires_at=expires_at,
        created_at=None,
    )


def _ship(*, warp_capable=False, condition=80.0, owner_id=None):
    """condition=80.0 sits in the neutral Good band (ships.md:68-75,
    matching test_warp_cost_canon.py's own fixture choice) -- speed 0.0 ->
    _maintenance_speed_multiplier returns exactly 1.0."""
    return SimpleNamespace(
        type=ShipType.CARGO_HAULER,
        warp_capable=warp_capable,
        maintenance={"condition": condition, "last_maintenance": None},
        owner_id=owner_id or uuid.uuid4(),
    )


class _FakeQuery:
    def __init__(self, first=None):
        self._first = first

    def filter(self, *a, **k):
        return self

    def with_for_update(self, *a, **k):
        return self

    def first(self):
        return self._first


class _FakeSession:
    def __init__(self, specs):
        self._specs = specs

    def query(self, model):
        assert model in self._specs, f"unexpected query for {model!r}"
        return self._specs[model]


def _service_for_tunnel(tunnel, *, knowledge_row=None):
    from src.models.sector import Sector
    from src.models.warp_tunnel import WarpTunnel
    from src.models.player_warp_knowledge import PlayerWarpKnowledge

    mock_db = _FakeSession({
        Sector: _FakeQuery(first=_sector(1)),
        WarpTunnel: _FakeQuery(first=tunnel),
        PlayerWarpKnowledge: _FakeQuery(first=knowledge_row),
    })
    return MovementService(mock_db)


class TestWarpTunnelCostCalculation:
    def test_warp_capable_ship_gets_20pct_reduction(self):
        service = _service_for_tunnel(_tunnel(turn_cost=10))
        ship = _ship(warp_capable=True)
        can_tunnel, cost, _ = service._check_warp_tunnel(1, 2, ship)
        assert can_tunnel is True
        assert cost == 8  # max(1, int(10*0.8)) * 1.0 maintenance multiplier

    def test_non_warp_capable_ship_pays_full_tunnel_cost(self):
        service = _service_for_tunnel(_tunnel(turn_cost=10))
        ship = _ship(warp_capable=False)
        _, cost, _ = service._check_warp_tunnel(1, 2, ship)
        assert cost == 10

    def test_maintenance_speed_multiplier_alone_raises_cost_for_worn_ship(self):
        good_service = _service_for_tunnel(_tunnel(turn_cost=20))
        worn_service = _service_for_tunnel(_tunnel(turn_cost=20))
        good_cost = good_service._check_warp_tunnel(1, 2, _ship(condition=80.0))[1]
        worn_cost = worn_service._check_warp_tunnel(1, 2, _ship(condition=60.0))[1]
        assert good_cost == 20  # Good band: multiplier exactly 1.0
        assert worn_cost > good_cost

    def test_warp_capable_reduction_composes_with_maintenance_multiplier(self):
        good_service = _service_for_tunnel(_tunnel(turn_cost=25))
        worn_service = _service_for_tunnel(_tunnel(turn_cost=25))
        good_cost = good_service._check_warp_tunnel(1, 2, _ship(warp_capable=True, condition=80.0))[1]
        worn_cost = worn_service._check_warp_tunnel(1, 2, _ship(warp_capable=True, condition=60.0))[1]
        assert good_cost == 20  # max(1, int(25*0.8)) = 20, Good multiplier 1.0 -> 20
        assert worn_cost > good_cost

    def test_tunnel_cost_floors_at_1_even_with_warp_capable_reduction(self):
        service = _service_for_tunnel(_tunnel(turn_cost=1))
        ship = _ship(warp_capable=True)
        _, cost, _ = service._check_warp_tunnel(1, 2, ship)
        assert cost == 1  # int(1*0.8)=0 -> floored to 1


# ---------------------------------------------------------------------------
# Latent-tunnel per-player knowledge gate (_player_knows_warp / WO-LW)
# ---------------------------------------------------------------------------


class TestLatentTunnelKnowledgeGating:
    def test_latent_tunnel_unknown_to_player_is_rejected(self):
        service = _service_for_tunnel(_tunnel(turn_cost=5, is_latent=True), knowledge_row=None)
        can_tunnel, cost, message = service._check_warp_tunnel(1, 2, _ship())
        assert can_tunnel is False
        assert cost == 0
        assert message == "No active warp tunnel found"

    def test_latent_tunnel_known_to_player_is_traversable(self):
        known_row = SimpleNamespace(is_known=True)
        service = _service_for_tunnel(_tunnel(turn_cost=5, is_latent=True), knowledge_row=known_row)
        can_tunnel, cost, _ = service._check_warp_tunnel(1, 2, _ship())
        assert can_tunnel is True
        assert cost == 5

    def test_non_latent_tunnel_bypasses_the_knowledge_check_entirely(self):
        """No knowledge row at all (None) -- a non-latent tunnel must still
        be traversable, proving the gate only engages for is_latent=True."""
        service = _service_for_tunnel(_tunnel(turn_cost=5, is_latent=False), knowledge_row=None)
        can_tunnel, cost, _ = service._check_warp_tunnel(1, 2, _ship())
        assert can_tunnel is True
        assert cost == 5

    def test_player_knows_warp_returns_false_when_no_knowledge_row_exists(self):
        from src.models.player_warp_knowledge import PlayerWarpKnowledge
        mock_db = _FakeSession({PlayerWarpKnowledge: _FakeQuery(first=None)})
        assert _player_knows_warp(mock_db, uuid.uuid4(), _tunnel(turn_cost=1)) is False

    def test_player_knows_warp_returns_false_when_row_exists_but_not_known(self):
        """A row can exist at a non-revealed/non-traversed visibility_state
        (is_known False) -- presence of a row alone must not grant access."""
        from src.models.player_warp_knowledge import PlayerWarpKnowledge
        row = SimpleNamespace(is_known=False)
        mock_db = _FakeSession({PlayerWarpKnowledge: _FakeQuery(first=row)})
        assert _player_knows_warp(mock_db, uuid.uuid4(), _tunnel(turn_cost=1)) is False

    def test_player_knows_warp_returns_true_when_row_is_known(self):
        from src.models.player_warp_knowledge import PlayerWarpKnowledge
        row = SimpleNamespace(is_known=True)
        mock_db = _FakeSession({PlayerWarpKnowledge: _FakeQuery(first=row)})
        assert _player_knows_warp(mock_db, uuid.uuid4(), _tunnel(turn_cost=1)) is True
