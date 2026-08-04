"""Unit tests for WO-TAKEOVER-DEFENSE-COUNTERMOVES (pure helpers + light mocks)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.models.market_transaction import TransactionType
from src.services import port_ownership_service as po
from src.services.port_ownership_service import PortOwnershipError

UTC = timezone.utc


class TestTariffCutRate:
    def test_halves_prior(self):
        assert po.tariff_cut_rate(0.10) == 0.05

    def test_floors_at_min(self):
        assert po.tariff_cut_rate(0.0) == po.MIN_TAX_RATE


class TestMonthShareWithDefense:
    def test_absorb_dilutes_share_below_threshold(self):
        # Challenger 60k of 100k market (=0.6) + 50k absorb → 60/150 = 0.4
        share = po.month_share_with_defense(100_000, 60_000, 50_000)
        assert share == 0.4
        assert share <= po.TAKEOVER_SHARE_THRESHOLD

    def test_zero_volumes(self):
        assert po.month_share_with_defense(0, 0, 0) == 0.0


class TestDefenseVolumeForMonth:
    def test_sums_matching_counter_trades(self):
        cid = uuid4()
        station = SimpleNamespace(ownership={
            "defense_counters": [
                {"type": "counter_trade", "campaign_id": str(cid), "month": 1,
                 "defense_volume": 10_000},
                {"type": "counter_trade", "campaign_id": str(cid), "month": 1,
                 "defense_volume": 5_000},
                {"type": "counter_trade", "campaign_id": str(cid), "month": 2,
                 "defense_volume": 99_000},
                {"type": "tariff_cut", "campaign_id": str(cid)},
            ]
        })
        assert po.defense_volume_for_month(station, cid, 1) == 15_000

    def test_skips_rows_with_market_transaction_id(self):
        cid = uuid4()
        station = SimpleNamespace(ownership={
            "defense_counters": [
                {"type": "counter_trade", "campaign_id": str(cid), "month": 1,
                 "defense_volume": 0, "market_transaction_id": str(uuid4())},
                {"type": "counter_trade", "campaign_id": str(cid), "month": 1,
                 "defense_volume": 7_000},
            ]
        })
        assert po.defense_volume_for_month(station, cid, 1) == 7_000


class TestTickDefenseCounters:
    def test_restores_tax_when_tariff_cut_expires(self):
        cid = uuid4()
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        station = SimpleNamespace(
            id=uuid4(),
            tax_rate=0.05,
            ownership={
                "defense_counters": [{
                    "type": "tariff_cut",
                    "campaign_id": str(cid),
                    "prior_tax_rate": 0.10,
                    "tax_rate": 0.05,
                    "expires_at": past,
                }]
            },
        )
        db = MagicMock()
        # Active campaign still present so expiry (not dead-campaign) path fires.
        db.query.return_value.filter.return_value.all.return_value = [(cid,)]
        po.tick_defense_counters(db, station, datetime.now(UTC))
        assert station.tax_rate == 0.10
        assert po.get_defense_counters(station) == []


class TestActivateGuards:
    def test_non_owner_tariff_cut_forbidden(self):
        owner_id = uuid4()
        station = SimpleNamespace(id=uuid4(), owner_id=owner_id, tax_rate=0.1, ownership={})
        stranger = SimpleNamespace(id=uuid4(), credits=1_000_000)
        db = MagicMock()
        with patch.object(po, "_lock_station", return_value=station):
            with pytest.raises(PortOwnershipError) as exc:
                po.activate_tariff_cut(db, station, stranger)
        assert exc.value.status_code == 403


class TestActivateCounterTradeWritesMarketTransaction:
    def test_writes_buy_ledger_row_and_skips_synthetic_volume(self):
        owner_id = uuid4()
        station_id = uuid4()
        campaign_id = uuid4()
        station = SimpleNamespace(
            id=station_id, owner_id=owner_id, tax_rate=0.1, ownership={},
        )
        owner = SimpleNamespace(id=owner_id, credits=100_000)
        campaign = SimpleNamespace(
            id=campaign_id,
            station_id=station_id,
            status="building",
            started_at=datetime.now(UTC),
        )
        db = MagicMock()
        added = []

        def _add(obj):
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()
            added.append(obj)

        db.add.side_effect = _add
        db.flush = MagicMock()

        with patch.object(po, "_lock_station", return_value=station), \
             patch.object(po, "tick_defense_counters"), \
             patch.object(po, "_threat_campaign_for_defense", return_value=campaign):
            result = po.activate_counter_trade(db, station, owner, 12_000)

        assert owner.credits == 88_000
        assert result["defense_volume"] == 12_000
        assert result["market_transaction_id"]
        assert len(added) == 1
        tx = added[0]
        assert tx.transaction_type == TransactionType.BUY
        assert tx.total_value == 12_000
        assert tx.quantity == 12_000
        assert tx.unit_price == 1
        assert tx.commodity == po.COUNTER_TRADE_COMMODITY
        assert tx.admin_notes == "counter_trade_defense"
        counters = po.get_defense_counters(station)
        assert len(counters) == 1
        assert counters[0]["defense_volume"] == 0
        assert counters[0]["market_transaction_id"] == str(tx.id)
        assert po.defense_volume_for_month(station, campaign_id, counters[0]["month"]) == 0
