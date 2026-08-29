"""LEG-2862: passed syndicate tariff votes apply station.tax_rate (port-ownership.md:141-152)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.services import port_ownership_service as po
from src.services.station_governance_service import (
    _apply_passed_tariff,
    _maybe_resolve_row,
    _tariff_rate_from_proposed,
)

UTC = timezone.utc
FIXED_NOW = datetime(2102, 6, 1, 12, 0, 0, tzinfo=UTC)

A = uuid4()
B = uuid4()
LOCK = "src.services.station_governance_service._lock_station"
FLAG = "src.services.station_governance_service.flag_modified"


def _syndicate_station(*, tax_rate=0.05):
    return SimpleNamespace(
        id=uuid4(),
        owner_id=A,
        tax_rate=tax_rate,
        ownership={
            po.SYNDICATE_MODE_KEY: "syndicate",
            "shares": [
                {"player_id": str(A), "pct": 60},
                {"player_id": str(B), "pct": 40},
            ],
        },
    )


class TestTariffRateFromProposed:
    def test_dict_value_key(self):
        assert _tariff_rate_from_proposed({"value": 0.18}) == pytest.approx(0.18)

    def test_dict_tax_rate_key(self):
        assert _tariff_rate_from_proposed({"tax_rate": 0.12}) == pytest.approx(0.12)

    def test_scalar(self):
        assert _tariff_rate_from_proposed(0.15) == pytest.approx(0.15)


class TestApplyPassedTariff:
    def test_sets_clamped_rate(self):
        station = _syndicate_station(tax_rate=0.05)
        row = SimpleNamespace(
            id=uuid4(),
            proposed_value={"value": 0.30},
        )
        db = MagicMock()
        with patch(LOCK, return_value=station):
            _apply_passed_tariff(db, station, row)
        assert station.tax_rate == pytest.approx(po.MAX_TAX_RATE)
        db.flush.assert_called_once()


class TestMaybeResolveRowTariffApply:
    def test_passed_tariff_updates_station(self):
        station = _syndicate_station(tax_rate=0.05)
        row = SimpleNamespace(
            id=uuid4(),
            status="open",
            vote_type="tariff",
            proposed_value={"value": 0.18},
            share_snapshot=[
                {"player_id": str(A), "pct": 60, "inactive": False},
                {"player_id": str(B), "pct": 40, "inactive": False},
            ],
            ballots=[
                {"player_id": str(A), "position": "for"},
                {"player_id": str(B), "position": "against"},
            ],
            rng_seed=1,
            window_ends_at=FIXED_NOW + timedelta(hours=1),
            outcome=None,
        )
        db = MagicMock()
        with patch(LOCK, return_value=station), patch(FLAG):
            _maybe_resolve_row(db, station, row, FIXED_NOW)
        assert row.status == "passed"
        assert row.outcome["passed"] is True
        assert station.tax_rate == pytest.approx(0.18)

    def test_failed_vote_leaves_tax_rate(self):
        station = _syndicate_station(tax_rate=0.05)
        row = SimpleNamespace(
            id=uuid4(),
            status="open",
            vote_type="tariff",
            proposed_value={"value": 0.20},
            share_snapshot=[
                {"player_id": str(A), "pct": 40, "inactive": False},
                {"player_id": str(B), "pct": 60, "inactive": False},
            ],
            ballots=[
                {"player_id": str(A), "position": "for"},
                {"player_id": str(B), "position": "against"},
            ],
            rng_seed=1,
            window_ends_at=FIXED_NOW + timedelta(hours=1),
            outcome=None,
        )
        db = MagicMock()
        with patch(FLAG):
            _maybe_resolve_row(db, station, row, FIXED_NOW)
        assert row.status == "open"
        assert station.tax_rate == pytest.approx(0.05)

    def test_tiebreak_pass_applies_tariff(self):
        station = _syndicate_station(tax_rate=0.05)
        row = SimpleNamespace(
            id=uuid4(),
            status="open",
            vote_type="tariff",
            proposed_value={"value": 0.12},
            share_snapshot=[
                {"player_id": str(A), "pct": 40, "inactive": False},
                {"player_id": str(B), "pct": 35, "inactive": False},
                {"player_id": str(uuid4()), "pct": 25, "inactive": False},
            ],
            ballots=[
                {"player_id": str(A), "position": "for"},
                {"player_id": str(B), "position": "against"},
            ],
            rng_seed=7,
            window_ends_at=FIXED_NOW - timedelta(hours=1),
            outcome=None,
        )
        db = MagicMock()
        with patch(LOCK, return_value=station), patch(FLAG):
            _maybe_resolve_row(db, station, row, FIXED_NOW)
        assert row.status == "tiebreak"
        assert row.outcome["passed"] is True
        assert station.tax_rate == pytest.approx(0.12)
