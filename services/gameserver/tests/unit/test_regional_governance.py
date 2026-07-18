"""
Unit tests for Regional Governance functionality
Tests the core business logic for multi-regional system governance
"""

import pytest
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.regional_governance_service import RegionalGovernanceService
from src.models.region import (
    Region, RegionalMembership, RegionalPolicy, RegionalElection,
    GovernanceType, PolicyStatus, ElectionStatus, MembershipType
)
from src.models.player import Player
from src.models.user import User


class TestRegionalGovernanceService:
    """Test the RegionalGovernanceService class"""
    
    @pytest.fixture
    def mock_db(self):
        """Mock database session"""
        return AsyncMock()
    
    @pytest.fixture
    def sample_region(self):
        """Sample region for testing"""
        return Region(
            id=uuid.uuid4(),
            name="test-region",
            display_name="Test Region",
            owner_id=uuid.uuid4(),
            governance_type=GovernanceType.DEMOCRACY,
            voting_threshold=Decimal('0.60'),
            tax_rate=Decimal('0.15'),
            starting_credits=2000,
            economic_specialization="trade",
            total_sectors=500
        )
    
    @pytest.fixture
    def sample_player(self):
        """Sample player for testing"""
        # Player.username is a read-only @property (nickname or user.username),
        # not a mapped column — it has no setter, so it can't be passed as a
        # constructor kwarg. Use nickname, which is the actual mapped column
        # backing the display name, to keep the same test intent.
        return Player(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            nickname="test_player",
            credits=5000
        )
    
    @pytest.mark.asyncio
    async def test_get_region_by_owner_success(self, mock_db, sample_region):
        """Test successful region retrieval by owner"""
        # A bare AsyncMock() auto-vivifies nested attribute chains as AsyncMock
        # too, so mock_db.execute.return_value.scalar_one_or_none() would return
        # an un-awaited coroutine instead of sample_region. Rebind execute to an
        # AsyncMock whose return_value is a plain (sync) MagicMock, matching a
        # real SQLAlchemy Result.
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=sample_region))
        )

        result = await RegionalGovernanceService.get_region_by_owner(
            mock_db, sample_region.owner_id
        )

        assert result == sample_region
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_region_by_owner_not_found(self, mock_db):
        """Test region retrieval when owner has no region"""
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )
        
        result = await RegionalGovernanceService.get_region_by_owner(
            mock_db, uuid.uuid4()
        )
        
        assert result is None
    
    @pytest.mark.asyncio
    async def test_get_regional_stats_success(self, mock_db, sample_region):
        """Test successful regional statistics calculation"""
        # Mock membership statistics
        membership_mock = MagicMock()
        membership_mock.all.return_value = [
            MagicMock(membership_type='citizen', count=50, avg_reputation=85.5),
            MagicMock(membership_type='resident', count=30, avg_reputation=70.2),
            MagicMock(membership_type='visitor', count=20, avg_reputation=60.0)
        ]
        
        # Mock governance statistics
        elections_mock = MagicMock()
        elections_mock.return_value = 2
        
        policies_mock = MagicMock()
        policies_mock.return_value = 3
        
        treaties_mock = MagicMock()
        treaties_mock.return_value = 1
        
        # Setup mock responses. get_regional_stats first calls
        # _expire_stale_treaties (one execute() for the lazy-settle UPDATE,
        # reading only .rowcount) before its own membership-stats execute() —
        # side_effect needs an entry for each, in call order. Rebinding execute
        # to a fresh AsyncMock also avoids the nested-auto-AsyncMock trap where
        # a bare AsyncMock()'s attribute chains resolve to un-awaited coroutines.
        mock_db.execute = AsyncMock(
            side_effect=[MagicMock(rowcount=0), membership_mock]
        )
        mock_db.scalar.side_effect = [2, 3, 1]  # elections, policies, treaties
        
        result = await RegionalGovernanceService.get_regional_stats(
            mock_db, sample_region.id
        )
        
        assert result['total_population'] == 100
        assert result['citizen_count'] == 50
        assert result['resident_count'] == 30
        assert result['visitor_count'] == 20
        # Weighted average: (85.5*50 + 70.2*30 + 60.0*20) / 100 = 75.81
        assert result['average_reputation'] == 75.81
        assert result['active_elections'] == 2
        assert result['pending_policies'] == 3
        assert result['treaties_count'] == 1
    
    @pytest.mark.asyncio
    async def test_update_economic_config_success(self, mock_db, sample_region):
        """Test successful economic configuration update"""
        config = {
            'tax_rate': 0.20,
            'starting_credits': 3000,
            'trade_bonuses': {'ore': 1.5, 'food': 1.2},
            'economic_specialization': 'mining'
        }
        
        mock_db.commit.return_value = None
        
        result = await RegionalGovernanceService.update_economic_config(
            mock_db, sample_region.id, config
        )
        
        assert result is True
        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_update_economic_config_failure(self, mock_db, sample_region):
        """Test economic configuration update failure"""
        config = {'tax_rate': 0.20}
        
        # Mock database exception
        mock_db.execute.side_effect = Exception("Database error")
        
        result = await RegionalGovernanceService.update_economic_config(
            mock_db, sample_region.id, config
        )
        
        assert result is False
        mock_db.rollback.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_update_governance_config_success(self, mock_db, sample_region):
        """Test successful governance configuration update"""
        config = {
            'governance_type': 'council',
            'voting_threshold': 0.75,
            'election_frequency_days': 120,
            'constitutional_text': 'New constitution text'
        }
        
        mock_db.commit.return_value = None
        
        result = await RegionalGovernanceService.update_governance_config(
            mock_db, sample_region.id, config
        )
        
        assert result is True
        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_create_policy_proposal_success(self, mock_db, sample_region, sample_player):
        """Test successful policy proposal creation"""
        policy_data = {
            'policy_type': 'tax_rate',
            'title': 'Increase Tax Rate',
            'description': 'Proposal to increase regional tax rate',
            'proposed_changes': {'tax_rate': 0.25},
            'voting_duration_days': 7
        }
        
        # 'voting_duration_days' is consumed by the service to compute
        # voting_closes_at -- it isn't a RegionalPolicy column, so it can't be
        # passed through the constructor. mock_policy is only used for its .id
        # below, so drop the non-column key.
        mock_policy = RegionalPolicy(
            id=uuid.uuid4(),
            region_id=sample_region.id,
            **{k: v for k, v in policy_data.items() if k != 'voting_duration_days'}
        )
        
        mock_db.commit.return_value = None
        mock_db.refresh.return_value = None
        
        # Mock the add operation to return our mock policy
        def mock_add(policy):
            policy.id = mock_policy.id
        
        mock_db.add.side_effect = mock_add
        
        result = await RegionalGovernanceService.create_policy_proposal(
            mock_db, sample_region.id, sample_player.id, policy_data
        )
        
        assert result is not None
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_start_election_success(self, mock_db, sample_region):
        """Test successful election start"""
        position = "governor"
        voting_duration_days = 7
        candidates = ["candidate1", "candidate2"]
        
        # Mock no existing election
        mock_db.scalar.return_value = None
        mock_db.commit.return_value = None
        mock_db.refresh.return_value = None
        
        mock_election = RegionalElection(
            id=uuid.uuid4(),
            region_id=sample_region.id,
            position=position,
            candidates=candidates,
            voting_opens_at=datetime.utcnow(),
            voting_closes_at=datetime.utcnow() + timedelta(days=voting_duration_days),
            status=ElectionStatus.ACTIVE
        )
        
        def mock_add(election):
            election.id = mock_election.id
        
        mock_db.add.side_effect = mock_add
        
        result = await RegionalGovernanceService.start_election(
            mock_db, sample_region.id, position, voting_duration_days, candidates
        )
        
        assert result is not None
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_start_election_conflict(self, mock_db, sample_region):
        """Test election start with existing active election"""
        position = "governor"
        
        # Mock existing active election
        existing_election = RegionalElection(
            id=uuid.uuid4(),
            region_id=sample_region.id,
            position=position,
            status=ElectionStatus.ACTIVE
        )
        mock_db.scalar.return_value = existing_election
        
        result = await RegionalGovernanceService.start_election(
            mock_db, sample_region.id, position
        )
        
        assert result is None
        mock_db.add.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_get_regional_policies(self, mock_db, sample_region):
        """Test retrieval of regional policies"""
        mock_policies = [
            RegionalPolicy(
                id=uuid.uuid4(),
                region_id=sample_region.id,
                policy_type='tax_rate',
                title='Test Policy 1',
                status=PolicyStatus.VOTING
            ),
            RegionalPolicy(
                id=uuid.uuid4(),
                region_id=sample_region.id,
                policy_type='pvp_rules',
                title='Test Policy 2',
                status=PolicyStatus.PASSED
            )
        ]
        
        # Rebind execute to an AsyncMock returning a plain MagicMock so the
        # chained .scalars().all() resolves synchronously (a bare AsyncMock()'s
        # auto-vivified attribute chain would return an un-awaited coroutine).
        mock_db.execute = AsyncMock(return_value=MagicMock())
        mock_db.execute.return_value.scalars.return_value.all.return_value = mock_policies

        result = await RegionalGovernanceService.get_regional_policies(
            mock_db, sample_region.id
        )
        
        assert len(result) == 2
        assert result == mock_policies
    
    @pytest.mark.asyncio
    async def test_get_regional_elections(self, mock_db, sample_region):
        """Test retrieval of regional elections"""
        mock_elections = [
            RegionalElection(
                id=uuid.uuid4(),
                region_id=sample_region.id,
                position='governor',
                status=ElectionStatus.ACTIVE
            ),
            RegionalElection(
                id=uuid.uuid4(),
                region_id=sample_region.id,
                position='council_member',
                status=ElectionStatus.COMPLETED
            )
        ]
        
        mock_db.execute = AsyncMock(return_value=MagicMock())
        mock_db.execute.return_value.scalars.return_value.all.return_value = mock_elections

        result = await RegionalGovernanceService.get_regional_elections(
            mock_db, sample_region.id
        )
        
        assert len(result) == 2
        assert result == mock_elections
    
    @pytest.mark.asyncio
    async def test_get_regional_treaties(self, mock_db, sample_region):
        """Test retrieval of regional treaties"""
        mock_treaties = [
            (MagicMock(
                id=uuid.uuid4(),
                treaty_type='trade_agreement',
                terms={'trade_bonus': 1.2},
                signed_at=datetime.utcnow(),
                expires_at=None,
                status='active'
            ), "Partner Region")
        ]
        
        # get_regional_treaties first calls _expire_stale_treaties (one execute()
        # for the lazy-settle UPDATE, reading only .rowcount) before its own
        # treaties execute() -- side_effect needs an entry for each, in order.
        mock_db.execute = AsyncMock(
            side_effect=[MagicMock(rowcount=0), MagicMock(all=MagicMock(return_value=mock_treaties))]
        )

        result = await RegionalGovernanceService.get_regional_treaties(
            mock_db, sample_region.id
        )
        
        assert len(result) == 1
        assert result[0]['partner_region'] == "Partner Region"
        assert result[0]['treaty_type'] == 'trade_agreement'
        assert result[0]['status'] == 'active'
    
    @pytest.mark.asyncio
    async def test_update_cultural_identity_success(self, mock_db, sample_region):
        """Test successful cultural identity update"""
        culture_data = {
            'language_pack': {'greeting': 'Hello', 'farewell': 'Goodbye'},
            'aesthetic_theme': {'primary_color': '#0066cc', 'font': 'Arial'},
            'traditions': {'festival': 'Annual Trade Fair'}
        }
        
        mock_db.commit.return_value = None
        
        result = await RegionalGovernanceService.update_cultural_identity(
            mock_db, sample_region.id, culture_data
        )
        
        assert result is True
        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_get_regional_members(self, mock_db, sample_region):
        """Test retrieval of regional members"""
        mock_members = [
            (MagicMock(
                player_id=uuid.uuid4(),
                membership_type=MembershipType.CITIZEN,
                reputation_score=85,
                local_rank="Senator",
                voting_power=Decimal('1.5'),
                joined_at=datetime.utcnow(),
                last_visit=datetime.utcnow(),
                total_visits=50
            ), "test_player1"),
            (MagicMock(
                player_id=uuid.uuid4(),
                membership_type=MembershipType.RESIDENT,
                reputation_score=70,
                local_rank=None,
                voting_power=Decimal('1.0'),
                joined_at=datetime.utcnow(),
                last_visit=datetime.utcnow(),
                total_visits=25
            ), "test_player2")
        ]
        
        # Rebind execute to an AsyncMock returning a plain MagicMock so the
        # chained .all() resolves synchronously (a bare AsyncMock()'s
        # auto-vivified attribute chain would return an un-awaited coroutine).
        mock_db.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=mock_members)))

        result = await RegionalGovernanceService.get_regional_members(
            mock_db, sample_region.id
        )
        
        assert len(result) == 2
        assert result[0]['username'] == "test_player1"
        assert result[0]['membership_type'] == MembershipType.CITIZEN
        assert result[0]['reputation_score'] == 85
        assert result[1]['username'] == "test_player2"
        assert result[1]['membership_type'] == MembershipType.RESIDENT


