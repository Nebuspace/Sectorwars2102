"""
Unit tests for the citizen-facing regional governance API (WO-REGOV-CITIZEN-API).

Before this slice, GET /my-region/policies|elections|treaties and POST
/my-region/policies were ALL owner-scoped — a non-owner region member had no
route to discover a single policy/election/treaty id. These tests exercise
the four NEW member-scoped routes (regional_governance.py) by calling the
route coroutines directly with fake Depends args (the "direct-call" pattern
already used for this file's thin, service-backed handlers) rather than
spinning up a real DB or TestClient. `RegionalGovernanceService` is patched
at the boundary for the pure discovery/gating tests; the policy-proposal
round-trip test instead runs the REAL service methods against a small
in-memory fake session so the POST -> GET discoverability claim is actually
exercised end-to-end, not merely asserted via mocks. Also regression-covers
the existing owner POST /my-region/policies route now that
policy_proposal_rules validation is wired into it too.
"""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes.regional_governance import (
    PolicyCreate,
    create_policy,
    create_policy_proposal_for_member,
    list_region_elections_for_member,
    list_region_policies_for_member,
    list_region_treaties_for_member,
)
from src.models.player import Player
from src.models.region import PolicyStatus, Region, RegionalElection, RegionalPolicy
from src.models.user import User
from src.services import policy_proposal_rules
from src.services.regional_governance_service import RegionalGovernanceService


def _fake_region(owner_id=None) -> Region:
    return Region(
        id=uuid.uuid4(),
        name=f"region-{uuid.uuid4().hex[:8]}",
        display_name="Test Region",
        owner_id=owner_id or uuid.uuid4(),
        voting_threshold=Decimal("0.51"),
    )


def _fake_player() -> Player:
    return Player(id=uuid.uuid4(), user_id=uuid.uuid4())


def _fake_db_for_reads(region, player) -> MagicMock:
    """A fake db satisfying _get_region_by_id (db.scalar) + _get_current_player
    (db.execute().scalar_one_or_none()) for a single member-route call. `region`
    may be None to exercise the 404 (region-not-found) path."""
    fake_db = MagicMock()
    fake_db.scalar = AsyncMock(return_value=region)
    fake_db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=player))
    )
    return fake_db


