"""LEG-3388 / LEG-3389 — emergent SS rep on Syndicate fence sales.

DB-free unit tests (dummy env so conftest/settings import).
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("DATABASE_URL", "postgresql://ci:ci@127.0.0.1:5432/ci")
os.environ.setdefault("JWT_SECRET", "ci-test-jwt-secret-not-used-32chars!!")
os.environ.setdefault("ADMIN_USERNAME", "ci-admin-user")
os.environ.setdefault("ADMIN_PASSWORD", "ci-admin-pass-12")
os.environ.setdefault(
    "ARIA_ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
)

import pytest

from src.core.commodity_economy import base_price
from src.models.faction import FactionType
from src.models.reputation import ReputationLevel
from src.services.emergent_reputation_service import (
    EMERGENT_ACTIONS,
    TRADE_VOLUME_CREDITS_PER_BLOCK,
)
from src.services.syndicate_fence_service import SyndicateFenceService


def test_fence_syndicate_volume_ss_registered() -> None:
    action = EMERGENT_ACTIONS["FENCE_SYNDICATE_VOLUME_SS"]
    assert [(d.faction, d.delta) for d in action.deltas] == [
        (FactionType.SYNDICATE, 5)
    ]
    assert "5,000" in action.doc_source or "fence" in action.doc_source.lower()


def test_stolen_flagged_sale_ss_registered() -> None:
    action = EMERGENT_ACTIONS["STOLEN_FLAGGED_SALE_SS"]
    assert [(d.faction, d.delta) for d in action.deltas] == [
        (FactionType.SYNDICATE, 10)
    ]
    assert "stolen" in action.doc_source.lower() or "flagged" in action.doc_source.lower()


def _player_ship_station(*, qty: int = 10, commodity: str = "ore"):
    unit = base_price(commodity)
    assert unit > 0
    player = SimpleNamespace(
        id=uuid4(),
        credits=0,
        personal_reputation=0,
        is_docked=True,
        current_port_id=None,
        current_sector_id=7,
        settings={},
    )
    ship = SimpleNamespace(
        cargo={
            "contents": {commodity: qty},
            "flagged_origin": {commodity: qty},
            "used": qty,
        }
    )
    station = SimpleNamespace(
        id=uuid4(),
        sector_id=7,
        has_syndicate_fence=True,
    )
    return player, ship, station, commodity, qty, unit


@pytest.mark.unit
class TestSyndicateFenceEmergentRep:
    def test_successful_fence_fires_stolen_flagged_and_volume(self) -> None:
        player, ship, station, commodity, qty, unit = _player_ship_station(qty=10)
        market_value = unit * qty
        svc = SyndicateFenceService(db=MagicMock())
        svc._syndicate_level = MagicMock(return_value=ReputationLevel.NEUTRAL)  # type: ignore[method-assign]

        with (
            patch(
                "src.services.emergent_reputation_service.apply_emergent_action"
            ) as mock_action,
            patch(
                "src.services.emergent_reputation_service.apply_trade_volume_rep"
            ) as mock_volume,
        ):
            mock_action.return_value = {"success": True}
            mock_volume.return_value = {"success": True, "blocks_awarded": 0}
            result = svc.fence_cargo(player, ship, station, commodity, qty)

        assert result["success"] is True
        assert result["market_value"] == market_value
        mock_action.assert_called_once()
        assert mock_action.call_args[0][2] == "STOLEN_FLAGGED_SALE_SS"
        mock_volume.assert_called_once()
        vol_args = mock_volume.call_args[0]
        assert vol_args[2] == "FENCE_SYNDICATE_VOLUME_SS"
        assert vol_args[3] == market_value  # gross, not payout

    def test_volume_uses_gross_not_payout_for_block_math(self) -> None:
        """Canon is / 5,000 cr *fenced* (market), not the 70% payout."""
        # Choose qty so market_value crosses exactly one 5k block if possible,
        # otherwise just assert the hook receives market_value.
        player, ship, station, commodity, qty, unit = _player_ship_station(qty=50)
        market_value = unit * qty
        svc = SyndicateFenceService(db=MagicMock())
        svc._syndicate_level = MagicMock(return_value=ReputationLevel.NEUTRAL)  # type: ignore[method-assign]

        with (
            patch(
                "src.services.emergent_reputation_service.apply_emergent_action"
            ) as mock_action,
            patch(
                "src.services.emergent_reputation_service.apply_trade_volume_rep"
            ) as mock_volume,
        ):
            mock_action.return_value = {"success": True}
            mock_volume.return_value = {
                "success": True,
                "blocks_awarded": market_value // TRADE_VOLUME_CREDITS_PER_BLOCK,
            }
            result = svc.fence_cargo(player, ship, station, commodity, qty)

        assert result["success"] is True
        assert result["payout"] < result["market_value"]
        assert mock_volume.call_args[0][3] == result["market_value"]
        assert mock_volume.call_args[0][3] != result["payout"]

    def test_gate_failure_never_dispatches_emergent(self) -> None:
        player, ship, station, commodity, qty, _unit = _player_ship_station()
        station.has_syndicate_fence = False
        svc = SyndicateFenceService(db=MagicMock())

        with (
            patch(
                "src.services.emergent_reputation_service.apply_emergent_action"
            ) as mock_action,
            patch(
                "src.services.emergent_reputation_service.apply_trade_volume_rep"
            ) as mock_volume,
        ):
            result = svc.fence_cargo(player, ship, station, commodity, qty)

        assert result["success"] is False
        mock_action.assert_not_called()
        mock_volume.assert_not_called()

    def test_rep_failure_never_blocks_fence_txn(self) -> None:
        player, ship, station, commodity, qty, _unit = _player_ship_station()
        svc = SyndicateFenceService(db=MagicMock())
        svc._syndicate_level = MagicMock(return_value=ReputationLevel.NEUTRAL)  # type: ignore[method-assign]

        with patch(
            "src.services.emergent_reputation_service.apply_emergent_action",
            side_effect=RuntimeError("simulated rep outage"),
        ):
            result = svc.fence_cargo(player, ship, station, commodity, qty)

        assert result["success"] is True
        assert result["credits"] == result["payout"]
