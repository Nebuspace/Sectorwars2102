"""DB-free unit coverage for auth fix (b): the ADR-0043 / SYSTEMS/region-
lifecycle.md:655 Galactic Citizen subscription gate on Region<->Nexus
natural-warp traversal (MovementService._check_nexus_subscription_gate).

Per the codebase's mock-only unit-test convention (see test_movement_core_
pins.py's _FakeQuery/_FakeSession, test_movement_region_sync.py's
SimpleNamespace stand-ins), this file keeps its own self-contained harness
(no test-file-to-test-file import). ``_FakeQuery`` supports an optional
``seq`` list (mirrors test_trading_core_pins.py's proven sequencing
convention, reused verbatim in test_aria_trade_hooks.py) since the gate
queries ``Region`` TWICE in the rejection/pass path (origin, then
destination) with DIFFERENT expected rows -- a single canned ``first=``
value can't distinguish them.

Two layers of proof, matching test_movement_core_pins.py's own structural-
pin convention for a hook that's expensive to drive fully end-to-end:
  (1) TestNexusSubscriptionGateLogic -- direct, exhaustive tests of
      _check_nexus_subscription_gate in isolation (all four quadrants:
      entering/leaving Nexus x subscribed/unsubscribed, plus intra-Nexus
      and non-Nexus-to-non-Nexus no-ops).
  (2) TestGateWiringStructural -- an AST/source pin proving the gate is
      called EXACTLY ONCE in move_player_to_sector, and that call site
      precedes all three _execute_movement call sites (player-gate /
      direct-warp / natural-tunnel) -- so no movement path can bypass it.
  (3) TestEndToEndRejection -- one true end-to-end call through
      move_player_to_sector proving the wiring actually rejects before
      any turn is spent (not just that the pieces are individually
      correct).
"""
from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace

from src.models.region import Region
from src.models.sector import Sector
from src.services.movement_service import MovementService

# --------------------------------------------------------------------------- #
# Fake session -- model-dispatched, with optional per-model call sequencing.
# --------------------------------------------------------------------------- #

class _FakeQuery:
    def __init__(self, *, first=None, seq=None):
        self._first = first
        self._seq = list(seq) if seq is not None else None

    def filter(self, *a, **k):
        return self

    def with_for_update(self, *a, **k):
        return self

    def first(self):
        if self._seq is not None:
            return self._seq.pop(0) if self._seq else None
        return self._first


class _FakeSession:
    def __init__(self, specs):
        self._specs = specs

    def query(self, model):
        assert model in self._specs, f"unexpected query for {model!r}"
        return self._specs[model]


def _region(*, is_central_nexus: bool) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), is_central_nexus=is_central_nexus)


def _sector(*, sector_id: int, region_id) -> SimpleNamespace:
    return SimpleNamespace(sector_id=sector_id, region_id=region_id)


def _player(*, current_region_id) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), current_region_id=current_region_id)


# --------------------------------------------------------------------------- #
# (1) Direct gate logic -- all four quadrants + edge cases.
# --------------------------------------------------------------------------- #

