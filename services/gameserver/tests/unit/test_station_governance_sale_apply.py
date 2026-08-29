"""LEG-2863: passed syndicate sale votes list the station (port-ownership.md:143)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.services import port_ownership_service as po
from src.services.station_governance_service import (
    _apply_passed_sale,
    _maybe_resolve_row,
)

UTC = timezone.utc
FIXED_NOW = datetime(2102, 6, 1, 12, 0, 0, tzinfo=UTC)

A = uuid4()
B = uuid4()
LOCK = "src.services.port_ownership_service._lock_station"
LIST = "src.services.port_ownership_service.list_station"
FLAG_PO = "src.services.port_ownership_service.flag_modified"
FLAG_GOV = "src.services.station_governance_service.flag_modified"
APPLY = "src.services.station_governance_service.apply_governance_sale_listing"


def _syndicate_station(*, owner_id=None, tax_rate=0.05):
    return SimpleNamespace(
        id=uuid4(),
        owner_id=owner_id or A,
        tax_rate=tax_rate,
        # Attributes required by is_listable (purchasable class-1 port).
        is_player_ownable=True,
        is_destroyed=False,
        station_class=1,
        is_spacedock=False,
        tradedock_tier=None,
        is_quest_hub=False,
        is_faction_headquarters=False,
        ownership={
            po.SYNDICATE_MODE_KEY: "syndicate",
            po.SYNDICATE_SHARES_KEY: [
                {"player_id": str(A), "pct": 70},
                {"player_id": str(B), "pct": 30},
            ],
        },
    )


class TestApplyPassedSale:
    def test_releases_ownership_and_lists(self):
        station = _syndicate_station()
        listing = SimpleNamespace(id=uuid4(), price=1_250_000)
        db = MagicMock()

        with patch(LOCK, return_value=station), patch(
            LIST, return_value=listing
        ) as mock_list, patch(FLAG_PO):
            result = po.apply_governance_sale_listing(db, station, now=FIXED_NOW)

        assert station.owner_id is None
        assert po.SYNDICATE_MODE_KEY not in (station.ownership or {})
        mock_list.assert_called_once()
        assert result is listing

    def test_apply_passed_sale_delegates_to_listing_helper(self):
        station = _syndicate_station()
        row = SimpleNamespace(id=uuid4())
        listing = SimpleNamespace(id=uuid4(), price=900_000)
        db = MagicMock()

        with patch(APPLY, return_value=listing) as mock_apply:
            _apply_passed_sale(db, station, row, now=FIXED_NOW)

        mock_apply.assert_called_once_with(db, station, now=FIXED_NOW)


class TestMaybeResolveRowSaleApply:
    def test_passed_sale_triggers_listing(self):
        station = _syndicate_station()
        row = SimpleNamespace(
            id=uuid4(),
            status="open",
            vote_type="sale",
            proposed_value={},
            share_snapshot=[
                {"player_id": str(A), "pct": 70, "inactive": False},
                {"player_id": str(B), "pct": 30, "inactive": False},
            ],
            ballots=[
                {"player_id": str(A), "position": "for"},
                {"player_id": str(B), "position": "for"},
            ],
            rng_seed=1,
            window_ends_at=FIXED_NOW - timedelta(hours=1),
            outcome=None,
        )
        listing = SimpleNamespace(id=uuid4(), price=2_000_000)
        db = MagicMock()

        with patch(LOCK, return_value=station), patch(
            LIST, return_value=listing
        ), patch(FLAG_PO), patch(FLAG_GOV):
            _maybe_resolve_row(db, station, row, FIXED_NOW)

        assert row.status == "passed"
        assert row.outcome["passed"] is True
        assert station.owner_id is None

    def test_non_passed_sale_leaves_owner(self):
        """Below 66% yes → tip resolves via tiebreak; largest against → not passed."""
        station = _syndicate_station()
        row = SimpleNamespace(
            id=uuid4(),
            status="open",
            vote_type="sale",
            proposed_value={},
            share_snapshot=[
                {"player_id": str(A), "pct": 40, "inactive": False},
                {"player_id": str(B), "pct": 60, "inactive": False},
            ],
            ballots=[
                {"player_id": str(A), "position": "for"},
                {"player_id": str(B), "position": "against"},
            ],
            rng_seed=1,
            window_ends_at=FIXED_NOW - timedelta(hours=1),
            outcome=None,
        )
        db = MagicMock()
        prior_owner = station.owner_id

        with patch(FLAG_GOV):
            _maybe_resolve_row(db, station, row, FIXED_NOW)

        assert row.outcome["passed"] is False
        assert station.owner_id == prior_owner