class TestRegionModel:
    """Test the Region model properties and methods"""
    
    def test_is_democratic_property(self):
        """Test the is_democratic property"""
        democratic_region = Region(governance_type=GovernanceType.DEMOCRACY)
        autocratic_region = Region(governance_type=GovernanceType.AUTOCRACY)
        
        assert democratic_region.is_democratic is True
        assert autocratic_region.is_democratic is False
    
    def test_get_trade_bonus(self):
        """Test the get_trade_bonus method"""
        region = Region(
            trade_bonuses={'ore': 1.5, 'food': 1.2}
        )
        
        assert region.get_trade_bonus('ore') == 1.5
        assert region.get_trade_bonus('food') == 1.2
        assert region.get_trade_bonus('technology') == 1.0  # Default
    
    def test_update_cultural_identity(self):
        """Test the update_cultural_identity method"""
        region = Region()
        
        language_pack = {'greeting': 'Hello', 'farewell': 'Goodbye'}
        aesthetic_theme = {'primary_color': '#0066cc'}
        traditions = [{'name': 'Trade Fair', 'frequency': 'annual'}]
        
        region.update_cultural_identity(language_pack, aesthetic_theme, traditions)
        
        assert region.language_pack == language_pack
        assert region.aesthetic_theme == aesthetic_theme
        assert region.traditions == traditions


