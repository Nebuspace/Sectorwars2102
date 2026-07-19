"""Unit tests for the Nexus tunnel-type vocabulary convergence
(WO-GWQ-TUNNELTYPE).

sectors.md:42-48 -- `WarpTunnel.type` has exactly two canon values, NATURAL
and ARTIFICIAL; ARTIFICIAL is reserved for the fedspace starter binding (not
stamped by the Central Nexus generator) and player-built warp gates.
Generator-placed ARTIFICIAL and NATURAL tunnels are "indistinguishable in
routing cost and stability". The prior STANDARD/QUANTUM/ANCIENT/UNSTABLE
minting and the QUANTUM/UNSTABLE 50% non-warp-capable movement surcharge
were undocumented (non-canon) and have been removed.

DB-free, mirrors the mock-session style of test_warp_cost_canon.py
(MovementService side) and the direct-route-call style of
test_admin_message_stats.py (admin_enhanced side).
"""
import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.api.routes.admin_comprehensive import (
    WarpTunnelCreateRequest,
    WarpTunnelUpdateRequest,
    create_warp_tunnel,
    update_warp_tunnel,
)
from src.api.routes.admin_enhanced import (
    WarpTunnelEnhancedRequest,
    create_enhanced_warp_tunnel,
)
from src.models.sector import Sector
from src.models.warp_tunnel import WarpTunnel, WarpTunnelStatus, WarpTunnelType
from src.services.movement_service import MovementService
from src.services.nexus_generation_service import NexusGenerationService

# ---------------------------------------------------------------------------
# Lane 1 -- generator minting + pricing
# ---------------------------------------------------------------------------


class TestGeneratorMintsOnlyCanonTypes:
    """`_choose_warp_tunnel_type` must never mint a non-canon type."""

    def test_two_hundred_rolls_are_all_natural(self):
        service = NexusGenerationService()
        rolls = {service._choose_warp_tunnel_type() for _ in range(200)}
        assert rolls == {WarpTunnelType.NATURAL}

    def test_no_roll_ever_lands_on_a_legacy_type(self):
        service = NexusGenerationService()
        legacy = {
            WarpTunnelType.STANDARD,
            WarpTunnelType.QUANTUM,
            WarpTunnelType.ANCIENT,
            WarpTunnelType.UNSTABLE,
        }
        for _ in range(200):
            assert service._choose_warp_tunnel_type() not in legacy


class TestGeneratorCostNeutrality:
    """The per-type turn-cost multiplier map is removed -- every type
    resolves to the same distance-scaled base cost."""

    def test_natural_and_artificial_cost_the_same(self):
        service = NexusGenerationService()
        distance = 37.0
        natural_cost = service._get_turn_cost_for_tunnel_type(WarpTunnelType.NATURAL, distance)
        artificial_cost = service._get_turn_cost_for_tunnel_type(WarpTunnelType.ARTIFICIAL, distance)
        assert natural_cost == artificial_cost

    def test_legacy_types_still_load_and_cost_the_same_as_natural(self):
        """The enum members must keep loading (no destructive drop) even
        though the generator no longer mints them -- and if one somehow
        reaches this helper (e.g. a legacy row re-priced), it must not get a
        different cost than NATURAL."""
        service = NexusGenerationService()
        distance = 52.0
        natural_cost = service._get_turn_cost_for_tunnel_type(WarpTunnelType.NATURAL, distance)
        for legacy_type in (
            WarpTunnelType.STANDARD,
            WarpTunnelType.QUANTUM,
            WarpTunnelType.ANCIENT,
            WarpTunnelType.UNSTABLE,
        ):
            assert service._get_turn_cost_for_tunnel_type(legacy_type, distance) == natural_cost


# ---------------------------------------------------------------------------
# Lane 2 -- movement turn-cost + event trigger
# ---------------------------------------------------------------------------


def _make_tunnel(tunnel_type, turn_cost=5, created_by_player_id=None, stability=0.9):
    return SimpleNamespace(
        id=uuid.uuid4(),
        type=tunnel_type,
        turn_cost=turn_cost,
        created_by_player_id=created_by_player_id,
        is_latent=False,
        expires_at=None,
        properties={},
        created_at=None,
        max_uses=None,
        stability=stability,
    )


