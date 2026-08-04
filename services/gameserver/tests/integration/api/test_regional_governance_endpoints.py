"""
Integration tests for Regional Governance API endpoints
Tests the complete API workflow for multi-regional governance functionality
"""

import pytest
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.models.user import User
from src.models.player import Player
from src.models.region import (
    Region, RegionalMembership, RegionalPolicy, RegionalElection,
    GovernanceType, PolicyStatus, ElectionStatus, MembershipType
)
from src.core.config import settings


class TestRegionalGovernanceEndpoints:
    """Test regional governance API endpoints"""
    
    @pytest.fixture
    def test_user(self, db: Session):
        """Create a test user who owns a region"""
        user = User(
            username="region_owner",
            email="owner@test.com",
            is_admin=False
        )
        db.add(user)
        db.flush()
        return user
    
    @pytest.fixture
    def test_player(self, db: Session, test_user):
        """Create a test player for the user"""
        player = Player(
            user_id=test_user.id,
            username=test_user.username,
            credits=10000
        )
        db.add(player)
        db.flush()
        return player
    
    @pytest.fixture
    def test_region(self, db: Session, test_user):
        """Create a test region owned by the test user"""
        region = Region(
            name="test-region",
            display_name="Test Region",
            owner_id=test_user.id,
            governance_type=GovernanceType.DEMOCRACY,
            voting_threshold=Decimal('0.60'),
            tax_rate=Decimal('0.15'),
            starting_credits=2000,
            economic_specialization="trade",
            total_sectors=500,
            trade_bonuses={'ore': 1.5, 'food': 1.2}
        )
        db.add(region)
        db.flush()
        return region
    
    @pytest.fixture
    def region_owner_headers(self, client: TestClient, test_user):
        """Get auth headers for the region owner"""
        login_payload = {
            "username": test_user.username,
            "password": "testpassword123"  # Default test password
        }
        
        # First create user credentials
        
        # This would need proper user creation endpoint in real app
        # For testing, we'll use admin login for simplicity
        return self.get_auth_headers(client, settings.ADMIN_USERNAME, settings.ADMIN_PASSWORD)
    
    def get_auth_headers(self, client: TestClient, username: str, password: str) -> dict:
        """Helper to get authentication headers"""
        login_payload = {"username": username, "password": password}
        login_url = f"{settings.API_V1_STR}/auth/login/json"
        response = client.post(login_url, json=login_payload)
        if response.status_code == 200:
            tokens = response.json()
            return {"Authorization": f"Bearer {tokens['access_token']}"}
        return {}
    
    def test_get_my_region_success(
        self, 
        client: TestClient, 
        db: Session, 
        test_region: Region,
        admin_auth_headers: dict
    ):
        """Test successful region retrieval"""
        # Update the region to be owned by admin for testing
        test_region.owner_id = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first().id
        db.commit()
        
        url = f"{settings.API_V1_STR}/regions/my-region"
        response = client.get(url, headers=admin_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == test_region.name
        assert data["display_name"] == test_region.display_name
        assert data["governance_type"] == test_region.governance_type
        assert float(data["tax_rate"]) == float(test_region.tax_rate)
        assert data["total_sectors"] == test_region.total_sectors
    
    def test_get_my_region_not_found(
        self, 
        client: TestClient, 
        admin_auth_headers: dict
    ):
        """Test region retrieval when user owns no region"""
        url = f"{settings.API_V1_STR}/regions/my-region"
        response = client.get(url, headers=admin_auth_headers)
        
        assert response.status_code == 404
        assert "No region found" in response.json()["detail"]
    
    def test_get_regional_stats_success(
        self, 
        client: TestClient, 
        db: Session,
        test_region: Region,
        test_player: Player,
        admin_auth_headers: dict
    ):
        """Test successful regional statistics retrieval"""
        # Update region ownership for admin
        admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
        test_region.owner_id = admin_user.id
        
        # Add some membership data
        membership = RegionalMembership(
            player_id=test_player.id,
            region_id=test_region.id,
            membership_type=MembershipType.CITIZEN,
            reputation_score=85,
            voting_power=Decimal('1.0')
        )
        db.add(membership)
        db.commit()
        
        url = f"{settings.API_V1_STR}/regions/my-region/stats"
        response = client.get(url, headers=admin_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert "total_population" in data
        assert "citizen_count" in data
        assert "average_reputation" in data
        assert "active_elections" in data
        assert "pending_policies" in data
        assert "treaties_count" in data
    
    def test_update_economic_config_success(
        self, 
        client: TestClient, 
        db: Session,
        test_region: Region,
        admin_auth_headers: dict
    ):
        """Test successful economic configuration update"""
        # Update region ownership for admin
        admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
        test_region.owner_id = admin_user.id
        db.commit()
        
        update_data = {
            "tax_rate": 0.20,
            "starting_credits": 3000,
            "trade_bonuses": {"ore": 2.0, "food": 1.5, "technology": 1.8},
            "economic_specialization": "mining"
        }
        
        url = f"{settings.API_V1_STR}/regions/my-region/economy"
        response = client.put(url, json=update_data, headers=admin_auth_headers)
        
        assert response.status_code == 200
        assert "successfully" in response.json()["message"]
        
        # Verify the update
        db.refresh(test_region)
        assert float(test_region.tax_rate) == 0.20
        assert test_region.starting_credits == 3000
        assert test_region.economic_specialization == "mining"
        assert test_region.trade_bonuses["ore"] == 2.0
    
    def test_update_economic_config_invalid_tax_rate(
        self, 
        client: TestClient, 
        db: Session,
        test_region: Region,
        admin_auth_headers: dict
    ):
        """Test economic configuration update with invalid tax rate"""
        admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
        test_region.owner_id = admin_user.id
        db.commit()
        
        update_data = {
            "tax_rate": 0.50,  # Too high
            "starting_credits": 3000
        }
        
        url = f"{settings.API_V1_STR}/regions/my-region/economy"
        response = client.put(url, json=update_data, headers=admin_auth_headers)
        
        assert response.status_code == 422  # Validation error
    
    def test_update_governance_config_success(
        self, 
        client: TestClient, 
        db: Session,
        test_region: Region,
        admin_auth_headers: dict
    ):
        """Test successful governance configuration update"""
        admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
        test_region.owner_id = admin_user.id
        db.commit()
        
        update_data = {
            "governance_type": "council",
            "voting_threshold": 0.75,
            "election_frequency_days": 120,
            "constitutional_text": "We the players of this region..."
        }
        
        url = f"{settings.API_V1_STR}/regions/my-region/governance"
        response = client.put(url, json=update_data, headers=admin_auth_headers)
        
        assert response.status_code == 200
        assert "successfully" in response.json()["message"]
        
        # Verify the update
        db.refresh(test_region)
        assert test_region.governance_type == "council"
        assert float(test_region.voting_threshold) == 0.75
        assert test_region.election_frequency_days == 120
        assert "We the players" in test_region.constitutional_text
    
    def test_update_governance_config_invalid_type(
        self, 
        client: TestClient, 
        db: Session,
        test_region: Region,
        admin_auth_headers: dict
    ):
        """Test governance configuration update with invalid governance type"""
        admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
        test_region.owner_id = admin_user.id
        db.commit()
        
        update_data = {
            "governance_type": "invalid_type",
            "voting_threshold": 0.60
        }
        
        url = f"{settings.API_V1_STR}/regions/my-region/governance"
        response = client.put(url, json=update_data, headers=admin_auth_headers)
        
        assert response.status_code == 400
        assert "Invalid governance type" in response.json()["detail"]
    
    def test_create_policy_success(
        self, 
        client: TestClient, 
        db: Session,
        test_region: Region,
        test_player: Player,
        admin_auth_headers: dict
    ):
        """Test successful policy creation"""
        admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
        test_region.owner_id = admin_user.id
        
        # Create admin player record
        admin_player = Player(
            user_id=admin_user.id,
            username=admin_user.username,
            credits=10000
        )
        db.add(admin_player)
        db.commit()
        
        policy_data = {
            "policy_type": "tax_rate",
            "title": "Increase Regional Tax Rate",
            "description": "Proposal to increase tax rate to fund infrastructure",
            "proposed_changes": {"tax_rate": 0.25},
            "voting_duration_days": 7
        }
        
        url = f"{settings.API_V1_STR}/regions/my-region/policies"
        response = client.post(url, json=policy_data, headers=admin_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert "successfully" in data["message"]
        assert "policy_id" in data
        
        # Verify policy was created
        policy = db.query(RegionalPolicy).filter(
            RegionalPolicy.region_id == test_region.id
        ).first()
        assert policy is not None
        assert policy.title == policy_data["title"]
        assert policy.policy_type == policy_data["policy_type"]
        assert policy.status == PolicyStatus.VOTING
    
    def test_get_regional_policies(
        self, 
        client: TestClient, 
        db: Session,
        test_region: Region,
        test_player: Player,
        admin_auth_headers: dict
    ):
        """Test retrieval of regional policies"""
        admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
        test_region.owner_id = admin_user.id
        
        # Create admin player
        admin_player = Player(
            user_id=admin_user.id,
            username=admin_user.username,
            credits=10000
        )
        db.add(admin_player)
        
        # Create test policies
        policy1 = RegionalPolicy(
            region_id=test_region.id,
            policy_type="tax_rate",
            title="Tax Policy",
            proposed_by=admin_player.id,
            voting_closes_at=datetime.utcnow() + timedelta(days=7),
            votes_for=15,
            votes_against=5
        )
        policy2 = RegionalPolicy(
            region_id=test_region.id,
            policy_type="pvp_rules",
            title="PvP Policy",
            proposed_by=admin_player.id,
            voting_closes_at=datetime.utcnow() + timedelta(days=5),
            votes_for=8,
            votes_against=12
        )
        
        db.add_all([policy1, policy2])
        db.commit()
        
        url = f"{settings.API_V1_STR}/regions/my-region/policies"
        response = client.get(url, headers=admin_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        
        # Check policy data
        tax_policy = next(p for p in data if p["policy_type"] == "tax_rate")
        assert tax_policy["title"] == "Tax Policy"
        assert tax_policy["votes_for"] == 15
        assert tax_policy["votes_against"] == 5
        assert tax_policy["approval_percentage"] == 75.0
    
    def test_start_election_success(
        self, 
        client: TestClient, 
        db: Session,
        test_region: Region,
        admin_auth_headers: dict
    ):
        """Test successful election start"""
        admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
        test_region.owner_id = admin_user.id
        db.commit()
        
        election_data = {
            "position": "governor",
            "voting_duration_days": 7
        }
        
        url = f"{settings.API_V1_STR}/regions/my-region/elections"
        response = client.post(url, json=election_data, headers=admin_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert "successfully" in data["message"]
        assert "election_id" in data
        
        # Verify election was created
        election = db.query(RegionalElection).filter(
            RegionalElection.region_id == test_region.id
        ).first()
        assert election is not None
        assert election.position == "governor"
        assert election.status == ElectionStatus.ACTIVE
    
    def test_start_election_conflict(
        self, 
        client: TestClient, 
        db: Session,
        test_region: Region,
        admin_auth_headers: dict
    ):
        """Test election start with existing active election"""
        admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
        test_region.owner_id = admin_user.id
        
        # Create existing active election
        existing_election = RegionalElection(
            region_id=test_region.id,
            position="governor",
            candidates=[],
            voting_opens_at=datetime.utcnow(),
            voting_closes_at=datetime.utcnow() + timedelta(days=7),
            status=ElectionStatus.ACTIVE
        )
        db.add(existing_election)
        db.commit()
        
        election_data = {
            "position": "governor",
            "voting_duration_days": 7
        }
        
        url = f"{settings.API_V1_STR}/regions/my-region/elections"
        response = client.post(url, json=election_data, headers=admin_auth_headers)
        
        assert response.status_code == 409
        assert "already exists" in response.json()["detail"]
    
    def test_get_regional_elections(
        self, 
        client: TestClient, 
        db: Session,
        test_region: Region,
        admin_auth_headers: dict
    ):
        """Test retrieval of regional elections"""
        admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
        test_region.owner_id = admin_user.id
        
        # Create test elections
        election1 = RegionalElection(
            region_id=test_region.id,
            position="governor",
            candidates=[{"player_id": str(uuid.uuid4()), "player_name": "Candidate 1"}],
            voting_opens_at=datetime.utcnow(),
            voting_closes_at=datetime.utcnow() + timedelta(days=7),
            status=ElectionStatus.ACTIVE
        )
        election2 = RegionalElection(
            region_id=test_region.id,
            position="council_member",
            candidates=[{"player_id": str(uuid.uuid4()), "player_name": "Candidate 2"}],
            voting_opens_at=datetime.utcnow() - timedelta(days=7),
            voting_closes_at=datetime.utcnow() - timedelta(days=1),
            status=ElectionStatus.COMPLETED
        )
        
        db.add_all([election1, election2])
        db.commit()
        
        url = f"{settings.API_V1_STR}/regions/my-region/elections"
        response = client.get(url, headers=admin_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        
        # Check election data
        gov_election = next(e for e in data if e["position"] == "governor")
        assert gov_election["status"] == ElectionStatus.ACTIVE
        assert len(gov_election["candidates"]) == 1
    
    def test_get_regional_treaties(
        self, 
        client: TestClient, 
        db: Session,
        test_region: Region,
        admin_auth_headers: dict
    ):
        """Test retrieval of regional treaties"""
        admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
        test_region.owner_id = admin_user.id
        db.commit()
        
        url = f"{settings.API_V1_STR}/regions/my-region/treaties"
        response = client.get(url, headers=admin_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        # No treaties expected in this test, should return empty list
        assert len(data) == 0
    
    def test_update_cultural_identity_success(
        self, 
        client: TestClient, 
        db: Session,
        test_region: Region,
        admin_auth_headers: dict
    ):
        """Test successful cultural identity update"""
        admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
        test_region.owner_id = admin_user.id
        db.commit()
        
        culture_data = {
            "language_pack": {"greeting": "Welcome", "farewell": "Safe travels"},
            "aesthetic_theme": {"primary_color": "#0066cc", "font": "Arial"},
            "traditions": {"festival": "Annual Trade Fair", "ceremony": "Founding Day"}
        }
        
        url = f"{settings.API_V1_STR}/regions/my-region/culture"
        response = client.put(url, json=culture_data, headers=admin_auth_headers)
        
        assert response.status_code == 200
        assert "successfully" in response.json()["message"]
        
        # Verify the update
        db.refresh(test_region)
        assert test_region.language_pack["greeting"] == "Welcome"
        assert test_region.aesthetic_theme["primary_color"] == "#0066cc"
        assert test_region.traditions["festival"] == "Annual Trade Fair"
    
    def test_unauthorized_access(self, client: TestClient):
        """Test that endpoints require authentication"""
        url = f"{settings.API_V1_STR}/regions/my-region"
        response = client.get(url)
        
        assert response.status_code == 401


class TestRegionalGovernanceEndpointsEdgeCases:
    """Test edge cases and error conditions for regional governance endpoints"""
    
    def test_invalid_voting_threshold_boundaries(
        self, 
        client: TestClient, 
        db: Session,
        admin_auth_headers: dict
    ):
        """Test voting threshold validation at boundaries"""
        # Create test region for admin
        admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
        region = Region(
            name="test-region",
            display_name="Test Region",
            owner_id=admin_user.id
        )
        db.add(region)
        db.commit()
        
        # Test too low
        update_data = {"governance_type": "democracy", "voting_threshold": 0.05}
        url = f"{settings.API_V1_STR}/regions/my-region/governance"
        response = client.put(url, json=update_data, headers=admin_auth_headers)
        assert response.status_code == 422
        
        # Test too high
        update_data = {"governance_type": "democracy", "voting_threshold": 0.95}
        response = client.put(url, json=update_data, headers=admin_auth_headers)
        assert response.status_code == 422
        
        # Test valid boundaries
        update_data = {"governance_type": "democracy", "voting_threshold": 0.1}
        response = client.put(url, json=update_data, headers=admin_auth_headers)
        assert response.status_code == 200
        
        update_data = {"governance_type": "democracy", "voting_threshold": 0.9}
        response = client.put(url, json=update_data, headers=admin_auth_headers)
        assert response.status_code == 200
    
    def test_invalid_trade_bonus_values(
        self, 
        client: TestClient, 
        db: Session,
        admin_auth_headers: dict
    ):
        """Test trade bonus validation"""
        admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
        region = Region(
            name="test-region",
            display_name="Test Region",
            owner_id=admin_user.id
        )
        db.add(region)
        db.commit()
        
        # Test invalid trade bonus (too low)
        update_data = {
            "tax_rate": 0.15,
            "trade_bonuses": {"ore": 0.5}  # Below minimum of 1.0
        }
        url = f"{settings.API_V1_STR}/regions/my-region/economy"
        response = client.put(url, json=update_data, headers=admin_auth_headers)
        assert response.status_code == 400
        assert "must be between 1.0 and 3.0" in response.json()["detail"]
        
        # Test invalid trade bonus (too high)
        update_data = {
            "tax_rate": 0.15,
            "trade_bonuses": {"food": 5.0}  # Above maximum of 3.0
        }
        response = client.put(url, json=update_data, headers=admin_auth_headers)
        assert response.status_code == 400
        assert "must be between 1.0 and 3.0" in response.json()["detail"]
    
    def test_policy_creation_missing_player(
        self, 
        client: TestClient, 
        db: Session,
        admin_auth_headers: dict
    ):
        """Test policy creation when player record doesn't exist"""
        admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
        region = Region(
            name="test-region",
            display_name="Test Region",
            owner_id=admin_user.id
        )
        db.add(region)
        db.commit()
        
        policy_data = {
            "policy_type": "tax_rate",
            "title": "Test Policy",
            "description": "Test description"
        }
        
        url = f"{settings.API_V1_STR}/regions/my-region/policies"
        response = client.post(url, json=policy_data, headers=admin_auth_headers)
        
        assert response.status_code == 404
        assert "Player record not found" in response.json()["detail"]