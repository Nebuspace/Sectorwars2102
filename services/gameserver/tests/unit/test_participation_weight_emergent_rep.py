"""LEG-3193: emergent faction-rep awards respect participation_weight."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.services.emergent_reputation_service import EmergentReputationService


def _player() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        settings={},
        return_boost_until=None,
    )


@pytest.mark.unit
class TestEmergentRepParticipationWeight:
    def test_hard_flagged_player_gains_zero_rep(self) -> None:
        player = _player()
        db = MagicMock()
        with patch(
            "src.services.emergent_reputation_service.participation_weight",
            return_value=0.0,
        ), patch(
            "src.services.emergent_reputation_service._apply_combined_rep_cap",
            side_effect=lambda _db, _pid, _fac, change: change,
        ), patch(
            "src.services.emergent_reputation_service._store_throttle_bucket",
        ), patch(
            "src.services.emergent_reputation_service.apply_faction_rep_delta",
        ) as rep_delta:
            result = EmergentReputationService(db).apply_emergent_action(
                player, "KILL_PIRATE_NPC", {}
            )
        rep_delta.assert_not_called()
        assert result["success"] is True
        assert result["applied"][0]["delta"] == 0
        assert result["applied"][0]["throttled"] == "participation_weight"

    def test_soft_flagged_player_gains_half_rep(self) -> None:
        player = _player()
        db = MagicMock()
        with patch(
            "src.services.emergent_reputation_service.participation_weight",
            return_value=0.5,
        ), patch(
            "src.services.emergent_reputation_service._apply_combined_rep_cap",
            side_effect=lambda _db, _pid, _fac, change: change,
        ), patch(
            "src.services.emergent_reputation_service._store_throttle_bucket",
        ), patch(
            "src.services.emergent_reputation_service.apply_faction_rep_delta",
            return_value=MagicMock(),
        ) as rep_delta:
            result = EmergentReputationService(db).apply_emergent_action(
                player, "KILL_PIRATE_NPC", {}
            )
        rep_delta.assert_called_once()
        assert rep_delta.call_args[0][3] == 2  # +5 * 0.5 → 2
        assert result["applied"][0]["delta"] == 2
        assert result["applied"][0]["participation_weight"] == 0.5

    def test_clean_player_unchanged(self) -> None:
        player = _player()
        db = MagicMock()
        with patch(
            "src.services.emergent_reputation_service.participation_weight",
            return_value=1.0,
        ), patch(
            "src.services.emergent_reputation_service._apply_combined_rep_cap",
            side_effect=lambda _db, _pid, _fac, change: change,
        ), patch(
            "src.services.emergent_reputation_service._store_throttle_bucket",
        ), patch(
            "src.services.emergent_reputation_service.apply_faction_rep_delta",
            return_value=MagicMock(),
        ) as rep_delta:
            EmergentReputationService(db).apply_emergent_action(
                player, "KILL_PIRATE_NPC", {}
            )
        assert rep_delta.call_args[0][3] == 5

    def test_rivalry_cascade_scales_off_weighted_award(self) -> None:
        player = _player()
        db = MagicMock()
        with patch(
            "src.services.emergent_reputation_service.participation_weight",
            return_value=0.5,
        ), patch(
            "src.services.emergent_reputation_service._apply_combined_rep_cap",
            side_effect=lambda _db, _pid, _fac, change: change,
        ), patch(
            "src.services.emergent_reputation_service._store_throttle_bucket",
        ), patch(
            "src.services.emergent_reputation_service.apply_faction_rep_delta",
            side_effect=[MagicMock(), MagicMock()],
        ) as rep_delta:
            EmergentReputationService(db).apply_emergent_action(
                player, "KILL_PIRATE_NPC", {}
            )
        assert rep_delta.call_count == 2
        assert rep_delta.call_args_list[0][0][3] == 2
        cascade_delta = rep_delta.call_args_list[1][0][3]
        assert cascade_delta < 0
        assert abs(cascade_delta) <= 2