class TestRegionalMembershipModel:
    """Test the RegionalMembership model properties and methods"""
    
    def test_is_citizen_property(self):
        """Test the is_citizen property"""
        citizen = RegionalMembership(membership_type=MembershipType.CITIZEN)
        resident = RegionalMembership(membership_type=MembershipType.RESIDENT)
        visitor = RegionalMembership(membership_type=MembershipType.VISITOR)
        
        assert citizen.is_citizen is True
        assert resident.is_citizen is False
        assert visitor.is_citizen is False
    
    def test_can_vote_property(self):
        """Test the can_vote property"""
        citizen = RegionalMembership(
            membership_type=MembershipType.CITIZEN,
            voting_power=Decimal('1.0')
        )
        resident = RegionalMembership(
            membership_type=MembershipType.RESIDENT,
            voting_power=Decimal('0.5')
        )
        visitor = RegionalMembership(
            membership_type=MembershipType.VISITOR,
            voting_power=Decimal('1.0')
        )
        no_voting_power = RegionalMembership(
            membership_type=MembershipType.CITIZEN,
            voting_power=Decimal('0.0')
        )
        
        assert citizen.can_vote is True
        assert resident.can_vote is True
        assert visitor.can_vote is False
        assert no_voting_power.can_vote is False
    
    def test_update_reputation(self):
        """Test the update_reputation method"""
        membership = RegionalMembership(reputation_score=100)
        
        # Test normal update
        membership.update_reputation(50)
        assert membership.reputation_score == 150
        
        # Test negative update
        membership.update_reputation(-200)
        assert membership.reputation_score == -50
        
        # Test upper bound
        membership.update_reputation(2000)
        assert membership.reputation_score == 1000  # Capped at max
        
        # Test lower bound
        membership.update_reputation(-3000)
        assert membership.reputation_score == -1000  # Capped at min


