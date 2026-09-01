"""LEG-3395 — emergent FA +25 on Fringe-controlled black-market transactions."""
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

from src.models.faction import FactionType
from src.models.station import StationType
from src.services.contraband_service import ContrabandService
from src.services.emergent_reputation_service import EMERGENT_ACTIONS


def test_black_market_tx_fa_registered() -> None:
    action = EMERGENT_ACTIONS["BLACK_MARKET_TX_FA"]
    assert [(d.faction, d.delta) for d in action.deltas] == [
        (FactionType.OUTLAWS, 25)
    ]
    assert "Black-market" in action.doc_source or "black-market" in action.doc_source.lower()


@pytest.mark.unit
class TestBlackMarketTxFaEmergent:
    def test_fringe_controlled_resolver(self) -> None:
        fringe = SimpleNamespace(faction_affiliation="Fringe Alliance")
        other = SimpleNamespace(faction_affiliation="Terran Federation")
        blank = SimpleNamespace(faction_affiliation=None)
        assert ContrabandService._is_fringe_controlled_port(fringe) is True
        assert ContrabandService._is_fringe_controlled_port(other) is False
        assert ContrabandService._is_fringe_controlled_port(blank) is False
        assert ContrabandService._is_fringe_controlled_port(None) is False

    def test_award_fires_only_on_fringe_port(self) -> None:
        svc = ContrabandService(db=MagicMock())
        player = SimpleNamespace(id=uuid4(), current_sector_id=9)
        fringe = SimpleNamespace(
            type=StationType.BLACK_MARKET,
            faction_affiliation="Fringe Alliance",
        )
        non_fringe = SimpleNamespace(
            type=StationType.BLACK_MARKET,
            faction_affiliation="Mercantile Guild",
        )

        with patch(
            "src.services.emergent_reputation_service.apply_emergent_action"
        ) as mock_action:
            mock_action.return_value = {"success": True}
            svc._maybe_award_black_market_tx_fa(player, non_fringe)
            mock_action.assert_not_called()

            svc._maybe_award_black_market_tx_fa(player, fringe)
            mock_action.assert_called_once()
            assert mock_action.call_args[0][2] == "BLACK_MARKET_TX_FA"

    def test_rep_failure_is_non_fatal(self) -> None:
        svc = ContrabandService(db=MagicMock())
        player = SimpleNamespace(id=uuid4(), current_sector_id=9)
        fringe = SimpleNamespace(
            type=StationType.BLACK_MARKET,
            faction_affiliation="Fringe Alliance",
        )
        with patch(
            "src.services.emergent_reputation_service.apply_emergent_action",
            side_effect=RuntimeError("simulated rep outage"),
        ):
            # Must not raise
            svc._maybe_award_black_market_tx_fa(player, fringe)
