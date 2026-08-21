"""Unit tests — fair-operation bonus sector influence (LEG-956).

DB-free: mock the sector lookup, spawn-bias read, and influence write."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.services import port_ownership_service as po
from src.services.faction_service import FAIR_OPS_SECTOR_INFLUENCE_DELTA


def _station(tariff=0.04, sector_id=12345):
    return SimpleNamespace(
        id=uuid4(),
        owner_id=uuid4(),
        sector_id=sector_id,
        tax_rate=tariff,
    )


class TestAccrueFairOperationBonusInfluence:
    @patch("src.services.port_ownership_service._apply_reputation")
    @patch("src.services.port_ownership_service.adjust_sector_influence")
    @patch("src.services.port_ownership_service.sector_spawn_bias")
    @patch("src.services.port_ownership_service.Sector")
    def test_fair_streak_grants_sector_influence(
        self, SectorModel, spawn_bias, adjust, apply_rep
    ):
        sector_uuid = uuid4()
        dominant_faction = uuid4()
        sector_row = SimpleNamespace(id=sector_uuid, sector_id=12345)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = sector_row
        spawn_bias.return_value = {"dominant_faction_id": dominant_faction}

        station = _station()
        ledger = {"fair_ops_months": po.FAIR_OPS_MONTHS - 1}

        granted = po._accrue_fair_operation_bonus(db, station, ledger, months_elapsed=1)

        assert granted == po.FAIR_OPS_REPUTATION
        assert ledger["fair_ops_bonus_granted"] is True
        spawn_bias.assert_called_once_with(db, sector_uuid)
        adjust.assert_called_once_with(
            db, sector_uuid, dominant_faction, FAIR_OPS_SECTOR_INFLUENCE_DELTA
        )
        apply_rep.assert_called_once()

    @patch("src.services.port_ownership_service.adjust_sector_influence")
    def test_above_ceiling_tariff_resets_without_influence(self, adjust):
        db = MagicMock()
        station = _station(tariff=po.FAIR_TARIFF_MAX + 0.01)
        ledger = {"fair_ops_months": po.FAIR_OPS_MONTHS}

        granted = po._accrue_fair_operation_bonus(db, station, ledger, months_elapsed=1)

        assert granted == 0
        assert ledger["fair_ops_months"] == 0
        adjust.assert_not_called()

    @patch("src.services.port_ownership_service._apply_reputation")
    @patch("src.services.port_ownership_service.adjust_sector_influence")
    @patch("src.services.port_ownership_service.sector_spawn_bias")
    @patch("src.services.port_ownership_service.Sector")
    def test_idempotent_no_double_influence(
        self, SectorModel, spawn_bias, adjust, apply_rep
    ):
        db = MagicMock()
        station = _station()
        ledger = {
            "fair_ops_months": po.FAIR_OPS_MONTHS,
            "fair_ops_bonus_granted": True,
        }

        granted = po._accrue_fair_operation_bonus(db, station, ledger, months_elapsed=1)

        assert granted == 0
        adjust.assert_not_called()
        apply_rep.assert_not_called()
