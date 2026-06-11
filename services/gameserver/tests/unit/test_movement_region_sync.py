"""Unit tests for MovementService._execute_movement region synchronization.

Warp tunnels can cross regions; before the region sync, _execute_movement
updated current_sector_id but never current_region_id, leaving the player's
region stale and making region-filtered routes (e.g. /player/current-sector)
404 after any cross-region jump.

Mirrors the mock-session style of sibling unit tests (test_central_nexus.py):
a MagicMock stands in for the SQLAlchemy session and SimpleNamespace
stand-ins carry the attributes the method reads — no DB required.
"""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.services.movement_service import MovementService


def make_player(current_sector_id=1001, current_region_id=None):
    """Lightweight stand-in carrying the attributes _execute_movement touches."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        current_sector_id=current_sector_id,
        current_region_id=current_region_id,
        is_docked=True,
        is_landed=False,
        current_port_id=uuid.uuid4(),
        current_planet_id=None,
        current_ship=SimpleNamespace(sector_id=current_sector_id),
        turns=100,
        aria_total_interactions=0,
        aria_consciousness_level=1,
        aria_bonus_multiplier=1.0,
    )


def make_sector(sector_id=1301, region_id=None, name="Nexus Gate"):
    """Destination sector stand-in with the fields sector_info reads."""
    return SimpleNamespace(
        sector_id=sector_id,
        name=name,
        type=SimpleNamespace(name="STANDARD"),
        hazard_level=0,
        radiation_level=0.0,
        region_id=region_id,
    )


@pytest.fixture
def mock_db():
    """Mock session whose Sector query returns a configurable destination."""
    return MagicMock()


def build_service(mock_db, destination_sector):
    """MovementService over the mock session, presence bookkeeping stubbed."""
    mock_db.query.return_value.filter.return_value.first.return_value = destination_sector
    service = MovementService(mock_db)
    service._update_player_presence = MagicMock()
    return service


class TestExecuteMovementRegionSync:
    def test_syncs_region_when_destination_region_differs(self, mock_db):
        """Cross-region tunnel jump must rewrite current_region_id."""
        origin_region = uuid.uuid4()
        destination_region = uuid.uuid4()
        player = make_player(current_sector_id=1001, current_region_id=origin_region)
        destination = make_sector(sector_id=1301, region_id=destination_region)
        service = build_service(mock_db, destination)

        result = service._execute_movement(player, 1301, turn_cost=1)

        assert result["success"] is True
        assert player.current_sector_id == 1301
        assert player.current_region_id == destination_region
        mock_db.commit.assert_called_once()

    def test_syncs_region_from_none_to_region(self, mock_db):
        """A player with no region (null-region branch) picks up the destination's."""
        destination_region = uuid.uuid4()
        player = make_player(current_region_id=None)
        destination = make_sector(region_id=destination_region)
        service = build_service(mock_db, destination)

        service._execute_movement(player, destination.sector_id, turn_cost=1)

        assert player.current_region_id == destination_region

    def test_syncs_region_to_none_for_null_region_destination(self, mock_db):
        """Destination sectors without a region clear current_region_id."""
        player = make_player(current_region_id=uuid.uuid4())
        destination = make_sector(region_id=None)
        service = build_service(mock_db, destination)

        service._execute_movement(player, destination.sector_id, turn_cost=1)

        assert player.current_region_id is None

    def test_destination_sector_fetched_once_and_reused(self, mock_db):
        """The hoisted fetch serves both the region sync and sector_info —
        the duplicate post-commit query was removed."""
        destination_region = uuid.uuid4()
        player = make_player(current_region_id=uuid.uuid4())
        destination = make_sector(sector_id=1301, region_id=destination_region)
        service = build_service(mock_db, destination)

        result = service._execute_movement(player, 1301, turn_cost=2)

        assert mock_db.query.call_count == 1
        assert result["sector"]["id"] == 1301
        assert result["sector"]["name"] == "Nexus Gate"
        assert result["turn_cost"] == 2
        assert player.turns == 98
        # Presence bookkeeping still receives old -> new sector ids
        service._update_player_presence.assert_called_once_with(player, 1001, 1301)