class _FakeProposalDB:
    """A minimal in-memory stand-in for AsyncSession that lets the REAL
    RegionalGovernanceService.create_policy_proposal + get_regional_policies
    run unmocked, so the round-trip test below proves an actual POST -> GET
    discoverability chain rather than a mocked assertion. Serves both the
    region-by-id `scalar()` lookup and the `execute()` calls used by the
    player lookup (scalar_one_or_none) and the policy list (scalars().all()):
    this fixture only ever exercises a single region, so no query-filter
    interpretation is needed — every execute() just reflects current state."""

    def __init__(self, region: Region, player: Player):
        self._region = region
        self._player = player
        self.policies: list = []

    def add(self, obj):
        # The RegionalPolicy constructor doesn't populate columns whose
        # defaults are only applied by the ORM at a REAL flush/INSERT
        # (id=Column(..., default=uuid.uuid4), proposed_at=server_default=
        # func.now(), votes_for/votes_against=default=0). This fake never
        # flushes against an engine, so backfill them here the same way a
        # real INSERT would, matching test_regional_governance.py's
        # `mock_add` pattern for the same underlying gap.
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if getattr(obj, "proposed_at", None) is None:
            obj.proposed_at = datetime.utcnow()
        if getattr(obj, "votes_for", None) is None:
            obj.votes_for = 0
        if getattr(obj, "votes_against", None) is None:
            obj.votes_against = 0
        self.policies.append(obj)

    async def commit(self):
        return None

    async def refresh(self, obj):
        return None

    async def scalar(self, stmt):
        return self._region

    async def execute(self, stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = self._player
        result.scalars.return_value.all.return_value = list(self.policies)
        return result


# ---------------------------------------------------------------------------
# Member-scoped reads: 404 (region not found) / 403 (authenticated non-member)
# ---------------------------------------------------------------------------

class TestMemberGovernanceReadsAuthGates:
    @pytest.mark.asyncio
    async def test_list_policies_region_not_found_404(self):
        user = User(id=uuid.uuid4())
        fake_db = _fake_db_for_reads(region=None, player=_fake_player())
        with pytest.raises(HTTPException) as exc:
            await list_region_policies_for_member(uuid.uuid4(), user, fake_db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_list_policies_non_member_403(self):
        region = _fake_region()
        player = _fake_player()
        user = User(id=player.user_id)
        fake_db = _fake_db_for_reads(region, player)
        with patch.object(
            RegionalGovernanceService, "get_membership_status",
            new=AsyncMock(return_value={"is_member": False, "can_vote": False}),
        ), pytest.raises(HTTPException) as exc:
            await list_region_policies_for_member(region.id, user, fake_db)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_list_elections_region_not_found_404(self):
        user = User(id=uuid.uuid4())
        fake_db = _fake_db_for_reads(region=None, player=_fake_player())
        with pytest.raises(HTTPException) as exc:
            await list_region_elections_for_member(uuid.uuid4(), user, fake_db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_list_elections_non_member_403(self):
        region = _fake_region()
        player = _fake_player()
        user = User(id=player.user_id)
        fake_db = _fake_db_for_reads(region, player)
        with patch.object(
            RegionalGovernanceService, "get_membership_status",
            new=AsyncMock(return_value={"is_member": False, "can_vote": False}),
        ), pytest.raises(HTTPException) as exc:
            await list_region_elections_for_member(region.id, user, fake_db)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_list_treaties_region_not_found_404(self):
        user = User(id=uuid.uuid4())
        fake_db = _fake_db_for_reads(region=None, player=_fake_player())
        with pytest.raises(HTTPException) as exc:
            await list_region_treaties_for_member(uuid.uuid4(), user, fake_db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_list_treaties_non_member_403(self):
        region = _fake_region()
        player = _fake_player()
        user = User(id=player.user_id)
        fake_db = _fake_db_for_reads(region, player)
        with patch.object(
            RegionalGovernanceService, "get_membership_status",
            new=AsyncMock(return_value={"is_member": False, "can_vote": False}),
        ), pytest.raises(HTTPException) as exc:
            await list_region_treaties_for_member(region.id, user, fake_db)
        assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Member-scoped reads: a citizen sees the collections + their ids
# ---------------------------------------------------------------------------

class TestMemberGovernanceReadsSeeIds:
    @pytest.mark.asyncio
    async def test_citizen_lists_policies_with_ids(self):
        region = _fake_region()
        player = _fake_player()
        user = User(id=player.user_id)
        fake_db = _fake_db_for_reads(region, player)
        policy = RegionalPolicy(
            id=uuid.uuid4(),
            region_id=region.id,
            policy_type="tax_rate",
            title="Lower taxes",
            description=None,
            proposed_changes={"tax_rate": 0.10},
            proposed_by=player.id,
            proposed_at=datetime.utcnow(),
            voting_closes_at=datetime.utcnow() + timedelta(days=7),
            votes_for=0,
            votes_against=0,
            status=PolicyStatus.VOTING,
        )
        with patch.object(
            RegionalGovernanceService, "get_membership_status",
            new=AsyncMock(return_value={"is_member": True, "can_vote": True}),
        ), patch.object(
            RegionalGovernanceService, "get_regional_policies",
            new=AsyncMock(return_value=[policy]),
        ):
            result = await list_region_policies_for_member(region.id, user, fake_db)
        assert len(result) == 1
        assert result[0]["id"] == str(policy.id)
        assert result[0]["status"] == PolicyStatus.VOTING

    @pytest.mark.asyncio
    async def test_citizen_lists_elections_with_ids(self):
        region = _fake_region()
        player = _fake_player()
        user = User(id=player.user_id)
        fake_db = _fake_db_for_reads(region, player)
        election = RegionalElection(
            id=uuid.uuid4(),
            region_id=region.id,
            position="governor",
            candidates=[],
            voting_opens_at=datetime.utcnow(),
            voting_closes_at=datetime.utcnow() + timedelta(days=7),
            results=None,
        )
        with patch.object(
            RegionalGovernanceService, "get_membership_status",
            new=AsyncMock(return_value={"is_member": True, "can_vote": True}),
        ), patch.object(
            RegionalGovernanceService, "get_regional_elections",
            new=AsyncMock(return_value=[election]),
        ):
            result = await list_region_elections_for_member(region.id, user, fake_db)
        assert len(result) == 1
        assert result[0]["id"] == str(election.id)
        assert result[0]["position"] == "governor"

    @pytest.mark.asyncio
    async def test_citizen_lists_treaties_with_ids_and_redacted_terms(self):
        """NO-CANON: member-facing treaty reads redact `terms` (citizens see
        type/partner/status/expiry, not the negotiated terms)."""
        region = _fake_region()
        player = _fake_player()
        user = User(id=player.user_id)
        fake_db = _fake_db_for_reads(region, player)
        treaty_id = str(uuid.uuid4())
        service_treaty = {
            "id": treaty_id,
            "partner_region": "Neighbor Region",
            "treaty_type": "trade_agreement",
            "terms": {"secret_clause": "no one should see this"},
            "signed_at": datetime.utcnow().isoformat(),
            "expires_at": None,
            "status": "active",
        }
        with patch.object(
            RegionalGovernanceService, "get_membership_status",
            new=AsyncMock(return_value={"is_member": True, "can_vote": True}),
        ), patch.object(
            RegionalGovernanceService, "get_regional_treaties",
            new=AsyncMock(return_value=[service_treaty]),
        ):
            result = await list_region_treaties_for_member(region.id, user, fake_db)
        assert len(result) == 1
        assert result[0]["id"] == treaty_id
        assert result[0]["partner_region"] == "Neighbor Region"
        assert result[0]["status"] == "active"
        assert "terms" not in result[0]


# ---------------------------------------------------------------------------
# Member-scoped policy proposal: auth gates, validation, round-trip discovery
# ---------------------------------------------------------------------------

class TestMemberPolicyProposal:
    @pytest.mark.asyncio
    async def test_propose_region_not_found_404(self):
        user = User(id=uuid.uuid4())
        fake_db = _fake_db_for_reads(region=None, player=_fake_player())
        body = PolicyCreate(policy_type="tax_rate", title="X", proposed_changes={})
        with pytest.raises(HTTPException) as exc:
            await create_policy_proposal_for_member(uuid.uuid4(), body, user, fake_db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_propose_non_member_403(self):
        region = _fake_region()
        player = _fake_player()
        user = User(id=player.user_id)
        fake_db = _fake_db_for_reads(region, player)
        body = PolicyCreate(policy_type="tax_rate", title="X", proposed_changes={})
        with patch.object(
            RegionalGovernanceService, "get_membership_status",
            new=AsyncMock(return_value={"is_member": False, "can_vote": False}),
        ), pytest.raises(HTTPException) as exc:
            await create_policy_proposal_for_member(region.id, body, user, fake_db)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_propose_member_not_vote_eligible_403(self):
        """A visitor is a member (is_member True) but not vote-eligible — the
        canon target is regional-reputation >= 100; the built stand-in is
        can_vote (citizen/resident membership, region.py:260-263)."""
        region = _fake_region()
        player = _fake_player()
        user = User(id=player.user_id)
        fake_db = _fake_db_for_reads(region, player)
        body = PolicyCreate(policy_type="tax_rate", title="X", proposed_changes={})
        with patch.object(
            RegionalGovernanceService, "get_membership_status",
            new=AsyncMock(return_value={"is_member": True, "can_vote": False}),
        ), pytest.raises(HTTPException) as exc:
            await create_policy_proposal_for_member(region.id, body, user, fake_db)
        assert exc.value.status_code == 403
        assert exc.value.detail == "ERR_NOT_ELIGIBLE"

    @pytest.mark.asyncio
    async def test_propose_unknown_key_rejected_400_no_row_written(self):
        region = _fake_region()
        player = _fake_player()
        user = User(id=player.user_id)
        fake_db = _fake_db_for_reads(region, player)
        body = PolicyCreate(
            policy_type="tax_rate", title="X",
            proposed_changes={"bogus_key": 1},
        )
        with patch.object(
            RegionalGovernanceService, "get_membership_status",
            new=AsyncMock(return_value={"is_member": True, "can_vote": True}),
        ), patch.object(
            RegionalGovernanceService, "create_policy_proposal",
            new=AsyncMock(),
        ) as mock_create, pytest.raises(HTTPException) as exc:
            await create_policy_proposal_for_member(region.id, body, user, fake_db)
        assert exc.value.status_code == 400
        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_propose_out_of_band_value_rejected_400(self):
        """governance_quorum_pct=0.9 is above the ADR-0059 [0.25, 0.60] band."""
        region = _fake_region()
        player = _fake_player()
        user = User(id=player.user_id)
        fake_db = _fake_db_for_reads(region, player)
        body = PolicyCreate(
            policy_type="governance_change", title="X",
            proposed_changes={"governance_quorum_pct": 0.9},
        )
        with patch.object(
            RegionalGovernanceService, "get_membership_status",
            new=AsyncMock(return_value={"is_member": True, "can_vote": True}),
        ), patch.object(
            RegionalGovernanceService, "create_policy_proposal",
            new=AsyncMock(),
        ) as mock_create, pytest.raises(HTTPException) as exc:
            await create_policy_proposal_for_member(region.id, body, user, fake_db)
        assert exc.value.status_code == 400
        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_citizen_proposal_creates_voting_policy_discoverable_via_list(self):
        """End-to-end (real, unmocked service methods): a citizen POST creates
        a VOTING policy that is then discoverable via the new GET list route."""
        region = _fake_region()
        player = _fake_player()
        user = User(id=player.user_id)
        fake_db = _FakeProposalDB(region, player)
        body = PolicyCreate(
            policy_type="tax_rate", title="Lower taxes",
            proposed_changes={"tax_rate": 0.10},
        )
        with patch.object(
            RegionalGovernanceService, "get_membership_status",
            new=AsyncMock(return_value={"is_member": True, "can_vote": True}),
        ):
            post_result = await create_policy_proposal_for_member(
                region.id, body, user, fake_db
            )
            list_result = await list_region_policies_for_member(
                region.id, user, fake_db
            )
        assert "policy_id" in post_result
        assert len(list_result) == 1
        assert list_result[0]["id"] == post_result["policy_id"]
        assert list_result[0]["status"] == PolicyStatus.VOTING
        assert list_result[0]["proposed_changes"] == {"tax_rate": 0.10}


# ---------------------------------------------------------------------------
# Owner POST /my-region/policies regression — validator wiring must not
# reject legitimate proposals, and must reject the same invalid ones as the
# member path.
# ---------------------------------------------------------------------------

class TestOwnerCreatePolicyRegression:
    def _fake_owner_db(self, region, player) -> MagicMock:
        """verify_region_owner + the in-route player lookup both go through
        db.execute().scalar_one_or_none() in that order."""
        fake_db = MagicMock()
        fake_db.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar_one_or_none=MagicMock(return_value=region)),
                MagicMock(scalar_one_or_none=MagicMock(return_value=player)),
            ]
        )
        fake_db.add = MagicMock()
        fake_db.commit = AsyncMock()
        fake_db.refresh = AsyncMock()
        return fake_db

    @pytest.mark.asyncio
    async def test_owner_valid_proposal_still_succeeds(self):
        player = _fake_player()
        region = _fake_region(owner_id=player.user_id)
        user = User(id=player.user_id)
        fake_db = self._fake_owner_db(region, player)
        body = PolicyCreate(
            policy_type="tax_rate", title="Lower taxes",
            proposed_changes={"tax_rate": 0.10},
        )
        result = await create_policy(body, user, fake_db)
        assert "policy_id" in result
        fake_db.add.assert_called_once()
        fake_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_owner_unknown_key_rejected_400_no_row_written(self):
        player = _fake_player()
        region = _fake_region(owner_id=player.user_id)
        user = User(id=player.user_id)
        fake_db = self._fake_owner_db(region, player)
        body = PolicyCreate(
            policy_type="tax_rate", title="X",
            proposed_changes={"bogus_key": 1},
        )
        with pytest.raises(HTTPException) as exc:
            await create_policy(body, user, fake_db)
        assert exc.value.status_code == 400
        fake_db.add.assert_not_called()
        fake_db.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# policy_proposal_rules.validate_proposed_changes — pure-function unit tests
# ---------------------------------------------------------------------------

class TestValidateProposedChanges:
    def test_empty_changes_are_valid(self):
        assert policy_proposal_rules.validate_proposed_changes({}) == []

    def test_known_keys_in_band_are_valid(self):
        errors = policy_proposal_rules.validate_proposed_changes({
            "tax_rate": 0.15,
            "voting_threshold": 0.6,
            "election_frequency_days": 90,
            "governance_type": "democracy",
            "governance_quorum_pct": 0.4,
            "trade_bonuses": {"ore": 1.5, "tariff_rate": 0.9},
        })
        assert errors == []

    def test_unknown_top_level_key_rejected(self):
        errors = policy_proposal_rules.validate_proposed_changes({"bogus_key": 1})
        assert any("bogus_key" in e for e in errors)

    def test_governance_quorum_pct_out_of_band_rejected(self):
        errors = policy_proposal_rules.validate_proposed_changes(
            {"governance_quorum_pct": 0.9}
        )
        assert any("governance_quorum_pct" in e for e in errors)

    def test_invalid_governance_type_rejected(self):
        errors = policy_proposal_rules.validate_proposed_changes(
            {"governance_type": "anarchy"}
        )
        assert any("governance_type" in e for e in errors)

    def test_trade_bonus_out_of_band_rejected(self):
        errors = policy_proposal_rules.validate_proposed_changes(
            {"trade_bonuses": {"ore": 5.0}}
        )
        assert any("trade_bonuses" in e for e in errors)
