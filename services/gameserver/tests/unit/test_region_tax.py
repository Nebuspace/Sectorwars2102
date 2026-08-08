"""DB-free unit tests for Region.tax_rate wiring (WO-BUILD-REGION-TAX-RATE-
WIRING) and the 50% owner revenue-share payout (WO-BUILD-REGION-TAX-
REVENUE-SHARE-PAYOUT, DECISIONS.md region-tax-revenue-share, ratified
2026-08-07).

Covers trading_service.compute_region_tax_rate and .realize_region_tax
directly (the actual taxable-event hook trading.py's buy/sell routes call)
rather than the full route, since realize_region_tax owns all the money
math and DB writes for this feature — the route-level wiring is a thin
two-line call already exercised by the existing test_trading_core_pins.py
buy/sell pins (unowned-station fixtures there resolve region_tax_rate to
0.0 via compute_region_tax_rate's own no-region branch, so those pins stay
green untouched).
"""
from __future__ import annotations

import uuid
from typing import Any, List
from unittest.mock import patch

from src.models.player import Player
from src.models.region import Region, RegionalTreasuryEntry
from src.models.station import Station
from src.models.user import User
from src.services.trading_service import (
    REGION_TAX_OWNER_SHARE,
    compute_region_tax_rate,
    fallback_credit_region_tax,
    realize_region_tax,
)


class _FakeQuery:
    def __init__(self, model, rows_by_model):
        self._model = model
        self._rows_by_model = rows_by_model
        self._filtered = None

    def filter(self, *args, **kwargs):
        return self

    def populate_existing(self):
        return self

    def with_for_update(self):
        return self

    def first(self):
        rows = self._rows_by_model.get(self._model, [])
        return rows[0] if rows else None


class FakeSession:
    """Keys rows by model class; .query(Model).filter(...).first() returns
    the single row registered for that model (good enough for
    realize_region_tax's single-row-per-model lookups)."""

    def __init__(self, rows_by_model: dict):
        self._rows_by_model = rows_by_model
        self.added: List[Any] = []

    def query(self, model, *args):
        return _FakeQuery(model, self._rows_by_model)

    def add(self, obj):
        self.added.append(obj)


def _region(*, tax_rate=0.10, owner_id=None, treasury_balance=0) -> Region:
    return Region(
        id=uuid.uuid4(),
        name="Test Region",
        tax_rate=tax_rate,
        owner_id=owner_id,
        treasury_balance=treasury_balance,
    )


def _station(*, region=None) -> Station:
    st = Station(id=uuid.uuid4(), name="Test Station", sector_id=1)
    st.region = region
    st.region_id = region.id if region is not None else None
    return st


class TestComputeRegionTaxRate:
    def test_no_region_is_zero(self):
        assert compute_region_tax_rate(_station(region=None)) == 0.0

    def test_reads_regions_tax_rate(self):
        region = _region(tax_rate=0.15)
        assert compute_region_tax_rate(_station(region=region)) == 0.15


