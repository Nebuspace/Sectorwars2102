"""LEG-4236: syndicate stake-transfer propose / approve / reject / threshold.

Canon: FEATURES/economy/port-ownership.md § Syndicate — approval of holders
representing >50% of remaining stake (strictly greater than half of
100 − transfer_pct).
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.services import station_stake_transfer_service as xfer
from src.services.port_ownership_service import PortOwnershipError, SYNDICATE_MODE_KEY

UTC = timezone.utc
FIXED_NOW = datetime(2102, 6, 1, 12, 0, 0, tzinfo=UTC)


class TestThresholdHelpers:
    def test_remaining_stake(self):
        assert xfer.remaining_stake_pct(20) == 80
        assert xfer.remaining_stake_pct(1) == 99

    def test_threshold_strictly_greater_than_half(self):
        # remaining=80 → need >40
        assert xfer.threshold_met(40, 80) is False
        assert xfer.threshold_met(41, 80) is True

    def test_transferor_weight_is_remaining_holding(self):
        a, b = str(uuid4()), str(uuid4())
        snap = [{"player_id": a, "pct": 60}, {"player_id": b, "pct": 40}]
        assert xfer.approval_weight_for(a, snap, a, 20) == 40
        assert xfer.approval_weight_for(b, snap, a, 20) == 40


class TestPropose:
    def _syndicate_station(self, owner_id, other_id, owner_pct=60, other_pct=40):
        return SimpleNamespace(
            id=uuid4(),
            owner_id=owner_id,
            ownership={
                SYNDICATE_MODE_KEY: "syndicate",
                "co_ownership_shares": [
                    {"player_id": str(owner_id), "pct": owner_pct},
                    {"player_id": str(other_id), "pct": other_pct},
                ],
            },
            treasury_balance=0,
        )

    def test_non_stakeholder_403(self):
        owner_id, other_id = uuid4(), uuid4()
        station = self._syndicate_station(owner_id, other_id)
        stranger = SimpleNamespace(id=uuid4())
        db = MagicMock()
        with patch.object(xfer, "_lock_station", return_value=station):
            with pytest.raises(PortOwnershipError) as exc:
                xfer.propose_stake_transfer(
                    db, station, stranger, uuid4(), 10, FIXED_NOW
                )
        assert exc.value.status_code == 403

    def test_solo_mode_403(self):
        owner_id = uuid4()
        station = SimpleNamespace(
            id=uuid4(),
            owner_id=owner_id,
            ownership={SYNDICATE_MODE_KEY: "solo"},
            treasury_balance=0,
        )
        owner = SimpleNamespace(id=owner_id)
        db = MagicMock()
        with patch.object(xfer, "_lock_station", return_value=station):
            with pytest.raises(PortOwnershipError) as exc:
                xfer.propose_stake_transfer(
                    db, station, owner, uuid4(), 10, FIXED_NOW
                )
        assert exc.value.status_code == 403

    def test_propose_pending_when_threshold_not_met(self):
        """Alice 60→20: remaining=80, Alice weight=40 — not >40 → pending."""
        owner_id, other_id, target_id = uuid4(), uuid4(), uuid4()
        station = self._syndicate_station(owner_id, other_id, 60, 40)
        owner = SimpleNamespace(id=owner_id)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
            id=target_id
        )
        with patch.object(xfer, "_lock_station", return_value=station):
            result = xfer.propose_stake_transfer(
                db, station, owner, target_id, 20, FIXED_NOW
            )
        prop = result["proposal"]
        assert prop["status"] == "pending"
        assert prop["pct"] == 20
        assert prop["remaining_stake_pct"] == 80
        assert prop["approving_weight"] == 40
        assert prop["threshold_met"] is False
        assert "shares" not in result

    def test_propose_applies_when_proposer_clears_threshold(self):
        """Alice 60→10: remaining=90, Alice weight=50 >45 → applied."""
        owner_id, other_id, target_id = uuid4(), uuid4(), uuid4()
        station = self._syndicate_station(owner_id, other_id, 60, 40)
        owner = SimpleNamespace(id=owner_id)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
            id=target_id
        )
        with patch.object(xfer, "_lock_station", return_value=station):
            result = xfer.propose_stake_transfer(
                db, station, owner, target_id, 10, FIXED_NOW
            )
        assert result["proposal"]["status"] == "applied"
        by_id = {s["player_id"]: s["pct"] for s in result["shares"]}
        assert by_id[str(owner_id)] == 50
        assert by_id[str(other_id)] == 40
        assert by_id[str(target_id)] == 10


class TestApproveReject:
    def _pending_proposal_station(self):
        owner_id, other_id, target_id = uuid4(), uuid4(), uuid4()
        proposal_id = str(uuid4())
        snap = [
            {"player_id": str(owner_id), "pct": 60},
            {"player_id": str(other_id), "pct": 40},
        ]
        station = SimpleNamespace(
            id=uuid4(),
            owner_id=owner_id,
            ownership={
                SYNDICATE_MODE_KEY: "syndicate",
                "co_ownership_shares": [
                    {"player_id": str(owner_id), "pct": 60},
                    {"player_id": str(other_id), "pct": 40},
                ],
                xfer.STAKE_TRANSFERS_KEY: [
                    {
                        "proposal_id": proposal_id,
                        "from_player_id": str(owner_id),
                        "to_player_id": str(target_id),
                        "pct": 20,
                        "status": "pending",
                        "share_snapshot": snap,
                        "approvals": [
                            {
                                "player_id": str(owner_id),
                                "at": FIXED_NOW.isoformat(),
                            }
                        ],
                        "approving_weight": 40,
                        "remaining_stake_pct": 80,
                        "created_at": FIXED_NOW.isoformat(),
                        "resolved_at": None,
                    }
                ],
            },
            treasury_balance=0,
        )
        return station, owner_id, other_id, target_id, proposal_id

    def test_approve_applies_when_second_co_owner_clears_threshold(self):
        station, owner_id, other_id, target_id, proposal_id = (
            self._pending_proposal_station()
        )
        other = SimpleNamespace(id=other_id)
        db = MagicMock()
        with patch.object(xfer, "_lock_station", return_value=station):
            result = xfer.approve_stake_transfer(
                db, station, other, proposal_id, FIXED_NOW
            )
        assert result["proposal"]["status"] == "applied"
        # Alice 40 + Bob 40 = 80 > 40
        assert result["proposal"]["approving_weight"] == 80
        by_id = {s["player_id"]: s["pct"] for s in result["shares"]}
        assert by_id[str(owner_id)] == 40
        assert by_id[str(other_id)] == 40
        assert by_id[str(target_id)] == 20

    def test_approve_non_stakeholder_403(self):
        station, _, _, _, proposal_id = self._pending_proposal_station()
        stranger = SimpleNamespace(id=uuid4())
        db = MagicMock()
        with patch.object(xfer, "_lock_station", return_value=station):
            with pytest.raises(PortOwnershipError) as exc:
                xfer.approve_stake_transfer(
                    db, station, stranger, proposal_id, FIXED_NOW
                )
        assert exc.value.status_code == 403

    def test_reject_closes_pending(self):
        station, _, other_id, _, proposal_id = self._pending_proposal_station()
        other = SimpleNamespace(id=other_id)
        db = MagicMock()
        with patch.object(xfer, "_lock_station", return_value=station):
            result = xfer.reject_stake_transfer(
                db, station, other, proposal_id, FIXED_NOW
            )
        assert result["proposal"]["status"] == "rejected"
        stored = station.ownership[xfer.STAKE_TRANSFERS_KEY][0]
        assert stored["status"] == "rejected"
        # Shares unchanged
        shares = station.ownership["co_ownership_shares"]
        assert sum(int(s["pct"]) for s in shares) == 100