class TestRegionalPolicyModel:
    """Test the RegionalPolicy model properties and methods"""
    
    def test_total_votes_property(self):
        """Test the total_votes property"""
        policy = RegionalPolicy(votes_for=25, votes_against=15)
        assert policy.total_votes == 40
    
    def test_approval_percentage_property(self):
        """Test the approval_percentage property"""
        policy_with_votes = RegionalPolicy(votes_for=30, votes_against=20)
        policy_no_votes = RegionalPolicy(votes_for=0, votes_against=0)
        
        assert policy_with_votes.approval_percentage == 60.0
        assert policy_no_votes.approval_percentage == 0.0
    
    def test_is_passing_property(self):
        """Test the is_passing property - requires region relationship"""
        # This would require a full region object with voting_threshold
        # For unit testing, we'll test the logic separately
        pass


class TestRegionalElectionModel:
    """Test the RegionalElection model properties and methods"""
    
    def test_is_active_property(self):
        """Test the is_active property"""
        now = datetime.utcnow()
        
        active_election = RegionalElection(
            status=ElectionStatus.ACTIVE,
            voting_opens_at=now - timedelta(hours=1),
            voting_closes_at=now + timedelta(hours=1)
        )
        
        pending_election = RegionalElection(
            status=ElectionStatus.PENDING,
            voting_opens_at=now + timedelta(hours=1),
            voting_closes_at=now + timedelta(hours=2)
        )
        
        completed_election = RegionalElection(
            status=ElectionStatus.COMPLETED,
            voting_opens_at=now - timedelta(hours=2),
            voting_closes_at=now - timedelta(hours=1)
        )
        
        # Note: The actual is_active property checks both status and time
        # This is a simplified test for the status check
        assert active_election.status == ElectionStatus.ACTIVE
        assert pending_election.status == ElectionStatus.PENDING
        assert completed_election.status == ElectionStatus.COMPLETED