class TestRealizeRegionTax:
    def test_zero_or_negative_tax_amount_is_noop(self):
        region = _region()
        db = FakeSession({Region: [region]})
        result = realize_region_tax(db, _station(region=region), 0)
        assert result == {
            "region_tax": 0, "owner_share": 0, "treasury_share": 0,
            "owner_payout_dest": None,
        }
        assert db.added == []

        result_neg = realize_region_tax(db, _station(region=region), -5)
        assert result_neg["region_tax"] == 0
        assert db.added == []

    def test_no_region_is_noop(self):
        db = FakeSession({})
        result = realize_region_tax(db, _station(region=None), 100)
        assert result["region_tax"] == 0
        assert db.added == []

    def test_unclaimed_region_routes_full_amount_to_treasury(self):
        region = _region(tax_rate=0.10, owner_id=None, treasury_balance=0)
        db = FakeSession({Region: [region]})
        station = _station(region=region)

        result = realize_region_tax(db, station, 100)

        assert result["region_tax"] == 100
        assert result["owner_share"] == 0
        assert result["treasury_share"] == 100
        assert result["owner_payout_dest"] is None
        assert region.treasury_balance == 100
        assert len(db.added) == 1
        entry = db.added[0]
        assert isinstance(entry, RegionalTreasuryEntry)
        assert entry.before_balance == 0
        assert entry.after_balance == 100
        assert entry.delta == 100
        assert entry.cause_type == RegionalTreasuryEntry.CAUSE_TAX_COLLECTION
        assert entry.cause_id == station.id

    def test_owned_region_splits_50_50_between_owner_and_treasury(self):
        owner_id = uuid.uuid4()
        region = _region(tax_rate=0.10, owner_id=owner_id, treasury_balance=500)
        station = _station(region=region)

        owner_user = User(id=owner_id, username="regionowner")
        owner_player = Player(id=uuid.uuid4(), user_id=owner_id)
        owner_user.player = owner_player

        db = FakeSession({Region: [region], User: [owner_user]})

        with patch(
            "src.services.central_bank_service.credit_wallet_or_bank",
            return_value="wallet",
        ) as mock_credit:
            result = realize_region_tax(db, station, 101)

        assert result["region_tax"] == 101
        expected_owner_share = int(101 * REGION_TAX_OWNER_SHARE)
        expected_treasury_share = 101 - expected_owner_share
        assert result["owner_share"] == expected_owner_share
        assert result["treasury_share"] == expected_treasury_share
        assert result["owner_payout_dest"] == "wallet"

        mock_credit.assert_called_once()
        call_args, call_kwargs = mock_credit.call_args
        assert call_args[0] is db
        assert call_args[1] is owner_player
        assert call_args[2] == expected_owner_share
        assert call_kwargs["entry_type"] == "region_tax_revenue_share"
        assert call_kwargs["source"] == f"region:{region.id}"

        assert region.treasury_balance == 500 + expected_treasury_share
        assert len(db.added) == 1
        assert db.added[0].delta == expected_treasury_share

    def test_owner_payout_failure_folds_share_into_treasury(self):
        owner_id = uuid.uuid4()
        region = _region(tax_rate=0.10, owner_id=owner_id, treasury_balance=0)
        station = _station(region=region)

        owner_user = User(id=owner_id, username="regionowner")
        owner_user.player = Player(id=uuid.uuid4(), user_id=owner_id)
        db = FakeSession({Region: [region], User: [owner_user]})

        with patch(
            "src.services.central_bank_service.credit_wallet_or_bank",
            side_effect=RuntimeError("boom"),
        ):
            result = realize_region_tax(db, station, 100)

        assert result["owner_share"] == 0
        assert result["treasury_share"] == 100
        assert result["owner_payout_dest"] is None
        assert region.treasury_balance == 100
        assert len(db.added) == 1
        assert db.added[0].delta == 100

    def test_owner_with_no_linked_player_folds_share_into_treasury(self):
        owner_id = uuid.uuid4()
        region = _region(tax_rate=0.10, owner_id=owner_id, treasury_balance=0)
        station = _station(region=region)

        owner_user = User(id=owner_id, username="regionowner")
        owner_user.player = None  # no linked Player row
        db = FakeSession({Region: [region], User: [owner_user]})

        result = realize_region_tax(db, station, 100)

        assert result["owner_share"] == 0
        assert result["treasury_share"] == 100
        assert region.treasury_balance == 100

    def test_owner_user_row_missing_folds_share_into_treasury(self):
        owner_id = uuid.uuid4()
        region = _region(tax_rate=0.10, owner_id=owner_id, treasury_balance=0)
        station = _station(region=region)
        # No User row registered at all -> query returns None.
        db = FakeSession({Region: [region], User: []})

        result = realize_region_tax(db, station, 100)

        assert result["owner_share"] == 0
        assert result["treasury_share"] == 100
        assert region.treasury_balance == 100


class TestFallbackCreditRegionTax:
    """WO-FIX-REGION-TAX-FALLBACK-MISROUTES-STATION-TREASURY — when
    realize_region_tax raises, the route fallback must credit the region
    treasury, never station.treasury_balance (station-owner private purse)."""

    def test_forced_realize_failure_does_not_credit_station_owner(self):
        region = _region(tax_rate=0.10, owner_id=uuid.uuid4(), treasury_balance=200)
        station = _station(region=region)
        station.treasury_balance = 1_000  # owner's private purse — must stay put
        db = FakeSession({Region: [region]})

        # Mimic trading.py buy/sell except path: realize raises → fallback.
        with patch(
            "src.services.trading_service.realize_region_tax",
            side_effect=RuntimeError("forced realize failure"),
        ):
            import src.services.trading_service as trading_svc
            try:
                trading_svc.realize_region_tax(db, station, 75)
                raise AssertionError("realize_region_tax should have raised")
            except RuntimeError:
                credited = fallback_credit_region_tax(db, station, 75)

        assert credited == 75
        assert region.treasury_balance == 275
        assert station.treasury_balance == 1_000
        assert any(isinstance(obj, RegionalTreasuryEntry) for obj in db.added)

    def test_no_region_drops_credits_rather_than_station_treasury(self):
        station = _station(region=None)
        station.treasury_balance = 50
        db = FakeSession({})

        credited = fallback_credit_region_tax(db, station, 40)

        assert credited == 0
        assert station.treasury_balance == 50
        assert db.added == []