def _build_movement_service(tunnel):
    """MovementService over a mock session that resolves both Sector lookups
    to a fixed stand-in and the outgoing WarpTunnel lookup to `tunnel`
    directly -- mirrors test_warp_cost_canon.py's build_service. The warp
    lookup path itself (forward/reverse fallback) is untouched by this WO."""
    mock_db = MagicMock()
    sector = SimpleNamespace(id=uuid.uuid4())

    def query_side_effect(model):
        if model is Sector:
            q = MagicMock()
            q.filter.return_value.first.return_value = sector
            return q
        if model is WarpTunnel:
            q = MagicMock()
            q.filter.return_value.first.return_value = tunnel
            return q
        raise AssertionError(f"unexpected query target: {model!r}")

    mock_db.query.side_effect = query_side_effect
    return MovementService(mock_db)


class TestMovementCostTypeIndifferent:
    """Turn cost must be identical for NATURAL vs (generator-placed)
    ARTIFICIAL tunnels of equal base cost, for the same ship -- routing cost
    is type-independent per sectors.md:47."""

    def test_natural_and_artificial_cost_the_same_for_a_warp_capable_ship(self):
        ship = SimpleNamespace(warp_capable=True)

        natural_tunnel = _make_tunnel(WarpTunnelType.NATURAL, turn_cost=5)
        artificial_tunnel = _make_tunnel(WarpTunnelType.ARTIFICIAL, turn_cost=5, created_by_player_id=None)

        natural_service = _build_movement_service(natural_tunnel)
        artificial_service = _build_movement_service(artificial_tunnel)

        ok_n, cost_n, _ = natural_service._check_warp_tunnel(1, 2, ship)
        ok_a, cost_a, _ = artificial_service._check_warp_tunnel(1, 2, ship)

        assert ok_n and ok_a
        assert cost_n == cost_a == 4  # max(1, int(5 * 0.8)) -- ship-based reduction, not type-based


class TestLegacyTypesNoSurcharge:
    """A legacy QUANTUM/UNSTABLE row must still traverse (enum members keep
    loading) and must NOT pay the removed 50% non-warp-capable surcharge --
    same turns as a NATURAL row of equal base cost."""

    @pytest.mark.parametrize("legacy_type", [WarpTunnelType.QUANTUM, WarpTunnelType.UNSTABLE])
    def test_legacy_row_costs_the_same_as_natural_for_non_warp_capable_ship(self, legacy_type):
        ship = SimpleNamespace(warp_capable=False)

        natural_tunnel = _make_tunnel(WarpTunnelType.NATURAL, turn_cost=5)
        legacy_tunnel = _make_tunnel(legacy_type, turn_cost=5)

        natural_service = _build_movement_service(natural_tunnel)
        legacy_service = _build_movement_service(legacy_tunnel)

        ok_n, cost_n, _ = natural_service._check_warp_tunnel(1, 2, ship)
        ok_l, cost_l, _ = legacy_service._check_warp_tunnel(1, 2, ship)

        assert ok_n and ok_l
        # Pre-WO this would have been 7 (max(1, int(5 * 1.5))) for the legacy type.
        assert cost_n == cost_l == 5


class TestSpacetimeAnomalyReKeyed:
    """`_check_for_tunnel_events` must no longer gate spacetime_anomaly on
    tunnel.type -- any sufficiently unstable tunnel qualifies
    (sector-presence.md:122-123)."""

    def _build_events_service(self, tunnel):
        mock_db = MagicMock()
        from_sector = SimpleNamespace(id=uuid.uuid4())
        to_sector = SimpleNamespace(id=uuid.uuid4())

        def query_side_effect(model):
            if model is Sector:
                q = MagicMock()
                q.filter.return_value.first.side_effect = [from_sector, to_sector]
                return q
            if model is WarpTunnel:
                q = MagicMock()
                q.filter.return_value.first.return_value = tunnel
                return q
            raise AssertionError(f"unexpected query target: {model!r}")

        mock_db.query.side_effect = query_side_effect
        return MovementService(mock_db)

    def test_natural_tunnel_below_0_5_stability_still_fires_spacetime_anomaly(self):
        """Previously only QUANTUM/UNSTABLE could trigger this event; a
        NATURAL tunnel at the same instability must now trigger it too."""
        tunnel = _make_tunnel(WarpTunnelType.NATURAL, stability=0.4)
        service = self._build_events_service(tunnel)

        events = service._check_for_tunnel_events(player=SimpleNamespace(), from_sector_id=1, to_sector_id=2)

        types = {e["type"] for e in events}
        assert "spacetime_anomaly" in types
        assert "radiation_exposure" in types

    def test_natural_tunnel_between_0_5_and_0_7_stability_does_not_fire_either_event(self):
        tunnel = _make_tunnel(WarpTunnelType.NATURAL, stability=0.6)
        service = self._build_events_service(tunnel)

        events = service._check_for_tunnel_events(player=SimpleNamespace(), from_sector_id=1, to_sector_id=2)

        types = {e["type"] for e in events}
        assert "spacetime_anomaly" not in types
        assert "radiation_exposure" not in types


