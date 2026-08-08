"""Unit tests for WO-SYNDICATE-CO-OWNERSHIP thin v1."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.services import port_ownership_service as po
from src.services.port_ownership_service import PortOwnershipError

UTC = timezone.utc
FIXED_NOW = datetime(2102, 6, 1, 12, 0, 0, tzinfo=UTC)


class TestWouldOversubscribe:
    def test_allows_under_cap(self):
        primary = "p1"
        shares = [{"player_id": primary, "pct": 70}, {"player_id": "p2", "pct": 30}]
        invites = [{"invitee_player_id": "p3", "pct": 20}]
        # accepted invitees 30 + pending 20 + new 40 = 90 ≤ 99
        assert po.would_oversubscribe(shares, invites, primary, 40) is False

    def test_rejects_when_pending_plus_accepted_exceed_invitee_cap(self):
        primary = "p1"
        shares = [{"player_id": primary, "pct": 60}, {"player_id": "p2", "pct": 40}]
        invites = [{"invitee_player_id": "p3", "pct": 50}]
        # 40 + 50 + 10 = 100 > 99
        assert po.would_oversubscribe(shares, invites, primary, 10) is True

    def test_accepted_invitee_pct_ignores_primary(self):
        shares = [
            {"player_id": "primary", "pct": 80},
            {"player_id": "a", "pct": 15},
            {"player_id": "b", "pct": 5},
        ]
        assert po.accepted_invitee_pct_total(shares, "primary") == 20


class TestIssueShareInvite:
    def test_non_owner_forbidden(self):
        owner_id = uuid4()
        station = SimpleNamespace(
            id=uuid4(), owner_id=owner_id, ownership={}, treasury_balance=0
        )
        stranger = SimpleNamespace(id=uuid4())
        db = MagicMock()
        with patch.object(po, "_lock_station", return_value=station):
            with pytest.raises(PortOwnershipError) as exc:
                po.issue_share_invite(db, station, stranger, uuid4(), 10, FIXED_NOW)
        assert exc.value.status_code == 403

    def test_issues_pending_invite(self):
        owner_id = uuid4()
        invitee_id = uuid4()
        station = SimpleNamespace(
            id=uuid4(),
            owner_id=owner_id,
            ownership={"acquisition_cost": 1_000_000},
            treasury_balance=50_000,
        )
        owner = SimpleNamespace(id=owner_id)
        invitee = SimpleNamespace(id=invitee_id)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = invitee
        with patch.object(po, "_lock_station", return_value=station):
            result = po.issue_share_invite(
                db, station, owner, invitee_id, 25, FIXED_NOW
            )
        assert result["invite"]["pct"] == 25
        assert str(result["invite"]["invitee_player_id"]) == str(invitee_id)
        invites = station.ownership[po.SYNDICATE_INVITES_KEY]
        assert len(invites) == 1
        assert invites[0]["pct"] == 25

    def test_rejects_oversubscribe_across_pending(self):
        owner_id = uuid4()
        station = SimpleNamespace(
            id=uuid4(),
            owner_id=owner_id,
            ownership={
                po.SYNDICATE_INVITES_KEY: [
                    {
                        "invite_id": "x",
                        "invitee_player_id": str(uuid4()),
                        "pct": 60,
                        "expires_at": (FIXED_NOW + timedelta(days=3)).isoformat(),
                    }
                ]
            },
            treasury_balance=0,
        )
        owner = SimpleNamespace(id=owner_id)
        invitee_id = uuid4()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
            id=invitee_id
        )
        with patch.object(po, "_lock_station", return_value=station):
            with pytest.raises(PortOwnershipError) as exc:
                po.issue_share_invite(db, station, owner, invitee_id, 50, FIXED_NOW)
        assert exc.value.status_code == 400
        assert "exceed" in exc.value.detail.lower()


class TestAcceptShareInvite:
    def test_accept_splits_stake_and_charges_fee(self):
        owner_id = uuid4()
        invitee_id = uuid4()
        invite_id = str(uuid4())
        station = SimpleNamespace(
            id=uuid4(),
            owner_id=owner_id,
            ownership={
                "acquisition_cost": 1_000_000,
                po.SYNDICATE_INVITES_KEY: [
                    {
                        "invite_id": invite_id,
                        "invitee_player_id": str(invitee_id),
                        "pct": 40,
                        "expires_at": (FIXED_NOW + timedelta(days=3)).isoformat(),
                    }
                ],
            },
            treasury_balance=50_000,
        )
        invitee = SimpleNamespace(id=invitee_id)
        db = MagicMock()
        with patch.object(po, "_lock_station", return_value=station):
            result = po.accept_share_invite(
                db, station, invitee, invite_id, FIXED_NOW
            )
        assert result["mode"] == "syndicate"
        assert result["conversion_fee"] == 10_000  # 1% of 1_000_000
        assert station.treasury_balance == 40_000
        assert station.owner_id == owner_id  # primary permanent in v1
        by_id = {s["player_id"]: s["pct"] for s in result["shares"]}
        assert by_id[str(owner_id)] == 60
        assert by_id[str(invitee_id)] == 40
        assert station.ownership[po.SYNDICATE_MODE_KEY] == "syndicate"
        assert station.ownership.get(po.SYNDICATE_INVITES_KEY) == []

    def test_accept_fails_if_treasury_short(self):
        owner_id = uuid4()
        invitee_id = uuid4()
        invite_id = str(uuid4())
        station = SimpleNamespace(
            id=uuid4(),
            owner_id=owner_id,
            ownership={
                "acquisition_cost": 1_000_000,
                po.SYNDICATE_INVITES_KEY: [
                    {
                        "invite_id": invite_id,
                        "invitee_player_id": str(invitee_id),
                        "pct": 10,
                        "expires_at": (FIXED_NOW + timedelta(days=3)).isoformat(),
                    }
                ],
            },
            treasury_balance=100,  # fee needs 10_000
        )
        invitee = SimpleNamespace(id=invitee_id)
        db = MagicMock()
        with patch.object(po, "_lock_station", return_value=station):
            with pytest.raises(PortOwnershipError) as exc:
                po.accept_share_invite(db, station, invitee, invite_id, FIXED_NOW)
        assert exc.value.status_code == 400
        assert "treasury" in exc.value.detail.lower()

    def test_non_invitee_cannot_accept(self):
        owner_id = uuid4()
        invitee_id = uuid4()
        invite_id = str(uuid4())
        station = SimpleNamespace(
            id=uuid4(),
            owner_id=owner_id,
            ownership={
                po.SYNDICATE_INVITES_KEY: [
                    {
                        "invite_id": invite_id,
                        "invitee_player_id": str(invitee_id),
                        "pct": 10,
                        "expires_at": (FIXED_NOW + timedelta(days=3)).isoformat(),
                    }
                ],
            },
            treasury_balance=100_000,
        )
        stranger = SimpleNamespace(id=uuid4())
        db = MagicMock()
        with patch.object(po, "_lock_station", return_value=station):
            with pytest.raises(PortOwnershipError) as exc:
                po.accept_share_invite(db, station, stranger, invite_id, FIXED_NOW)
        assert exc.value.status_code == 403


class TestDeclineShareInvite:
    def test_decline_removes_invite(self):
        owner_id = uuid4()
        invitee_id = uuid4()
        invite_id = str(uuid4())
        station = SimpleNamespace(
            id=uuid4(),
            owner_id=owner_id,
            ownership={
                po.SYNDICATE_INVITES_KEY: [
                    {
                        "invite_id": invite_id,
                        "invitee_player_id": str(invitee_id),
                        "pct": 15,
                        "expires_at": (FIXED_NOW + timedelta(days=3)).isoformat(),
                    }
                ],
            },
            treasury_balance=0,
        )
        invitee = SimpleNamespace(id=invitee_id)
        db = MagicMock()
        with patch.object(po, "_lock_station", return_value=station):
            result = po.decline_share_invite(
                db, station, invitee, invite_id, FIXED_NOW
            )
        assert result["declined_invite_id"] == invite_id
        assert station.ownership.get(po.SYNDICATE_INVITES_KEY) == []