class TestNexusSubscriptionGateLogic:
    def test_unsubscribed_entering_nexus_is_rejected(self, monkeypatch):
        origin_region = _region(is_central_nexus=False)
        nexus_region = _region(is_central_nexus=True)
        destination_sector = _sector(sector_id=1301, region_id=nexus_region.id)
        player = _player(current_region_id=origin_region.id)

        db = _FakeSession({
            Region: _FakeQuery(seq=[origin_region, nexus_region]),
            Sector: _FakeQuery(first=destination_sector),
        })
        monkeypatch.setattr(
            "src.services.ship_upgrade_service.is_galactic_citizen",
            lambda db, player: False,
        )

        service = MovementService(db)
        result = service._check_nexus_subscription_gate(player, 1301)

        assert result is not None
        assert result["success"] is False
        assert result["message"] == "ERR_GALACTIC_CITIZEN_REQUIRED"
        assert result["error"] == "ERR_GALACTIC_CITIZEN_REQUIRED"
        assert result["turn_cost"] == 0

    def test_subscribed_entering_nexus_passes(self, monkeypatch):
        origin_region = _region(is_central_nexus=False)
        nexus_region = _region(is_central_nexus=True)
        destination_sector = _sector(sector_id=1301, region_id=nexus_region.id)
        player = _player(current_region_id=origin_region.id)

        db = _FakeSession({
            Region: _FakeQuery(seq=[origin_region, nexus_region]),
            Sector: _FakeQuery(first=destination_sector),
        })
        monkeypatch.setattr(
            "src.services.ship_upgrade_service.is_galactic_citizen",
            lambda db, player: True,
        )

        service = MovementService(db)
        result = service._check_nexus_subscription_gate(player, 1301)

        assert result is None  # pass -- move proceeds unchanged

    def test_non_nexus_to_non_nexus_movement_is_completely_unaffected(self, monkeypatch):
        """Regression: ordinary in-region/cross-region (non-Nexus) movement
        must never consult subscription status at all -- even an
        unsubscribed player moves freely between two non-Nexus regions."""
        origin_region = _region(is_central_nexus=False)
        other_region = _region(is_central_nexus=False)
        destination_sector = _sector(sector_id=1301, region_id=other_region.id)
        player = _player(current_region_id=origin_region.id)

        db = _FakeSession({
            Region: _FakeQuery(seq=[origin_region, other_region]),
            Sector: _FakeQuery(first=destination_sector),
        })
        # If the gate incorrectly consulted subscription status here, this
        # would make the test fail loudly (raises) rather than silently
        # passing for the wrong reason.
        monkeypatch.setattr(
            "src.services.ship_upgrade_service.is_galactic_citizen",
            lambda db, player: (_ for _ in ()).throw(AssertionError("must not be called")),
        )

        service = MovementService(db)
        result = service._check_nexus_subscription_gate(player, 1301)

        assert result is None

    def test_leaving_nexus_is_always_allowed_even_when_unsubscribed(self, monkeypatch):
        """[NO-CANON] directionality: an unsubscribed player already inside
        Nexus must always be able to leave -- never stranded."""
        nexus_region = _region(is_central_nexus=True)
        destination_region = _region(is_central_nexus=False)
        player = _player(current_region_id=nexus_region.id)

        db = _FakeSession({
            Region: _FakeQuery(seq=[nexus_region]),  # only the ORIGIN lookup fires
        })
        monkeypatch.setattr(
            "src.services.ship_upgrade_service.is_galactic_citizen",
            lambda db, player: (_ for _ in ()).throw(AssertionError("must not be called")),
        )

        service = MovementService(db)
        result = service._check_nexus_subscription_gate(player, 2001)

        assert result is None
        assert destination_region  # constructed but intentionally unused by the SUT here

    def test_intra_nexus_movement_is_never_gated(self, monkeypatch):
        """Moving between two sectors that are BOTH inside Nexus is not an
        entry -- must never gate, regardless of subscription status."""
        nexus_region = _region(is_central_nexus=True)
        player = _player(current_region_id=nexus_region.id)

        db = _FakeSession({
            Region: _FakeQuery(seq=[nexus_region]),  # only the ORIGIN lookup fires
        })
        monkeypatch.setattr(
            "src.services.ship_upgrade_service.is_galactic_citizen",
            lambda db, player: (_ for _ in ()).throw(AssertionError("must not be called")),
        )

        service = MovementService(db)
        result = service._check_nexus_subscription_gate(player, 2050)

        assert result is None

    def test_player_with_no_origin_region_still_gates_on_destination(self, monkeypatch):
        """A player with current_region_id=None (edge case, e.g. a stale/
        unsynced row) must still be gated on the DESTINATION -- absence of
        an origin region must never fail the gate open by accident."""
        nexus_region = _region(is_central_nexus=True)
        destination_sector = _sector(sector_id=1301, region_id=nexus_region.id)
        player = _player(current_region_id=None)

        db = _FakeSession({
            Region: _FakeQuery(seq=[nexus_region]),  # only the DESTINATION lookup fires
            Sector: _FakeQuery(first=destination_sector),
        })
        monkeypatch.setattr(
            "src.services.ship_upgrade_service.is_galactic_citizen",
            lambda db, player: False,
        )

        service = MovementService(db)
        result = service._check_nexus_subscription_gate(player, 1301)

        assert result is not None
        assert result["message"] == "ERR_GALACTIC_CITIZEN_REQUIRED"

    def test_unknown_destination_sector_fails_open_not_gated(self, monkeypatch):
        """A destination sector this service can't resolve (None) is caught
        elsewhere (move_player_to_sector's own "No valid path" branch) --
        the gate itself must not raise or block on missing data."""
        origin_region = _region(is_central_nexus=False)
        player = _player(current_region_id=origin_region.id)

        db = _FakeSession({
            Region: _FakeQuery(seq=[origin_region]),
            Sector: _FakeQuery(first=None),
        })
        monkeypatch.setattr(
            "src.services.ship_upgrade_service.is_galactic_citizen",
            lambda db, player: (_ for _ in ()).throw(AssertionError("must not be called")),
        )

        service = MovementService(db)
        result = service._check_nexus_subscription_gate(player, 9999)

        assert result is None