# ---------------------------------------------------------------------------
# Lane 3 -- admin route
# ---------------------------------------------------------------------------


def _admin_user():
    """These routes are RBAC-E5-wrapped (admin_action_attempt), which reads
    actor.id on both the succeed() and fail() paths -- a bare SimpleNamespace()
    (fine when the routes were is_admin-only) now AttributeErrors on log."""
    return SimpleNamespace(id=uuid.uuid4(), username="admin", is_admin=True)


def _build_admin_db(source, target, existing_tunnel=None):
    mock_db = MagicMock()
    sector_returns = iter([source, target])

    def query_side_effect(model):
        if model is Sector:
            q = MagicMock()
            q.filter.return_value.first.side_effect = lambda: next(sector_returns)
            return q
        if model is WarpTunnel:
            q = MagicMock()
            q.filter.return_value.first.return_value = existing_tunnel
            return q
        raise AssertionError(f"unexpected query target: {model!r}")

    mock_db.query.side_effect = query_side_effect
    return mock_db


def _make_request(tunnel_type):
    return WarpTunnelEnhancedRequest(
        source_sector_id=1,
        target_sector_id=2,
        tunnel_type=tunnel_type,
        is_one_way=False,
        stability=90,
        turn_cost=1,
        access_control="public",
        toll_amount=None,
    )


class TestAdminCreateRestrictsToCanonTypes:
    @pytest.mark.parametrize("tunnel_type", ["natural", "artificial", "NATURAL", "Artificial"])
    def test_accepts_canon_types_case_insensitively(self, tunnel_type):
        source = SimpleNamespace(id=uuid.uuid4(), name="Alpha", has_warp_tunnel=False)
        target = SimpleNamespace(id=uuid.uuid4(), name="Beta", has_warp_tunnel=False)
        db = _build_admin_db(source, target)

        result = asyncio.run(
            create_enhanced_warp_tunnel(
                _make_request(tunnel_type), current_admin=_admin_user(), db=db
            )
        )

        assert result["created"] is True

    @pytest.mark.parametrize("tunnel_type", ["quantum", "unstable", "ancient", "standard", "warp_hole"])
    def test_rejects_non_canon_types_with_400(self, tunnel_type):
        source = SimpleNamespace(id=uuid.uuid4(), name="Alpha", has_warp_tunnel=False)
        target = SimpleNamespace(id=uuid.uuid4(), name="Beta", has_warp_tunnel=False)
        db = _build_admin_db(source, target)

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                create_enhanced_warp_tunnel(
                    _make_request(tunnel_type), current_admin=_admin_user(), db=db
                )
            )

        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Lane 4 -- admin_comprehensive.py's sector-scoped create + standalone update
# routes (WO-GWQ-TUNNELTYPE micro-lane: these bare WarpTunnelType[value.upper()]
# lookups had no canon-membership restriction, unlike admin_enhanced.py above).
# ---------------------------------------------------------------------------


def _make_create_request(tunnel_type):
    return WarpTunnelCreateRequest(
        name="Test Tunnel",
        destination_sector_id=2,
        type=tunnel_type,
        is_bidirectional=True,
        turn_cost=5,
        stability=1.0,
        is_public=True,
    )


class TestAdminComprehensiveCreateRestrictsToCanonTypes:
    @pytest.mark.parametrize("tunnel_type", ["natural", "artificial", "NATURAL", "Artificial"])
    def test_accepts_canon_types_case_insensitively(self, tunnel_type):
        origin = SimpleNamespace(id=uuid.uuid4(), sector_id=1, region_id=uuid.uuid4())
        dest = SimpleNamespace(id=uuid.uuid4(), sector_id=2, region_id=origin.region_id)
        db = _build_admin_db(origin, dest)

        result = asyncio.run(
            create_warp_tunnel(
                str(origin.id), _make_create_request(tunnel_type), current_admin=_admin_user(), db=db
            )
        )

        assert result["success"] is True
        assert result["tunnel"]["type"] == tunnel_type.upper()

    @pytest.mark.parametrize("tunnel_type", ["quantum", "unstable", "ancient", "standard", "warp_hole"])
    def test_rejects_non_canon_types_with_400(self, tunnel_type):
        origin = SimpleNamespace(id=uuid.uuid4(), sector_id=1, region_id=uuid.uuid4())
        dest = SimpleNamespace(id=uuid.uuid4(), sector_id=2, region_id=origin.region_id)
        db = _build_admin_db(origin, dest)

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                create_warp_tunnel(
                    str(origin.id), _make_create_request(tunnel_type), current_admin=_admin_user(), db=db
                )
            )

        assert exc_info.value.status_code == 400


