"""LEG-1093 — kill −2% + defend +1% Dynamic sector influence helpers.

Canon: sw2102-docs FEATURES/gameplay/factions-and-teams.md Dynamic influence table.
Fair-tariff +2% is out of scope (separate tip-land WO).
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

from src.services.faction_service import (
    DEFEND_SECTOR_INFLUENCE_DELTA,
    RIVAL_KILL_INFLUENCE_DELTA,
    apply_defense_survived_sector_influence,
    apply_rival_kill_sector_influence,
)


class TestCanonDeltas:
    def test_rival_kill_delta_is_minus_two(self):
        assert RIVAL_KILL_INFLUENCE_DELTA == -2.0

    def test_defend_delta_is_plus_one(self):
        assert DEFEND_SECTOR_INFLUENCE_DELTA == 1.0


class TestApplyRivalKillSectorInfluence:
    @patch("src.services.faction_service.adjust_sector_influence")
    @patch("src.services.faction_service.dominant_reputation_faction_id")
    def test_applies_minus_two_to_victim_faction_when_rival(
        self, mock_dominant, mock_adjust
    ):
        db = MagicMock()
        sector_id = uuid4()
        killer_id, victim_id = uuid4(), uuid4()
        killer_faction, victim_faction = uuid4(), uuid4()
        mock_dominant.side_effect = [killer_faction, victim_faction]

        apply_rival_kill_sector_influence(db, sector_id, killer_id, victim_id)

        mock_adjust.assert_called_once_with(
            db, sector_id, victim_faction, RIVAL_KILL_INFLUENCE_DELTA
        )

    @patch("src.services.faction_service.adjust_sector_influence")
    @patch("src.services.faction_service.dominant_reputation_faction_id")
    def test_same_faction_is_noop(self, mock_dominant, mock_adjust):
        db = MagicMock()
        shared = uuid4()
        mock_dominant.side_effect = [shared, shared]

        apply_rival_kill_sector_influence(db, uuid4(), uuid4(), uuid4())

        mock_adjust.assert_not_called()

    @patch("src.services.faction_service.adjust_sector_influence")
    def test_missing_sector_is_noop(self, mock_adjust):
        apply_rival_kill_sector_influence(MagicMock(), None, uuid4(), uuid4())
        mock_adjust.assert_not_called()


class TestApplyDefenseSurvivedSectorInfluence:
    @patch("src.services.faction_service.adjust_sector_influence")
    def test_applies_plus_one_for_defended_faction(self, mock_adjust):
        db = MagicMock()
        sector_id, faction_id = uuid4(), uuid4()

        apply_defense_survived_sector_influence(db, sector_id, faction_id)

        mock_adjust.assert_called_once_with(
            db, sector_id, faction_id, DEFEND_SECTOR_INFLUENCE_DELTA
        )

    @patch("src.services.faction_service.adjust_sector_influence")
    def test_missing_faction_is_noop(self, mock_adjust):
        apply_defense_survived_sector_influence(MagicMock(), uuid4(), None)
        mock_adjust.assert_not_called()
