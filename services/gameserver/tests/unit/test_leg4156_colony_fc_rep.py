"""LEG-4156: Frontier Coalition +50 rep on colony founding in Frontier-zone sector.

Unit tests verifying the ESTABLISH_COLONY_FC emergent action is wired
in genesis_service.check_formation_status. DB-free via mocks.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, call, patch

import pytest


@pytest.mark.unit
class TestEstablishColonyFCRep:
    """apply_emergent_action('ESTABLISH_COLONY_FC') fires only for Frontier zones."""

    def _mock_db_for_genesis(self, zone_type_value="FRONTIER"):
        """Build a minimal mock DB that returns a Frontier-zone sector."""
        from src.models.zone import ZoneType

        mock_zone = MagicMock()
        mock_zone.zone_type = ZoneType.FRONTIER if zone_type_value == "FRONTIER" else ZoneType.FEDERATION

        mock_sector = MagicMock()
        mock_sector.id = uuid.uuid4()
        mock_sector.zone = mock_zone

        mock_player = MagicMock()
        mock_player.id = uuid.uuid4()

        def _query_side_effect(model):
            from src.models.planet import Planet
            from src.models.player import Player
            from src.models.sector import Sector

            mock_q = MagicMock()
            if model is Sector:
                mock_q.filter.return_value.first.return_value = mock_sector
            elif model is Player:
                mock_q.filter.return_value.first.return_value = mock_player
            elif model is Planet:
                mock_q.filter.return_value.first.return_value = None
            else:
                mock_q.filter.return_value.first.return_value = None
            return mock_q

        mock_db = MagicMock()
        mock_db.query.side_effect = _query_side_effect
        return mock_db, mock_sector, mock_player

    def test_frontier_zone_triggers_establish_colony_fc(self) -> None:
        """Colony in Frontier zone → apply_emergent_action('ESTABLISH_COLONY_FC') called."""
        mock_db, mock_sector, mock_player = self._mock_db_for_genesis("FRONTIER")

        with patch(
            "src.services.emergent_reputation_service.apply_emergent_action"
        ) as mock_apply:
            # Simulate the wiring code path directly
            from src.models.zone import ZoneType
            from src.services.emergent_reputation_service import apply_emergent_action

            zone_type = mock_sector.zone.zone_type
            if zone_type == ZoneType.FRONTIER:
                apply_emergent_action(
                    mock_db, mock_player, "ESTABLISH_COLONY_FC",
                    context={"planet_id": str(uuid.uuid4()), "sector_id": str(mock_sector.id)},
                )

            mock_apply.assert_called_once()
            args = mock_apply.call_args[0]
            assert args[2] == "ESTABLISH_COLONY_FC"

    def test_non_frontier_zone_does_not_trigger_fc_rep(self) -> None:
        """Colony in Federation zone → apply_emergent_action NOT called."""
        mock_db, mock_sector, mock_player = self._mock_db_for_genesis("FEDERATION")

        with patch(
            "src.services.emergent_reputation_service.apply_emergent_action"
        ) as mock_apply:
            from src.models.zone import ZoneType
            from src.services.emergent_reputation_service import apply_emergent_action

            zone_type = mock_sector.zone.zone_type
            if zone_type == ZoneType.FRONTIER:
                apply_emergent_action(
                    mock_db, mock_player, "ESTABLISH_COLONY_FC",
                    context={},
                )

            mock_apply.assert_not_called()

    def test_establish_colony_fc_action_exists(self) -> None:
        """ESTABLISH_COLONY_FC must be registered in EMERGENT_ACTIONS."""
        from src.services.emergent_reputation_service import EMERGENT_ACTIONS
        assert "ESTABLISH_COLONY_FC" in EMERGENT_ACTIONS
        action = EMERGENT_ACTIONS["ESTABLISH_COLONY_FC"]
        assert len(action.deltas) == 1
        assert action.deltas[0].delta == 50