# --------------------------------------------------------------------------- #
# (2) Structural pin -- the gate can't be bypassed by an alternate path.
# --------------------------------------------------------------------------- #

class TestGateWiringStructural:
    def test_gate_is_called_exactly_once_in_move_player_to_sector(self):
        source = inspect.getsource(MovementService.move_player_to_sector)
        assert source.count("_check_nexus_subscription_gate(") == 1

    def test_gate_call_precedes_all_three_execute_movement_call_sites(self):
        """The player-gate / direct-warp / natural-tunnel branches are the
        ONLY three ways move_player_to_sector reaches _execute_movement
        (test_movement_core_pins.py's TestExecuteMovementWiring pins that
        count at exactly 3) -- proving the gate's source position precedes
        all three proves no branch can execute a move before the gate ran."""
        source = inspect.getsource(MovementService.move_player_to_sector)
        gate_pos = source.index("_check_nexus_subscription_gate(")

        for branch_call in [
            "self._execute_movement(player, destination_sector_id, gate_cost)",
            "self._execute_movement(player, destination_sector_id, warp_cost)",
            "self._execute_movement(player, destination_sector_id, tunnel_cost)",
        ]:
            assert gate_pos < source.index(branch_call), (
                f"nexus gate must precede {branch_call!r}"
            )

    def test_gate_return_value_is_returned_immediately_when_rejecting(self):
        """The wiring must be `if nexus_gate_rejection is not None: return
        nexus_gate_rejection` -- not merely called and ignored."""
        source = inspect.getsource(MovementService.move_player_to_sector)
        assert "if nexus_gate_rejection is not None:" in source
        assert "return nexus_gate_rejection" in source


# --------------------------------------------------------------------------- #
# (3) End-to-end: one true call through move_player_to_sector proving the
# wiring rejects before any turn is spent.
# --------------------------------------------------------------------------- #

class TestEndToEndRejection:
    def test_unsubscribed_player_move_into_nexus_is_rejected_before_any_turn_spend(
        self, monkeypatch,
    ):
        from src.models.player import Player
        from src.models.warp_tunnel import WarpTunnel

        origin_region = _region(is_central_nexus=False)
        nexus_region = _region(is_central_nexus=True)
        destination_sector = _sector(sector_id=1301, region_id=nexus_region.id)

        ship = SimpleNamespace(
            status=None, hangar=None, tow_state=None,
        )
        player = SimpleNamespace(
            id=uuid.uuid4(),
            current_sector_id=1001,
            current_region_id=origin_region.id,
            current_ship_id=uuid.uuid4(),
            current_ship=ship,
            is_docked=False,
            is_landed=False,
            turns=100,
            lifetime_turns_spent=0,
            last_turn_regeneration=None,
            created_at=None,
            max_turns=1000,
            aria_bonus_multiplier=1.0,
        )

        db = _FakeSession({
            Player: _FakeQuery(first=player),
            Region: _FakeQuery(seq=[origin_region, nexus_region]),
            Sector: _FakeQuery(first=destination_sector),
            WarpTunnel: _FakeQuery(first=None),
        })

        monkeypatch.setattr(
            "src.services.ship_upgrade_service.is_galactic_citizen",
            lambda db, player: False,
        )
        # warp_gate_service.advance_gates_touching_sector is called (best-
        # effort, wrapped in try/except) on the pre-lock player fetch --
        # stub it to a no-op so this test stays scoped to the gate itself.
        monkeypatch.setattr(
            "src.services.movement_service.warp_gate_service.advance_gates_touching_sector",
            lambda db, sector_id: None,
        )
        monkeypatch.setattr(
            "src.services.movement_service.MovementService._has_player_gate",
            lambda self, origin, dest: None,
        )

        service = MovementService(db)
        result = service.move_player_to_sector(player.id, 1301)

        assert result["success"] is False
        assert result["message"] == "ERR_GALACTIC_CITIZEN_REQUIRED"
        assert result["turn_cost"] == 0
        assert player.turns == 100  # untouched -- no turn was spent
