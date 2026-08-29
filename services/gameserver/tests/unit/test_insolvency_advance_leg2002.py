"""LEG-2002 Soft-ORDER invent=0 — insolvency 7-day advance before auto-sell."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from src.services import port_ownership_service as po

UTC = timezone.utc
FIXED_NOW = datetime(2102, 6, 1, 12, 0, 0, tzinfo=UTC)


def _station(*, ownership=None, treasury=0):
    owner_id = uuid4()
    return SimpleNamespace(
        id=uuid4(),
        owner_id=owner_id,
        ownership=ownership if ownership is not None else {},
        treasury_balance=treasury,
        tax_rate=0.05,
        sector_id=None,
    )


class TestAdvanceOrSellInsolvent:
    def test_first_hit_opens_pending_window_no_sell(self):
        station = _station()
        ledger = station.ownership
        db = MagicMock()
        with patch.object(po, "flag_modified"):
            with patch.object(po, "_broadcast_insolvency_advance") as broadcast:
                with patch.object(po, "auto_sell_insolvent") as sell:
                    result = po._advance_or_sell_insolvent(
                        db, station, ledger, FIXED_NOW
                    )
        assert result["status"] == "pending"
        assert result["advance_days"] == po.INSOLVENCY_ADVANCE_DAYS
        assert ledger["insolvency_pending"] is True
        assert "insolvency_sell_at" in ledger
        broadcast.assert_called_once()
        sell.assert_not_called()

    def test_mid_window_stays_pending_idempotent(self):
        sell_at = FIXED_NOW + timedelta(days=3)
        station = _station(
            ownership={
                "insolvency_pending": True,
                "insolvency_sell_at": sell_at.isoformat(),
            }
        )
        ledger = station.ownership
        db = MagicMock()
        with patch.object(po, "flag_modified"):
            with patch.object(po, "auto_sell_insolvent") as sell:
                with patch.object(po, "_broadcast_insolvency_advance") as broadcast:
                    result = po._advance_or_sell_insolvent(
                        db, station, ledger, FIXED_NOW
                    )
        assert result["status"] == "pending"
        assert result["insolvency_sell_at"] == sell_at.isoformat()
        sell.assert_not_called()
        broadcast.assert_not_called()

    def test_after_deadline_calls_auto_sell(self):
        sell_at = FIXED_NOW - timedelta(hours=1)
        station = _station(
            ownership={
                "insolvency_pending": True,
                "insolvency_sell_at": sell_at.isoformat(),
            }
        )
        ledger = station.ownership
        db = MagicMock()
        with patch.object(po, "flag_modified"):
            with patch.object(
                po, "auto_sell_insolvent", return_value={"status": "auto_sold"}
            ) as sell:
                result = po._advance_or_sell_insolvent(db, station, ledger, FIXED_NOW)
        assert result["status"] == "auto_sold"
        sell.assert_called_once()
        assert "insolvency_pending" not in ledger
        assert "insolvency_sell_at" not in ledger


class TestAccrueOperatingCostsAdvance:
    def test_threshold_hit_defers_auto_sell(self):
        station = _station(
            ownership={
                "acquisition_cost": 1_000_000,
                "operating_fund": 0,
                "insolvency_months": 2,
                "costs_accrued_at": (FIXED_NOW - timedelta(days=35)).isoformat(),
            },
            treasury=0,
        )
        db = MagicMock()
        with patch.object(po, "flag_modified"):
            with patch.object(po, "_lock_station", return_value=station):
                with patch.object(po, "_accrue_fair_operation_bonus", return_value=0):
                    with patch.object(po, "_broadcast_insolvency_advance"):
                        with patch.object(po, "auto_sell_insolvent") as sell:
                            result = po.accrue_operating_costs(
                                db, station, FIXED_NOW
                            )
        assert result["insolvency_months"] >= po.INSOLVENCY_MONTHS
        assert result["insolvency"]["status"] == "pending"
        assert station.ownership.get("insolvency_pending") is True
        sell.assert_not_called()

    def test_covered_month_clears_pending_window(self):
        sell_at = (FIXED_NOW + timedelta(days=2)).isoformat()
        station = _station(
            ownership={
                "acquisition_cost": 1_000_000,
                "operating_fund": 1_000_000,
                "insolvency_months": 3,
                "insolvency_pending": True,
                "insolvency_sell_at": sell_at,
                "costs_accrued_at": (FIXED_NOW - timedelta(days=35)).isoformat(),
            },
            treasury=0,
        )
        db = MagicMock()
        with patch.object(po, "flag_modified"):
            with patch.object(po, "_lock_station", return_value=station):
                with patch.object(po, "_accrue_fair_operation_bonus", return_value=0):
                    with patch.object(po, "auto_sell_insolvent") as sell:
                        result = po.accrue_operating_costs(db, station, FIXED_NOW)
        assert result["covered"] is True
        assert result["insolvency_months"] == 0
        assert station.ownership.get("insolvency_pending") is None
        assert station.ownership.get("insolvency_sell_at") is None
        assert "insolvency" not in result
        sell.assert_not_called()
