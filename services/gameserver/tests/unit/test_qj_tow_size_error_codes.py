"""Tip-sweep: QJ tow size rejects carry ERR_QJ_TOWED_SIZE_EXCEEDS_MEDIUM."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.models.ship import ShipSize
from src.services.quantum_service import QuantumError, _compute_jump_cost


@pytest.mark.unit
class TestQjTowSizeErrorCodes:
    def test_capital_tow_raises_coded_error(self) -> None:
        ship = SimpleNamespace(
            id="wj-1",
            tow_state={"towed_size": "CAPITAL"},
        )
        with patch("src.services.tow_service.TowService") as Tow:
            inst = Tow.return_value
            inst.is_being_towed.return_value = False
            inst.is_actively_towing.return_value = True
            with pytest.raises(QuantumError) as ei:
                _compute_jump_cost(MagicMock(), ship)
        assert ei.value.error_code == "ERR_QJ_TOWED_SIZE_EXCEEDS_MEDIUM"

    def test_oversized_tow_raises_coded_error(self) -> None:
        # LARGE is above QJ_MAX_TOWED_SIZE_UNITS (medium max).
        ship = SimpleNamespace(
            id="wj-2",
            tow_state={"towed_size": "LARGE"},
        )
        with patch("src.services.tow_service.TowService") as Tow:
            inst = Tow.return_value
            inst.is_being_towed.return_value = False
            inst.is_actively_towing.return_value = True
            with pytest.raises(QuantumError) as ei:
                _compute_jump_cost(MagicMock(), ship)
        assert ei.value.error_code == "ERR_QJ_TOWED_SIZE_EXCEEDS_MEDIUM"
        assert ShipSize.LARGE  # sanity — enum exists
