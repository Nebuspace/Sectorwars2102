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


class TestWithdrawTreasurySyndicate:
    """LEG-1997 Soft-ORDER invent=0 — per-share treasury withdrawal."""

    def test_solo_path_unchanged(self):
        owner_id = uuid4()
        station = SimpleNamespace(
            id=uuid4(),
            owner_id=owner_id,
            ownership={po.SYNDICATE_MODE_KEY: "solo"},
            treasury_balance=10_000,
        )
        owner = SimpleNamespace(id=owner_id, credits=100)
        db = MagicMock()
        with patch.object(po, "_lock_station", return_value=station):
            with patch.object(
                po, "_lock_players_ascending", return_value={owner_id: owner}
            ):
                result = po.withdraw_treasury(db, station, owner, 1_000)
        assert result["withdrawn"] == 1_000
        assert result["mode"] == "solo"
        assert result["treasury_balance"] == 9_000
        assert owner.credits == 1_100
        assert result["distributions"] == [
            {"player_id": str(owner_id), "pct": 100, "credits": 1_000}
        ]

    def test_syndicate_60_40_exact_split(self):
        owner_id = uuid4()
        partner_id = uuid4()
        station = SimpleNamespace(
            id=uuid4(),
            owner_id=owner_id,
            ownership={
                po.SYNDICATE_MODE_KEY: "syndicate",
                po.SYNDICATE_SHARES_KEY: [
                    {"player_id": str(owner_id), "pct": 60},
                    {"player_id": str(partner_id), "pct": 40},
                ],
            },
            treasury_balance=10_000,
        )
        owner = SimpleNamespace(id=owner_id, credits=0)
        partner = SimpleNamespace(id=partner_id, credits=0)
        db = MagicMock()
        with patch.object(po, "_lock_station", return_value=station):
            with patch.object(
                po,
                "_lock_players_ascending",
                return_value={owner_id: owner, partner_id: partner},
            ):
                result = po.withdraw_treasury(db, station, owner, 1_000)
        assert result["mode"] == "syndicate"
        assert result["withdrawn"] == 1_000
        assert result["treasury_balance"] == 9_000
        by_id = {d["player_id"]: d for d in result["distributions"]}
        assert by_id[str(owner_id)]["credits"] == 600
        assert by_id[str(partner_id)]["credits"] == 400
        assert owner.credits == 600
        assert partner.credits == 400
        assert sum(d["credits"] for d in result["distributions"]) == 1_000

    def test_syndicate_remainder_to_primary(self):
        owner_id = uuid4()
        partner_id = uuid4()
        station = SimpleNamespace(
            id=uuid4(),
            owner_id=owner_id,
            ownership={
                po.SYNDICATE_MODE_KEY: "syndicate",
                po.SYNDICATE_SHARES_KEY: [
                    {"player_id": str(owner_id), "pct": 60},
                    {"player_id": str(partner_id), "pct": 40},
                ],
            },
            treasury_balance=10_000,
        )
        owner = SimpleNamespace(id=owner_id, credits=0)
        partner = SimpleNamespace(id=partner_id, credits=0)
        db = MagicMock()
        # 101 * 60 // 100 = 60; 101 * 40 // 100 = 40; remainder 1 → primary
        with patch.object(po, "_lock_station", return_value=station):
            with patch.object(
                po,
                "_lock_players_ascending",
                return_value={owner_id: owner, partner_id: partner},
            ):
                result = po.withdraw_treasury(db, station, owner, 101)
        by_id = {d["player_id"]: d["credits"] for d in result["distributions"]}
        assert by_id[str(owner_id)] == 61
        assert by_id[str(partner_id)] == 40
        assert sum(by_id.values()) == 101

    def test_non_owner_forbidden(self):
        owner_id = uuid4()
        station = SimpleNamespace(
            id=uuid4(), owner_id=owner_id, ownership={}, treasury_balance=10_000
        )
        stranger = SimpleNamespace(id=uuid4(), credits=0)
        db = MagicMock()
        with patch.object(po, "_lock_station", return_value=station):
            with pytest.raises(PortOwnershipError) as exc:
                po.withdraw_treasury(db, station, stranger, 100)
        assert exc.value.status_code == 403


class TestInjectTreasury:
    """LEG-2000 Soft-ORDER invent=0 — owner cash-injection."""

    def test_inject_success(self):
        owner_id = uuid4()
        station = SimpleNamespace(
            id=uuid4(), owner_id=owner_id, ownership={}, treasury_balance=500
        )
        owner = SimpleNamespace(id=owner_id, credits=2_000)
        db = MagicMock()
        with patch.object(po, "_lock_station", return_value=station):
            with patch.object(
                po, "_lock_players_ascending", return_value={owner_id: owner}
            ):
                result = po.inject_treasury(db, station, owner, 750)
        assert result["injected"] == 750
        assert result["treasury_balance"] == 1_250
        assert owner.credits == 1_250
        assert station.treasury_balance == 1_250

    def test_insufficient_credits(self):
        owner_id = uuid4()
        station = SimpleNamespace(
            id=uuid4(), owner_id=owner_id, ownership={}, treasury_balance=0
        )
        owner = SimpleNamespace(id=owner_id, credits=50)
        db = MagicMock()
        with patch.object(po, "_lock_station", return_value=station):
            with patch.object(
                po, "_lock_players_ascending", return_value={owner_id: owner}
            ):
                with pytest.raises(PortOwnershipError) as exc:
                    po.inject_treasury(db, station, owner, 100)
        assert exc.value.status_code == 400
        assert "insufficient" in exc.value.detail.lower()

    def test_non_owner_forbidden(self):
        owner_id = uuid4()
        station = SimpleNamespace(
            id=uuid4(), owner_id=owner_id, ownership={}, treasury_balance=0
        )
        stranger = SimpleNamespace(id=uuid4(), credits=500)
        db = MagicMock()
        with patch.object(po, "_lock_station", return_value=station):
            with pytest.raises(PortOwnershipError) as exc:
                po.inject_treasury(db, station, stranger, 100)
        assert exc.value.status_code == 403