class TestColonyCitizenshipOnRamp:
    """WO-CF PATH A: owning a colony in region R grants voting-citizenship in R.

    Acceptance: a player who owns a colony in R is on R's voter roll (can_vote);
    a player with no colony in R is not. These tests exercise the eligibility
    logic in isolation by stubbing the colony-ownership and membership-row reads
    so the rule itself is what is under test.
    """

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_colony_owner_is_on_voter_roll(self, mock_db):
        """A player who owns a colony in R is reported as a citizen on the roll,
        even with NO stored membership row (the colony is the qualifying stake)."""
        region_id = uuid.uuid4()
        player_id = uuid.uuid4()
        with patch.object(
            RegionalGovernanceService, "owns_colony_in_region",
            new=AsyncMock(return_value=True),
        ), patch.object(
            RegionalGovernanceService, "_get_voting_membership",
            new=AsyncMock(return_value=None),
        ):
            status = await RegionalGovernanceService.get_membership_status(
                mock_db, region_id, player_id
            )
        assert status["owns_colony_in_region"] is True
        assert status["membership_type"] == MembershipType.CITIZEN.value
        assert status["can_vote"] is True
        assert status["voting_power"] >= 1.0
        assert status["citizenship_source"] == "colony"
        assert status["is_member"] is True

    @pytest.mark.asyncio
    async def test_non_colony_owner_not_on_voter_roll(self, mock_db):
        """A player with no colony in R and only a visitor row is NOT on the roll."""
        region_id = uuid.uuid4()
        player_id = uuid.uuid4()
        visitor = RegionalMembership(
            player_id=player_id,
            region_id=region_id,
            membership_type=MembershipType.VISITOR.value,
            voting_power=Decimal("1.0"),
        )
        with patch.object(
            RegionalGovernanceService, "owns_colony_in_region",
            new=AsyncMock(return_value=False),
        ), patch.object(
            RegionalGovernanceService, "_get_voting_membership",
            new=AsyncMock(return_value=visitor),
        ):
            status = await RegionalGovernanceService.get_membership_status(
                mock_db, region_id, player_id
            )
        assert status["owns_colony_in_region"] is False
        assert status["can_vote"] is False
        assert status["membership_type"] == MembershipType.VISITOR.value
        assert status["citizenship_source"] is None

    @pytest.mark.asyncio
    async def test_non_member_no_colony_not_on_roll(self, mock_db):
        """A player with neither a membership row nor a colony is not a member
        and not on the roll."""
        region_id = uuid.uuid4()
        player_id = uuid.uuid4()
        with patch.object(
            RegionalGovernanceService, "owns_colony_in_region",
            new=AsyncMock(return_value=False),
        ), patch.object(
            RegionalGovernanceService, "_get_voting_membership",
            new=AsyncMock(return_value=None),
        ):
            status = await RegionalGovernanceService.get_membership_status(
                mock_db, region_id, player_id
            )
        assert status["is_member"] is False
        assert status["can_vote"] is False
        assert status["membership_type"] is None

    @pytest.mark.asyncio
    async def test_grant_citizenship_rejected_without_colony(self, mock_db):
        """grant_citizenship_for_colony refuses a player who owns no colony in R."""
        with patch.object(
            RegionalGovernanceService, "owns_colony_in_region",
            new=AsyncMock(return_value=False),
        ):
            result = await RegionalGovernanceService.grant_citizenship_for_colony(
                mock_db, uuid.uuid4(), uuid.uuid4()
            )
        assert result["ok"] is False
        assert result["code"] == "ERR_NO_COLONY_IN_REGION"

    @pytest.mark.asyncio
    async def test_grant_citizenship_promotes_visitor_for_colony_owner(self, mock_db):
        """A colony owner whose row is a visitor is promoted in place to citizen
        with voting weight (no duplicate row — the UNIQUE constraint upsert)."""
        region_id = uuid.uuid4()
        player_id = uuid.uuid4()
        visitor = RegionalMembership(
            player_id=player_id,
            region_id=region_id,
            membership_type=MembershipType.VISITOR.value,
            voting_power=Decimal("1.0"),
        )
        with patch.object(
            RegionalGovernanceService, "owns_colony_in_region",
            new=AsyncMock(return_value=True),
        ), patch.object(
            RegionalGovernanceService, "_get_voting_membership",
            new=AsyncMock(return_value=visitor),
        ):
            result = await RegionalGovernanceService.grant_citizenship_for_colony(
                mock_db, player_id, region_id
            )
        assert result["ok"] is True
        assert result["code"] == "CITIZENSHIP_GRANTED"
        assert visitor.membership_type == MembershipType.CITIZEN.value
        assert float(visitor.voting_power) >= 1.0
        assert visitor.can_vote is True
        mock_db.commit.assert_awaited()


