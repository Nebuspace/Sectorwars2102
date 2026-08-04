"""
Comprehensive test suite for the Multi-Regional System
Tests the complete integration of all multi-regional functionality
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
from src.models.sector import Sector
from src.core.config import settings


class TestMultiRegionalSystemIntegration:
    """Integration tests for the complete multi-regional system"""
    
    @pytest.fixture
    def complete_multi_regional_setup(self, db: Session):
        """Create a complete multi-regional setup for testing"""
        # Create Central Nexus
        nexus_region = Region(
            name="central-nexus",
            display_name="Central Nexus",
            governance_type="galactic_council",
            economic_specialization="universal_hub",
            total_sectors=5000
        )
        db.add(nexus_region)
        
        # Create test region owners
        owner1 = User(username="region_owner_1", email="owner1@test.com", is_admin=False)
        owner2 = User(username="region_owner_2", email="owner2@test.com", is_admin=False)
        db.add_all([owner1, owner2])
        db.flush()
        
        # Create players for owners
        player1 = Player(user_id=owner1.id, username=owner1.username, credits=50000)
        player2 = Player(user_id=owner2.id, username=owner2.username, credits=75000)
        db.add_all([player1, player2])
        
        # Create two regional territories
        region1 = Region(
            name="alpha-sector",
            display_name="Alpha Sector Trading Hub",
            owner_id=owner1.id,
            governance_type=GovernanceType.DEMOCRACY,
            voting_threshold=Decimal('0.60'),
            tax_rate=Decimal('0.15'),
            starting_credits=2500,
            economic_specialization="trade",
            total_sectors=500,
            trade_bonuses={'ore': 1.5, 'food': 1.3}
        )
        
        region2 = Region(
            name="beta-industrial",
            display_name="Beta Industrial Complex",
            owner_id=owner2.id,
            governance_type=GovernanceType.AUTOCRACY,
            voting_threshold=Decimal('0.51'),
            tax_rate=Decimal('0.20'),
            starting_credits=2000,
            economic_specialization="manufacturing",
            total_sectors=500,
            trade_bonuses={'technology': 2.0, 'luxury': 1.2}
        )
        
        db.add_all([region1, region2])
        db.flush()
        
        # Create regional memberships
        membership1 = RegionalMembership(
            player_id=player1.id,
            region_id=region1.id,
            membership_type=MembershipType.CITIZEN,
            reputation_score=100,
            voting_power=Decimal('1.0')
        )
        
        membership2 = RegionalMembership(
            player_id=player2.id,
            region_id=region2.id,
            membership_type=MembershipType.CITIZEN,
            reputation_score=85,
            voting_power=Decimal('1.0')
        )
        
        # Cross-regional membership (player1 visits region2)
        cross_membership = RegionalMembership(
            player_id=player1.id,
            region_id=region2.id,
            membership_type=MembershipType.VISITOR,
            reputation_score=20,
            voting_power=Decimal('0.0')
        )
        
        db.add_all([membership1, membership2, cross_membership])
        
        # Create some sectors for each region
        sectors = []
        for region, sector_start in [(region1, 1), (region2, 501), (nexus_region, 10001)]:
            for i in range(10):  # 10 sectors per region for testing
                sector = Sector(
                    sector_id=sector_start + i,
                    sector_number=sector_start + i,
                    name=f"{region.display_name} Sector {i + 1}",
                    region_id=region.id,
                    cluster_id=uuid.uuid4(),
                    district='commerce_central' if region == nexus_region else None,
                    security_level=8 if region == nexus_region else 5,
                    development_level=9 if region == nexus_region else 6,
                    traffic_level=7,
                    x_coord=i,
                    y_coord=1,
                    z_coord=0
                )
                sectors.append(sector)
                db.add(sector)
        
        # Create policies for democratic region
        policy = RegionalPolicy(
            region_id=region1.id,
            policy_type="tax_rate",
            title="Reduce Regional Tax Rate",
            description="Proposal to reduce tax rate to stimulate economic growth",
            proposed_changes={"tax_rate": 0.12},
            proposed_by=player1.id,
            voting_closes_at=datetime.utcnow() + timedelta(days=5),
            votes_for=15,
            votes_against=8,
            status=PolicyStatus.VOTING
        )
        db.add(policy)
        
        # Create election for democratic region
        election = RegionalElection(
            region_id=region1.id,
            position="council_member",
            candidates=[
                {"player_id": str(player1.id), "player_name": player1.username, "platform": "Economic Growth"}
            ],
            voting_opens_at=datetime.utcnow() - timedelta(hours=1),
            voting_closes_at=datetime.utcnow() + timedelta(days=3),
            status=ElectionStatus.ACTIVE
        )
        db.add(election)
        
        db.commit()
        
        return {
            "nexus_region": nexus_region,
            "region1": region1,
            "region2": region2,
            "owner1": owner1,
            "owner2": owner2,
            "player1": player1,
            "player2": player2,
            "sectors": sectors,
            "policy": policy,
            "election": election,
            "memberships": [membership1, membership2, cross_membership]
        }
    
    def test_complete_governance_workflow(
        self, 
        client: TestClient,
        db: Session,
        complete_multi_regional_setup: dict,
        admin_auth_headers: dict
    ):
        """Test complete governance workflow from policy creation to implementation"""
        setup = complete_multi_regional_setup
        region1 = setup["region1"]
        
        # Update region to be owned by admin for testing
        admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
        region1.owner_id = admin_user.id
        
        # Create admin player
        admin_player = Player(
            user_id=admin_user.id,
            username=admin_user.username,
            credits=100000
        )
        db.add(admin_player)
        db.commit()
        
        # 1. Create a new policy proposal
        policy_data = {
            "policy_type": "economic_specialization",
            "title": "Switch to Mining Specialization",
            "description": "Proposal to change regional specialization to mining for better ore trade bonuses",
            "proposed_changes": {"economic_specialization": "mining", "trade_bonuses": {"ore": 2.5}},
            "voting_duration_days": 7
        }
        
        url = f"{settings.API_V1_STR}/regions/my-region/policies"
        response = client.post(url, json=policy_data, headers=admin_auth_headers)
        assert response.status_code == 200
        policy_id = response.json()["policy_id"]
        
        # 2. Verify policy appears in policy list
        response = client.get(url, headers=admin_auth_headers)
        assert response.status_code == 200
        policies = response.json()
        new_policy = next(p for p in policies if p["id"] == policy_id)
        assert new_policy["title"] == policy_data["title"]
        assert new_policy["status"] == "voting"
        
        # 3. Check regional statistics include the new policy
        url = f"{settings.API_V1_STR}/regions/my-region/stats"
        response = client.get(url, headers=admin_auth_headers)
        assert response.status_code == 200
        stats = response.json()
        assert stats["pending_policies"] >= 1
    
    def test_multi_regional_economics_integration(
        self, 
        client: TestClient,
        db: Session,
        complete_multi_regional_setup: dict,
        admin_auth_headers: dict
    ):
        """Test economic configuration and trade bonus integration"""
        setup = complete_multi_regional_setup
        region1 = setup["region1"]
        
        # Update region ownership for testing
        admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
        region1.owner_id = admin_user.id
        db.commit()
        
        # 1. Update economic configuration
        economic_update = {
            "tax_rate": 0.18,
            "starting_credits": 3500,
            "trade_bonuses": {
                "ore": 2.2,
                "food": 1.8,
                "technology": 1.4,
                "luxury": 1.1,
                "energy": 1.6
            },
            "economic_specialization": "diversified_trade"
        }
        
        url = f"{settings.API_V1_STR}/regions/my-region/economy"
        response = client.put(url, json=economic_update, headers=admin_auth_headers)
        assert response.status_code == 200
        
        # 2. Verify changes are reflected in region info
        url = f"{settings.API_V1_STR}/regions/my-region"
        response = client.get(url, headers=admin_auth_headers)
        assert response.status_code == 200
        region_info = response.json()
        
        assert float(region_info["tax_rate"]) == 0.18
        assert region_info["starting_credits"] == 3500
        assert region_info["economic_specialization"] == "diversified_trade"
        assert region_info["trade_bonuses"]["ore"] == 2.2
        assert region_info["trade_bonuses"]["energy"] == 1.6
        
        # 3. Verify statistics reflect economic changes
        url = f"{settings.API_V1_STR}/regions/my-region/stats"
        response = client.get(url, headers=admin_auth_headers)
        assert response.status_code == 200
        stats = response.json()
        
        # Total revenue should reflect new tax rate
        expected_revenue = float(region1.total_trade_volume) * 0.18
        assert abs(stats["total_revenue"] - expected_revenue) < 0.01
    
    def test_central_nexus_regional_integration(
        self, 
        client: TestClient,
        complete_multi_regional_setup: dict,
        admin_auth_headers: dict
    ):
        """Test integration between Central Nexus and regional territories"""
        setup = complete_multi_regional_setup
        
        # 1. Verify Central Nexus exists and has correct status
        url = f"{settings.API_V1_STR}/nexus/status"
        response = client.get(url, headers=admin_auth_headers)
        assert response.status_code == 200
        nexus_status = response.json()
        
        assert nexus_status["exists"] is True
        assert nexus_status["governance_type"] == "galactic_council"
        assert nexus_status["economic_specialization"] == "universal_hub"
        assert nexus_status["total_sectors"] == 5000
        
        # 2. Check Central Nexus districts
        url = f"{settings.API_V1_STR}/nexus/districts"
        response = client.get(url, headers=admin_auth_headers)
        assert response.status_code == 200
        districts = response.json()
        
        # Should have districts with sectors
        assert len(districts) >= 1
        commerce_district = next((d for d in districts if d["district_type"] == "commerce_central"), None)
        assert commerce_district is not None
        assert commerce_district["sectors_count"] >= 10  # Our test sectors
        
        # 3. Verify regional territories are separate from Central Nexus
        region1 = setup["region1"]
        region2 = setup["region2"]
        
        # Update one region for admin testing
        admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
        region1.owner_id = admin_user.id
        db.commit()
        
        url = f"{settings.API_V1_STR}/regions/my-region"
        response = client.get(url, headers=admin_auth_headers)
        assert response.status_code == 200
        region_info = response.json()
        
        assert region_info["name"] != "central-nexus"
        assert region_info["total_sectors"] == 500  # Regional size
        assert region_info["governance_type"] in ["democracy", "autocracy"]  # Not galactic_council
    
    def test_cross_regional_membership_system(
        self, 
        client: TestClient,
        complete_multi_regional_setup: dict,
        admin_auth_headers: dict
    ):
        """Test cross-regional membership and reputation system"""
        setup = complete_multi_regional_setup
        region1 = setup["region1"]
        
        # Update region ownership for testing
        admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
        region1.owner_id = admin_user.id
        db.commit()
        
        # Get regional members
        url = f"{settings.API_V1_STR}/regions/my-region/members"
        response = client.get(url, headers=admin_auth_headers)
        assert response.status_code == 200
        members = response.json()
        
        # Should have at least one member (the region owner as citizen)
        assert len(members) >= 1
        
        # Find citizen and visitor memberships
        citizens = [m for m in members if m["membership_type"] == "citizen"]
        visitors = [m for m in members if m["membership_type"] == "visitor"]
        
        assert len(citizens) >= 1
        
        # Verify citizen has voting rights
        citizen = citizens[0]
        assert citizen["voting_power"] > 0
        assert citizen["reputation_score"] >= 0
        
        # If there are visitors, they should not have voting power
        for visitor in visitors:
            assert visitor["voting_power"] == 0
    
    def test_governance_system_integration(
        self, 
        client: TestClient,
        complete_multi_regional_setup: dict,
        admin_auth_headers: dict
    ):
        """Test complete governance system integration"""
        setup = complete_multi_regional_setup
        region1 = setup["region1"]
        
        # Update region ownership for testing
        admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
        region1.owner_id = admin_user.id
        
        # Create admin player
        admin_player = Player(
            user_id=admin_user.id,
            username=admin_user.username,
            credits=100000
        )
        db.add(admin_player)
        db.commit()
        
        # 1. Update governance configuration
        governance_update = {
            "governance_type": "council",
            "voting_threshold": 0.70,
            "election_frequency_days": 60,
            "constitutional_text": "The Alpha Sector Trading Hub operates under council governance with elected representatives serving 60-day terms."
        }
        
        url = f"{settings.API_V1_STR}/regions/my-region/governance"
        response = client.put(url, json=governance_update, headers=admin_auth_headers)
        assert response.status_code == 200
        
        # 2. Start a new election
        election_data = {
            "position": "trade_commissioner",
            "voting_duration_days": 5
        }
        
        url = f"{settings.API_V1_STR}/regions/my-region/elections"
        response = client.post(url, json=election_data, headers=admin_auth_headers)
        assert response.status_code == 200
        election_id = response.json()["election_id"]
        
        # 3. Verify election appears in elections list
        response = client.get(url, headers=admin_auth_headers)
        assert response.status_code == 200
        elections = response.json()
        
        new_election = next(e for e in elections if e["id"] == election_id)
        assert new_election["position"] == "trade_commissioner"
        assert new_election["status"] == "active"
        
        # 4. Verify governance statistics
        url = f"{settings.API_V1_STR}/regions/my-region/stats"
        response = client.get(url, headers=admin_auth_headers)
        assert response.status_code == 200
        stats = response.json()
        
        assert stats["active_elections"] >= 1  # Our new election plus any existing
    
    def test_cultural_identity_system(
        self, 
        client: TestClient,
        complete_multi_regional_setup: dict,
        admin_auth_headers: dict
    ):
        """Test cultural identity customization system"""
        setup = complete_multi_regional_setup
        region1 = setup["region1"]
        
        # Update region ownership for testing
        admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
        region1.owner_id = admin_user.id
        db.commit()
        
        # Update cultural identity
        culture_data = {
            "language_pack": {
                "greeting": "Welcome to Alpha Sector, trader!",
                "farewell": "May profitable winds fill your cargo holds!",
                "currency_symbol": "ATC",  # Alpha Trade Credits
                "motto": "Prosperity Through Commerce"
            },
            "aesthetic_theme": {
                "primary_color": "#FFD700",  # Gold
                "secondary_color": "#1E3A8A",  # Deep Blue
                "font_family": "Trade Gothic",
                "logo_style": "corporate_modern"
            },
            "traditions": {
                "founding_day": "Annual Trade Festival celebrating sector founding",
                "merchant_honors": "Monthly awards for outstanding traders",
                "diplomatic_customs": "Formal trade negotiations with ceremonial contract signing"
            }
        }
        
        url = f"{settings.API_V1_STR}/regions/my-region/culture"
        response = client.put(url, json=culture_data, headers=admin_auth_headers)
        assert response.status_code == 200
        
        # Verify cultural changes are reflected
        url = f"{settings.API_V1_STR}/regions/my-region"
        response = client.get(url, headers=admin_auth_headers)
        assert response.status_code == 200
        region_info = response.json()
        
        assert region_info["language_pack"]["greeting"] == "Welcome to Alpha Sector, trader!"
        assert region_info["language_pack"]["currency_symbol"] == "ATC"
        assert region_info["aesthetic_theme"]["primary_color"] == "#FFD700"
        assert region_info["traditions"]["founding_day"] == "Annual Trade Festival celebrating sector founding"
    
    def test_end_to_end_multi_regional_workflow(
        self, 
        client: TestClient,
        complete_multi_regional_setup: dict,
        admin_auth_headers: dict
    ):
        """Test complete end-to-end workflow covering all major systems"""
        setup = complete_multi_regional_setup
        region1 = setup["region1"]
        
        # Update region ownership for testing
        admin_user = db.query(User).filter(User.username == settings.ADMIN_USERNAME).first()
        region1.owner_id = admin_user.id
        
        # Create admin player
        admin_player = Player(
            user_id=admin_user.id,
            username=admin_user.username,
            credits=100000
        )
        db.add(admin_player)
        db.commit()
        
        # 1. Check initial regional status
        url = f"{settings.API_V1_STR}/regions/my-region"
        response = client.get(url, headers=admin_auth_headers)
        assert response.status_code == 200
        initial_region = response.json()
        
        # 2. Update economics
        economic_update = {
            "tax_rate": 0.22,
            "starting_credits": 4000,
            "economic_specialization": "luxury_goods",
            "trade_bonuses": {"luxury": 2.5, "technology": 1.8}
        }
        
        url = f"{settings.API_V1_STR}/regions/my-region/economy"
        response = client.put(url, json=economic_update, headers=admin_auth_headers)
        assert response.status_code == 200
        
        # 3. Update governance
        governance_update = {
            "governance_type": "democracy",
            "voting_threshold": 0.65,
            "constitutional_text": "Democratic governance with citizen participation"
        }
        
        url = f"{settings.API_V1_STR}/regions/my-region/governance"
        response = client.put(url, json=governance_update, headers=admin_auth_headers)
        assert response.status_code == 200
        
        # 4. Create policy proposal
        policy_data = {
            "policy_type": "trade_policy",
            "title": "Luxury Goods Incentive Program",
            "description": "Enhanced bonuses for luxury goods trading",
            "proposed_changes": {"trade_bonuses": {"luxury": 3.0}}
        }
        
        url = f"{settings.API_V1_STR}/regions/my-region/policies"
        response = client.post(url, json=policy_data, headers=admin_auth_headers)
        assert response.status_code == 200
        
        # 5. Start election
        election_data = {"position": "economic_minister", "voting_duration_days": 7}
        
        url = f"{settings.API_V1_STR}/regions/my-region/elections"
        response = client.post(url, json=election_data, headers=admin_auth_headers)
        assert response.status_code == 200
        
        # 6. Update cultural identity
        culture_data = {
            "language_pack": {"motto": "Excellence in Luxury"},
            "aesthetic_theme": {"primary_color": "#800080"},  # Purple for luxury
            "traditions": {"luxury_expo": "Annual luxury goods exhibition"}
        }
        
        url = f"{settings.API_V1_STR}/regions/my-region/culture"
        response = client.put(url, json=culture_data, headers=admin_auth_headers)
        assert response.status_code == 200
        
        # 7. Verify all changes are integrated
        url = f"{settings.API_V1_STR}/regions/my-region"
        response = client.get(url, headers=admin_auth_headers)
        assert response.status_code == 200
        final_region = response.json()
        
        # Economics
        assert float(final_region["tax_rate"]) == 0.22
        assert final_region["starting_credits"] == 4000
        assert final_region["economic_specialization"] == "luxury_goods"
        assert final_region["trade_bonuses"]["luxury"] == 2.5
        
        # Governance
        assert final_region["governance_type"] == "democracy"
        assert float(final_region["voting_threshold"]) == 0.65
        
        # Culture
        assert final_region["language_pack"]["motto"] == "Excellence in Luxury"
        assert final_region["aesthetic_theme"]["primary_color"] == "#800080"
        
        # 8. Verify statistics reflect all changes
        url = f"{settings.API_V1_STR}/regions/my-region/stats"
        response = client.get(url, headers=admin_auth_headers)
        assert response.status_code == 200
        stats = response.json()
        
        assert stats["pending_policies"] >= 1
        assert stats["active_elections"] >= 1
        
        # 9. Verify Central Nexus remains independent
        url = f"{settings.API_V1_STR}/nexus/status"
        response = client.get(url, headers=admin_auth_headers)
        assert response.status_code == 200
        nexus_status = response.json()
        
        assert nexus_status["governance_type"] == "galactic_council"  # Unchanged
        assert nexus_status["economic_specialization"] == "universal_hub"  # Unchanged