def _build_update_db(tunnel):
    mock_db = MagicMock()

    def query_side_effect(model):
        if model is WarpTunnel:
            q = MagicMock()
            q.filter.return_value.first.return_value = tunnel
            return q
        raise AssertionError(f"unexpected query target: {model!r}")

    mock_db.query.side_effect = query_side_effect
    return mock_db


def _make_existing_tunnel(tunnel_type=WarpTunnelType.NATURAL):
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Existing Tunnel",
        type=tunnel_type,
        status=WarpTunnelStatus.ACTIVE,
        is_bidirectional=True,
        turn_cost=5,
        stability=0.9,
    )


class TestAdminComprehensiveUpdateRestrictsToCanonTypes:
    @pytest.mark.parametrize("tunnel_type", ["natural", "artificial", "NATURAL", "Artificial"])
    def test_accepts_canon_types_case_insensitively(self, tunnel_type):
        tunnel = _make_existing_tunnel()
        db = _build_update_db(tunnel)

        result = asyncio.run(
            update_warp_tunnel(
                str(tunnel.id),
                WarpTunnelUpdateRequest(type=tunnel_type),
                current_admin=_admin_user(),
                db=db,
            )
        )

        assert result["success"] is True
        assert result["tunnel"]["type"] == tunnel_type.upper()

    @pytest.mark.parametrize("tunnel_type", ["quantum", "unstable", "ancient", "standard", "warp_hole"])
    def test_rejects_non_canon_types_with_400(self, tunnel_type):
        tunnel = _make_existing_tunnel()
        db = _build_update_db(tunnel)

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                update_warp_tunnel(
                    str(tunnel.id),
                    WarpTunnelUpdateRequest(type=tunnel_type),
                    current_admin=_admin_user(),
                    db=db,
                )
            )

        assert exc_info.value.status_code == 400
        # The tunnel must be left untouched on rejection -- no partial mutation.
        assert tunnel.type == WarpTunnelType.NATURAL

    def test_legacy_row_resubmitting_its_own_type_unchanged_is_accepted(self):
        """The admin edit form resubmits `tunnel.type` verbatim; editing e.g.
        stability on a legacy QUANTUM row must not 400 just because QUANTUM
        itself isn't canon -- a same-value passthrough is not a mint."""
        tunnel = _make_existing_tunnel(tunnel_type=WarpTunnelType.QUANTUM)
        db = _build_update_db(tunnel)

        result = asyncio.run(
            update_warp_tunnel(
                str(tunnel.id),
                WarpTunnelUpdateRequest(type="quantum"),
                current_admin=_admin_user(),
                db=db,
            )
        )

        assert result["success"] is True
        assert result["tunnel"]["type"] == "QUANTUM"
        assert tunnel.type == WarpTunnelType.QUANTUM

    def test_legacy_to_legacy_transition_is_rejected(self):
        """QUANTUM -> UNSTABLE is an actual transition (not a passthrough) to
        a non-canon value and must still 400."""
        tunnel = _make_existing_tunnel(tunnel_type=WarpTunnelType.QUANTUM)
        db = _build_update_db(tunnel)

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                update_warp_tunnel(
                    str(tunnel.id),
                    WarpTunnelUpdateRequest(type="unstable"),
                    current_admin=_admin_user(),
                    db=db,
                )
            )

        assert exc_info.value.status_code == 400
        assert tunnel.type == WarpTunnelType.QUANTUM

    def test_legacy_to_canon_transition_is_accepted(self):
        """QUANTUM -> NATURAL is a transition onto a canon value and must be
        accepted -- this is how a legacy row gets migrated forward."""
        tunnel = _make_existing_tunnel(tunnel_type=WarpTunnelType.QUANTUM)
        db = _build_update_db(tunnel)

        result = asyncio.run(
            update_warp_tunnel(
                str(tunnel.id),
                WarpTunnelUpdateRequest(type="natural"),
                current_admin=_admin_user(),
                db=db,
            )
        )

        assert result["success"] is True
        assert result["tunnel"]["type"] == "NATURAL"
        assert tunnel.type == WarpTunnelType.NATURAL