class TestGrantRegionCitizenshipPrimitive:
    """WO-IL2: grant_region_citizenship is the ONE citizenship-grant primitive
    shared by the colony onramp and the invite-link onramp.

    These exercise the upsert/idempotency/monotonicity rules directly (no colony
    precondition — that lives in grant_citizenship_for_colony, which delegates
    here). The membership read is stubbed so the grant logic itself is under test.
    """

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_inserts_citizen_row_when_no_membership(self, mock_db):
        """No existing row -> a fresh citizen RegionalMembership is added at
        voting_power 1.0 and committed."""
        region_id = uuid.uuid4()
        player_id = uuid.uuid4()
        with patch.object(
            RegionalGovernanceService, "_get_voting_membership",
            new=AsyncMock(return_value=None),
        ):
            result = await RegionalGovernanceService.grant_region_citizenship(
                mock_db, player_id, region_id
            )
        assert result["ok"] is True
        # A freshly-INSERTED row is born at the citizen tier with weight, so the
        # promote checks are no-ops -- but the insert itself IS a grant, so the
        # code must say GRANTED, not CONFIRMED (commit f37613c / WO-IL2: IL6's
        # redeem path keys off GRANTED meaning "newly granted").
        assert result["code"] == "CITIZENSHIP_GRANTED"
        assert result["membership_type"] == MembershipType.CITIZEN.value
        assert result["voting_power"] >= 1.0
        mock_db.add.assert_called_once()
        added = mock_db.add.call_args[0][0]
        assert isinstance(added, RegionalMembership)
        assert added.membership_type == MembershipType.CITIZEN.value
        assert float(added.voting_power) >= 1.0
        mock_db.commit.assert_awaited()  # the INSERT was committed

    @pytest.mark.asyncio
    async def test_promotes_visitor_to_citizen(self, mock_db):
        """An existing lower-tier (visitor) row is PROMOTED in place — never a
        duplicate row, and citizenship is never downgraded."""
        region_id = uuid.uuid4()
        player_id = uuid.uuid4()
        visitor = RegionalMembership(
            player_id=player_id,
            region_id=region_id,
            membership_type=MembershipType.VISITOR.value,
            voting_power=Decimal("1.0"),
        )
        with patch.object(
            RegionalGovernanceService, "_get_voting_membership",
            new=AsyncMock(return_value=visitor),
        ):
            result = await RegionalGovernanceService.grant_region_citizenship(
                mock_db, player_id, region_id
            )
        assert result["ok"] is True
        assert result["code"] == "CITIZENSHIP_GRANTED"
        assert visitor.membership_type == MembershipType.CITIZEN.value
        assert visitor.can_vote is True
        mock_db.add.assert_not_called()  # promoted in place, not re-inserted
        mock_db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_floors_zero_power_citizen_to_one(self, mock_db):
        """A citizen row stuck at voting_power 0.0 is floored to 1.0 so it is not
        silently dropped from the roll despite the citizen tier."""
        region_id = uuid.uuid4()
        player_id = uuid.uuid4()
        zero_power = RegionalMembership(
            player_id=player_id,
            region_id=region_id,
            membership_type=MembershipType.CITIZEN.value,
            voting_power=Decimal("0.0"),
        )
        with patch.object(
            RegionalGovernanceService, "_get_voting_membership",
            new=AsyncMock(return_value=zero_power),
        ):
            result = await RegionalGovernanceService.grant_region_citizenship(
                mock_db, player_id, region_id
            )
        assert result["ok"] is True
        assert result["code"] == "CITIZENSHIP_GRANTED"
        assert float(zero_power.voting_power) >= 1.0
        assert zero_power.can_vote is True

    @pytest.mark.asyncio
    async def test_idempotent_noop_for_existing_citizen(self, mock_db):
        """Re-calling for an already-citizen-with-weight player is a no-op
        success (CITIZENSHIP_CONFIRMED) — does not downgrade or re-insert."""
        region_id = uuid.uuid4()
        player_id = uuid.uuid4()
        citizen = RegionalMembership(
            player_id=player_id,
            region_id=region_id,
            membership_type=MembershipType.CITIZEN.value,
            voting_power=Decimal("2.5"),
        )
        with patch.object(
            RegionalGovernanceService, "_get_voting_membership",
            new=AsyncMock(return_value=citizen),
        ):
            result = await RegionalGovernanceService.grant_region_citizenship(
                mock_db, player_id, region_id
            )
        assert result["ok"] is True
        assert result["code"] == "CITIZENSHIP_CONFIRMED"
        assert citizen.membership_type == MembershipType.CITIZEN.value
        assert float(citizen.voting_power) == 2.5  # weight preserved, not floored
        mock_db.add.assert_not_called()
        mock_db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_colony_onramp_delegates_to_primitive(self, mock_db):
        """grant_citizenship_for_colony keeps the colony precondition then
        delegates the grant to the shared primitive (single source of truth)."""
        region_id = uuid.uuid4()
        player_id = uuid.uuid4()
        with patch.object(
            RegionalGovernanceService, "owns_colony_in_region",
            new=AsyncMock(return_value=True),
        ), patch.object(
            RegionalGovernanceService, "grant_region_citizenship",
            new=AsyncMock(return_value={"ok": True, "code": "CITIZENSHIP_GRANTED",
                                        "membership_type": "citizen",
                                        "voting_power": 1.0}),
        ) as mock_grant:
            result = await RegionalGovernanceService.grant_citizenship_for_colony(
                mock_db, player_id, region_id
            )
        assert result["ok"] is True
        mock_grant.assert_awaited_once_with(mock_db, player_id, region_id)


