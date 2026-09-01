"""Unit tests for syndicate buyout → solo (LEG-2012)."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.services import port_ownership_service as po
from src.services.port_ownership_service import PortOwnershipError

UTC = timezone.utc
FIXED_NOW = datetime(2102, 6, 1, 12, 0, 0, tzinfo=UTC)


def _syndicate_station(primary_id, others, credits_by_id=None):
    shares = [{"player_id": str(primary_id), "pct": 100 - sum(o[1] for o in others)}]
    for oid, pct in others:
        shares.append({"player_id": str(oid), "pct": pct})
    return SimpleNamespace(
        id=uuid4(),
        owner_id=primary_id,
        ownership={
            po.SYNDICATE_MODE_KEY: "syndicate",
            po.SYNDICATE_SHARES_KEY: shares,
            "acquisition_cost": 1_000_000,
        },
        treasury_balance=0,
        _credits=credits_by_id or {},
    )


class TestExecuteSyndicateBuyout:
    def test_successful_buyout_primary_pays_others(self):
        primary = uuid4()
        other = uuid4()
        station = _syndicate_station(primary, [(other, 30)])
        buyer = SimpleNamespace(id=primary)
        other_player = SimpleNamespace(id=other, credits=0)
        db = MagicMock()

        def _lock_players(_db, ids):
            out = {}
            for pid in ids:
                if pid == primary:
                    out[primary] = SimpleNamespace(id=primary, credits=500_000)
                else:
                    out[other] = other_player
            return out

        with patch.object(po, "_lock_station", return_value=station):
            with patch.object(po, "forced_sale_price", return_value=1_000_000):
                with patch.object(po, "_lock_players_ascending", side_effect=_lock_players):
                    result = po.execute_syndicate_buyout(
                        db, station, buyer, FIXED_NOW
                    )

        assert result["mode"] == "solo"
        assert result["total_payout"] == 300_000
        assert result["fair_value"] == 1_000_000
        assert station.ownership[po.SYNDICATE_MODE_KEY] == "solo"
        assert station.ownership[po.SYNDICATE_SHARES_KEY] == [
            {"player_id": str(primary), "pct": 100}
        ]

    def test_insufficient_funds_rejected(self):
        primary = uuid4()
        other = uuid4()
        station = _syndicate_station(primary, [(other, 40)])
        buyer = SimpleNamespace(id=primary)
        db = MagicMock()

        def _lock_players(_db, ids):
            return {
                primary: SimpleNamespace(id=primary, credits=100),
                other: SimpleNamespace(id=other, credits=0),
            }

        with patch.object(po, "_lock_station", return_value=station):
            with patch.object(po, "forced_sale_price", return_value=1_000_000):
                with patch.object(po, "_lock_players_ascending", side_effect=_lock_players):
                    with pytest.raises(PortOwnershipError) as exc:
                        po.execute_syndicate_buyout(db, station, buyer, FIXED_NOW)
        assert exc.value.status_code == 400
        assert "Insufficient credits" in exc.value.detail

    def test_non_shareholder_rejected(self):
        primary = uuid4()
        other = uuid4()
        stranger = uuid4()
        station = _syndicate_station(primary, [(other, 25)])
        db = MagicMock()
        with patch.object(po, "_lock_station", return_value=station):
            with pytest.raises(PortOwnershipError) as exc:
                po.execute_syndicate_buyout(
                    db, station, SimpleNamespace(id=stranger), FIXED_NOW
                )
        assert exc.value.status_code == 403

    def test_solo_mode_rejected(self):
        owner = uuid4()
        station = SimpleNamespace(
            id=uuid4(),
            owner_id=owner,
            ownership={po.SYNDICATE_MODE_KEY: "solo"},
            treasury_balance=0,
        )
        db = MagicMock()
        with patch.object(po, "_lock_station", return_value=station):
            with pytest.raises(PortOwnershipError) as exc:
                po.execute_syndicate_buyout(
                    db, station, SimpleNamespace(id=owner), FIXED_NOW
                )
        assert exc.value.status_code == 400

    def test_minority_shareholder_becomes_owner(self):
        primary = uuid4()
        buyer_id = uuid4()
        station = _syndicate_station(primary, [(buyer_id, 35)])
        buyer = SimpleNamespace(id=buyer_id)
        primary_player = SimpleNamespace(id=primary, credits=0)
        db = MagicMock()

        def _lock_players(_db, ids):
            return {
                buyer_id: SimpleNamespace(id=buyer_id, credits=1_000_000),
                primary: primary_player,
            }

        with patch.object(po, "_lock_station", return_value=station):
            with patch.object(po, "forced_sale_price", return_value=1_000_000):
                with patch.object(po, "_lock_players_ascending", side_effect=_lock_players):
                    result = po.execute_syndicate_buyout(
                        db, station, buyer, FIXED_NOW
                    )

        assert station.owner_id == buyer_id
        assert result["owner_id"] == str(buyer_id)
        assert primary_player.credits == 650_000
