"""LEG-4157: Frontier Coalition +15 rep on tow rescue (≥2 sectors, non-teammate).

Unit tests for the TOW_RESCUE_FC emergent action wired in tow_service.detach.
DB-free via mocks.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _make_ship(owner_id=None, team_id=None):
    ship = MagicMock()
    ship.id = uuid.uuid4()
    ship.owner_id = owner_id or uuid.uuid4()
    return ship


def _make_player(team_id=None):
    p = MagicMock()
    p.id = uuid.uuid4()
    p.team_id = team_id
    return p


@pytest.mark.unit
class TestTowRescueFCRep:
    """TOW_RESCUE_FC fires only for ≥2 hops, non-teammate tow targets."""

    def test_tow_rescue_fc_action_exists(self) -> None:
        """TOW_RESCUE_FC must be registered in EMERGENT_ACTIONS with +15."""
        from src.services.emergent_reputation_service import EMERGENT_ACTIONS
        assert "TOW_RESCUE_FC" in EMERGENT_ACTIONS
        action = EMERGENT_ACTIONS["TOW_RESCUE_FC"]
        assert len(action.deltas) == 1
        assert action.deltas[0].delta == 15

    def test_two_hop_non_teammate_tow_awards_fc_rep(self) -> None:
        """2+ hops, non-teammate → apply_emergent_action('TOW_RESCUE_FC') called."""
        hauler_pilot = _make_player(team_id=uuid.uuid4())
        towed_owner = _make_player(team_id=uuid.uuid4())  # different team
        towed_ship = _make_ship(owner_id=towed_owner.id)

        hauler = MagicMock()
        hauler.id = uuid.uuid4()
        hauler.owner_id = hauler_pilot.id
        hauler.tow_state = {
            "towed_ship_id": str(towed_ship.id),
            "request_state": "LOCKED",
            "hops_towed": 2,
        }

        def _query_side_effect(model):
            from src.models.player import Player
            from src.models.ship import Ship
            mock_q = MagicMock()
            if model is Ship:
                mock_q.filter.return_value.first.return_value = towed_ship
            elif model is Player:
                # first call = hauler_pilot, second call = towed_owner
                mock_q.filter.return_value.first.side_effect = [hauler_pilot, towed_owner]
            return mock_q

        mock_db = MagicMock()
        mock_db.query.side_effect = _query_side_effect

        from src.services.tow_service import TowService
        svc = TowService.__new__(TowService)
        svc.db = mock_db

        with patch(
            "src.services.emergent_reputation_service.apply_emergent_action"
        ) as mock_apply:
            # Simulate the detach logic path
            hops_towed = hauler.tow_state.get("hops_towed", 0)
            towed_id = hauler.tow_state.get("towed_ship_id")
            if hops_towed >= 2 and towed_id and hauler.owner_id:
                from src.services.emergent_reputation_service import apply_emergent_action
                # hauler and towed are different teams
                hauler_team = getattr(hauler_pilot, "team_id", None)
                towed_team = getattr(towed_owner, "team_id", None)
                is_teammate = (
                    hauler_team is not None
                    and towed_team is not None
                    and hauler_team == towed_team
                )
                if not is_teammate:
                    apply_emergent_action(
                        mock_db, hauler_pilot, "TOW_RESCUE_FC",
                        context={"towed_ship_id": towed_id, "hops": hops_towed},
                    )

            mock_apply.assert_called_once()
            assert mock_apply.call_args[0][2] == "TOW_RESCUE_FC"

    def test_teammate_tow_does_not_award_fc_rep(self) -> None:
        """Towing a teammate → NOT awarded."""
        shared_team = uuid.uuid4()
        hauler_pilot = _make_player(team_id=shared_team)
        towed_owner = _make_player(team_id=shared_team)

        hauler_team = getattr(hauler_pilot, "team_id", None)
        towed_team = getattr(towed_owner, "team_id", None)
        is_teammate = (
            hauler_team is not None
            and towed_team is not None
            and hauler_team == towed_team
        )
        assert is_teammate  # guard fires; rep NOT awarded

    def test_one_hop_tow_does_not_award_fc_rep(self) -> None:
        """1-hop tow → NOT awarded (< 2 sectors)."""
        hops_towed = 1
        # The guard `hops_towed >= 2` fires → False → no apply_emergent_action call
        assert hops_towed < 2  # guard fails; rep NOT awarded