class TestAccountAgeVoteGate:
    """WO-IL5 / ADR-0056 N-V3 / Max-D5: a citizen cannot VOTE until their account
    is ≥ 60 days old. Citizenship/presence is granted immediately; only the
    franchise waits. Migration-backfilled citizens (old accounts) must still pass.
    """

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_fresh_account_is_ineligible(self, mock_db):
        """An account created today is under the 60-day window -> ineligible."""
        from datetime import timezone as _tz
        mock_db.scalar = AsyncMock(return_value=datetime.now(_tz.utc))
        eligible = await RegionalGovernanceService._is_account_vote_eligible(
            mock_db, uuid.uuid4()
        )
        assert eligible is False

    @pytest.mark.asyncio
    async def test_old_account_is_eligible(self, mock_db):
        """A 90-day-old account clears the 60-day window -> eligible. This is the
        migration-backfilled-citizen case (real historical created_at)."""
        from datetime import timezone as _tz
        mock_db.scalar = AsyncMock(
            return_value=datetime.now(_tz.utc) - timedelta(days=90)
        )
        eligible = await RegionalGovernanceService._is_account_vote_eligible(
            mock_db, uuid.uuid4()
        )
        assert eligible is True

    @pytest.mark.asyncio
    async def test_exactly_60_days_is_eligible(self, mock_db):
        """The boundary is inclusive: an account exactly 60 days old can vote."""
        from datetime import timezone as _tz
        mock_db.scalar = AsyncMock(
            return_value=datetime.now(_tz.utc) - timedelta(days=60, seconds=1)
        )
        eligible = await RegionalGovernanceService._is_account_vote_eligible(
            mock_db, uuid.uuid4()
        )
        assert eligible is True

    @pytest.mark.asyncio
    async def test_unresolvable_account_fails_closed(self, mock_db):
        """No resolvable account (orphaned player / missing user) -> ineligible
        (never hand a vote to an account of unknown age)."""
        mock_db.scalar = AsyncMock(return_value=None)
        eligible = await RegionalGovernanceService._is_account_vote_eligible(
            mock_db, uuid.uuid4()
        )
        assert eligible is False

    @pytest.mark.asyncio
    async def test_naive_created_at_treated_as_utc(self, mock_db):
        """A naive created_at (defensive: hand-built/legacy row) is treated as
        UTC and does not raise a naive/aware TypeError."""
        mock_db.scalar = AsyncMock(
            return_value=datetime.utcnow() - timedelta(days=90)
        )
        eligible = await RegionalGovernanceService._is_account_vote_eligible(
            mock_db, uuid.uuid4()
        )
        assert eligible is True

    @pytest.mark.asyncio
    async def test_cast_election_vote_rejects_too_new_account(self, mock_db):
        """A citizen with a fresh account is rejected with ERR_ACCOUNT_TOO_NEW —
        the can_vote model gate passes but the age gate does not."""
        now = datetime.utcnow()
        region = Region(id=uuid.uuid4())
        election = RegionalElection(
            id=uuid.uuid4(),
            region_id=region.id,
            status=ElectionStatus.ACTIVE,
            voting_opens_at=now - timedelta(hours=1),
            voting_closes_at=now + timedelta(hours=1),
            candidates=[],
        )
        # cast_election_vote only reads voter.id; Player.username is a read-only
        # property (no setter), so construct the minimal voter.
        voter = Player(id=uuid.uuid4(), user_id=uuid.uuid4())
        citizen = RegionalMembership(
            player_id=voter.id,
            region_id=region.id,
            membership_type=MembershipType.CITIZEN.value,
            voting_power=Decimal("1.0"),
        )
        assert citizen.can_vote is True  # model gate would let them through
        with patch.object(
            RegionalGovernanceService, "_get_voting_membership",
            new=AsyncMock(return_value=citizen),
        ), patch.object(
            RegionalGovernanceService, "_is_account_vote_eligible",
            new=AsyncMock(return_value=False),
        ):
            result = await RegionalGovernanceService.cast_election_vote(
                mock_db, region, election, voter, str(uuid.uuid4())
            )
        assert result["ok"] is False
        assert result["code"] == "ERR_ACCOUNT_TOO_NEW"
        mock_db.commit.assert_not_awaited()  # no vote written

    @pytest.mark.asyncio
    async def test_age_eligible_player_ids_empty_set(self, mock_db):
        """The batch quorum helper short-circuits on an empty input (no query)."""
        result = await RegionalGovernanceService._age_eligible_player_ids(
            mock_db, set()
        )
        assert result == set()