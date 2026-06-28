"""
Integration tests for Central Nexus API endpoints
Tests the complete API workflow for Central Nexus management
"""

import pytest
import uuid
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.models.region import Region
from src.models.sector import Sector
from src.models.planet import Planet
from src.models.station import Station, StationClass, StationType
from src.core.config import settings


class TestNexusEndpoints:
    """Test Central Nexus API endpoints"""
    
    @pytest.fixture
    def nexus_region(self, db: Session):
        """Create a Central Nexus region for testing"""
        region = Region(
            name="central-nexus",
            display_name="Central Nexus",
            governance_type="galactic_council",
            economic_specialization="universal_hub",
            total_sectors=5000
        )
        db.add(region)
        db.flush()
        return region
    
    @pytest.fixture
    def sample_nexus_sectors(self, db: Session, nexus_region):
        """Create sample sectors for the Central Nexus"""
        sectors = []
        for i in range(1, 11):  # Create 10 sample sectors
            sector = Sector(
                sector_id=i,
                sector_number=i,
                name=f"Nexus Sector {i}",
                region_id=nexus_region.id,
                cluster_id=uuid.uuid4(),  # Mock cluster ID
                district='commerce_central' if i <= 5 else 'industrial_zone',
                security_level=8 if i <= 5 else 6,
                development_level=9 if i <= 5 else 7,
                traffic_level=8 if i <= 5 else 5,
                x_coord=i,
                y_coord=1,
                z_coord=0
            )
            sectors.append(sector)
            db.add(sector)
        
        db.flush()
        return sectors
    
    def test_get_nexus_status_exists(
        self, 
        client: TestClient, 
        db: Session,
        nexus_region: Region,
        sample_nexus_sectors: list,
        admin_auth_headers: dict
    ):
        """Test getting nexus status when it exists"""
        url = f"{settings.API_V1_STR}/nexus/status"
        response = client.get(url, headers=admin_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["exists"] is True
        assert data["status"] == "active"
        assert data["nexus_id"] == str(nexus_region.id)
        assert data["total_sectors"] >= 10  # At least our sample sectors
        assert "created_at" in data
    
    def test_get_nexus_status_not_exists(
        self, 
        client: TestClient, 
        admin_auth_headers: dict
    ):
        """Test getting nexus status when it doesn't exist"""
        url = f"{settings.API_V1_STR}/nexus/status"
        response = client.get(url, headers=admin_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["exists"] is False
        assert data["status"] == "not_generated"
        assert data["total_sectors"] == 0
        assert data["total_ports"] == 0
        assert data["total_planets"] == 0
    
    def test_get_nexus_statistics(
        self, 
        client: TestClient, 
        db: Session,
        nexus_region: Region,
        sample_nexus_sectors: list,
        admin_auth_headers: dict
    ):
        """Test getting comprehensive nexus statistics"""
        url = f"{settings.API_V1_STR}/nexus/stats"
        response = client.get(url, headers=admin_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert "total_sectors" in data
        assert "total_ports" in data
        assert "total_planets" in data
        assert "total_warp_gates" in data
        assert "active_players" in data
        assert "daily_traffic" in data
        assert "districts" in data
        assert isinstance(data["districts"], list)
    
    def test_get_districts_list(
        self, 
        client: TestClient, 
        db: Session,
        nexus_region: Region,
        sample_nexus_sectors: list,
        admin_auth_headers: dict
    ):
        """Test getting list of all districts"""
        url = f"{settings.API_V1_STR}/nexus/districts"
        response = client.get(url, headers=admin_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        
        if len(data) > 0:
            district = data[0]
            assert "district_type" in district
            assert "name" in district
            assert "sector_range" in district
            assert "sectors_count" in district
            assert "ports_count" in district
            assert "planets_count" in district
            assert "security_level" in district
            assert "development_level" in district
            assert "current_traffic" in district
    
    def test_get_district_details(
        self, 
        client: TestClient, 
        db: Session,
        nexus_region: Region,
        sample_nexus_sectors: list,
        admin_auth_headers: dict
    ):
        """Test getting detailed district information"""
        district_type = "commerce_central"
        url = f"{settings.API_V1_STR}/nexus/districts/{district_type}"
        response = client.get(url, headers=admin_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["district_type"] == district_type
        assert "total_sectors" in data
        assert "sector_range" in data
        assert "sample_sectors" in data
        assert "sample_ports" in data
        assert "sample_planets" in data
        assert isinstance(data["sample_sectors"], list)
    
    def test_get_district_details_invalid(
        self, 
        client: TestClient, 
        admin_auth_headers: dict
    ):
        """Test getting district details for invalid district"""
        district_type = "invalid_district"
        url = f"{settings.API_V1_STR}/nexus/districts/{district_type}"
        response = client.get(url, headers=admin_auth_headers)
        
        assert response.status_code == 400
        assert "Invalid district type" in response.json()["detail"]
    
    def test_generate_nexus_new(
        self, 
        client: TestClient, 
        admin_auth_headers: dict
    ):
        """Test generating new Central Nexus"""
        generation_data = {
            "force_regenerate": False,
            "preserve_player_data": True
        }
        
        url = f"{settings.API_V1_STR}/nexus/generate"
        response = client.post(url, json=generation_data, headers=admin_auth_headers)
        
        # Note: This is a long-running operation, so we expect it to be queued
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "task_id" in data
        assert "started generation" in data["message"].lower()
    
    def test_generate_nexus_force_regenerate(
        self, 
        client: TestClient, 
        db: Session,
        nexus_region: Region,
        admin_auth_headers: dict
    ):
        """Test force regenerating existing Central Nexus"""
        generation_data = {
            "force_regenerate": True,
            "preserve_player_data": True
        }
        
        url = f"{settings.API_V1_STR}/nexus/generate"
        response = client.post(url, json=generation_data, headers=admin_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "regeneration" in data["message"].lower()
    
    def test_generate_nexus_exists_no_force(
        self, 
        client: TestClient, 
        db: Session,
        nexus_region: Region,
        admin_auth_headers: dict
    ):
        """Test generating when nexus exists without force flag"""
        generation_data = {
            "force_regenerate": False,
            "preserve_player_data": True
        }
        
        url = f"{settings.API_V1_STR}/nexus/generate"
        response = client.post(url, json=generation_data, headers=admin_auth_headers)
        
        assert response.status_code == 409
        assert "already exists" in response.json()["detail"]
    
    def test_regenerate_district_success(
        self, 
        client: TestClient, 
        db: Session,
        nexus_region: Region,
        sample_nexus_sectors: list,
        admin_auth_headers: dict
    ):
        """Test successful district regeneration"""
        district_type = "commerce_central"
        
        url = f"{settings.API_V1_STR}/nexus/districts/{district_type}/regenerate"
        response = client.post(url, headers=admin_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "regeneration started" in data["message"].lower()
    
    def test_regenerate_district_preserve_data(
        self, 
        client: TestClient, 
        db: Session,
        nexus_region: Region,
        sample_nexus_sectors: list,
        admin_auth_headers: dict
    ):
        """Test district regeneration with data preservation"""
        district_type = "industrial_zone"
        
        url = f"{settings.API_V1_STR}/nexus/districts/{district_type}/regenerate"
        params = {"preserve_player_data": True}
        response = client.post(url, params=params, headers=admin_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
    
    def test_regenerate_district_no_preserve(
        self, 
        client: TestClient, 
        db: Session,
        nexus_region: Region,
        sample_nexus_sectors: list,
        admin_auth_headers: dict
    ):
        """Test district regeneration without data preservation"""
        district_type = "industrial_zone"
        
        url = f"{settings.API_V1_STR}/nexus/districts/{district_type}/regenerate"
        params = {"preserve_player_data": False}
        response = client.post(url, params=params, headers=admin_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
    
    def test_regenerate_district_invalid(
        self, 
        client: TestClient, 
        admin_auth_headers: dict
    ):
        """Test regenerating invalid district"""
        district_type = "invalid_district"
        
        url = f"{settings.API_V1_STR}/nexus/districts/{district_type}/regenerate"
        response = client.post(url, headers=admin_auth_headers)
        
        assert response.status_code == 400
        assert "Invalid district type" in response.json()["detail"]
    
    def test_regenerate_district_no_nexus(
        self, 
        client: TestClient, 
        admin_auth_headers: dict
    ):
        """Test regenerating district when no nexus exists"""
        district_type = "commerce_central"
        
        url = f"{settings.API_V1_STR}/nexus/districts/{district_type}/regenerate"
        response = client.post(url, headers=admin_auth_headers)
        
        assert response.status_code == 404
        assert "Central Nexus not found" in response.json()["detail"]
    
    def test_unauthorized_access(self, client: TestClient):
        """Test that endpoints require authentication"""
        endpoints = [
            "/nexus/status",
            "/nexus/stats", 
            "/nexus/districts",
            "/nexus/districts/commerce_central"
        ]
        
        for endpoint in endpoints:
            url = f"{settings.API_V1_STR}{endpoint}"
            response = client.get(url)
            assert response.status_code == 401
    
    def test_unauthorized_generation(self, client: TestClient):
        """Test that generation endpoints require admin authentication"""
        generation_data = {"force_regenerate": False}
        
        # Test without auth
        url = f"{settings.API_V1_STR}/nexus/generate"
        response = client.post(url, json=generation_data)
        assert response.status_code == 401
        
        # Test district regeneration without auth
        url = f"{settings.API_V1_STR}/nexus/districts/commerce_central/regenerate"
        response = client.post(url)
        assert response.status_code == 401


class TestNexusEndpointsWithComplexData:
    """Test nexus endpoints with more complex data scenarios"""
    
    @pytest.fixture
    def complex_nexus_setup(self, db: Session):
        """Create a complex nexus setup with multiple districts and data"""
        # Create Central Nexus region
        nexus_region = Region(
            name="central-nexus",
            display_name="Central Nexus",
            governance_type="galactic_council",
            economic_specialization="universal_hub",
            total_sectors=5000
        )
        db.add(nexus_region)
        db.flush()
        
        # Create sectors for multiple districts
        districts = [
            ('commerce_central', 1, 500),
            ('diplomatic_quarter', 501, 800),
            ('industrial_zone', 801, 1400)
        ]
        
        sectors = []
        for district_type, start, end in districts:
            for i in range(start, end + 1):
                sector = Sector(
                    sector_id=i,
                    sector_number=i,
                    name=f"Nexus Sector {i}",
                    region_id=nexus_region.id,
                    cluster_id=uuid.uuid4(),
                    district=district_type,
                    security_level=8 if district_type == 'commerce_central' else 6,
                    development_level=9 if district_type == 'commerce_central' else 7,
                    traffic_level=8 if district_type == 'commerce_central' else 5,
                    x_coord=i % 100,
                    y_coord=i // 100,
                    z_coord=0
                )
                sectors.append(sector)
                db.add(sector)
                
                # Add some stations and planets
                if i % 10 == 0:  # Every 10th sector gets a station
                    station = Station(
                        name=f"Nexus Station {i}",
                        sector_id=i,
                        station_class=StationClass.CLASS_6 if district_type == 'commerce_central' else StationClass.CLASS_3,
                        type=StationType.TRADING if district_type == 'commerce_central' else StationType.INDUSTRIAL
                    )
                    db.add(station)
                
                if i % 15 == 0:  # Every 15th sector gets a planet
                    planet = Planet(
                        sector_id=i,
                        region_id=nexus_region.id,
                        planet_type='Urban' if district_type == 'commerce_central' else 'Industrial',
                        population=1000000 if district_type == 'commerce_central' else 500000,
                        development_level=9 if district_type == 'commerce_central' else 7
                    )
                    db.add(planet)
        
        db.commit()
        return nexus_region, sectors
    
    def test_comprehensive_statistics(
        self, 
        client: TestClient,
        complex_nexus_setup,
        admin_auth_headers: dict
    ):
        """Test statistics with comprehensive data"""
        nexus_region, sectors = complex_nexus_setup
        
        url = f"{settings.API_V1_STR}/nexus/stats"
        response = client.get(url, headers=admin_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify comprehensive statistics
        assert data["total_sectors"] >= 1400  # We created 1400 sectors
        assert data["total_ports"] >= 140     # Approximately 1400/10
        assert data["total_planets"] >= 93    # Approximately 1400/15
        assert len(data["districts"]) >= 3    # At least 3 districts
        
        # Verify district breakdown
        districts = {d["district_type"]: d for d in data["districts"]}
        assert "commerce_central" in districts
        assert "diplomatic_quarter" in districts
        assert "industrial_zone" in districts
        
        # Verify district sector counts
        assert districts["commerce_central"]["sectors"] == 500
        assert districts["diplomatic_quarter"]["sectors"] == 300
        assert districts["industrial_zone"]["sectors"] == 600
    
    def test_district_details_with_samples(
        self, 
        client: TestClient,
        complex_nexus_setup,
        admin_auth_headers: dict
    ):
        """Test district details with sample data"""
        nexus_region, sectors = complex_nexus_setup
        
        # Test commerce central district
        url = f"{settings.API_V1_STR}/nexus/districts/commerce_central"
        response = client.get(url, headers=admin_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["district_type"] == "commerce_central"
        assert data["total_sectors"] == 500
        assert data["sector_range"] == [1, 500]
        assert len(data["sample_sectors"]) <= 10  # Up to 10 samples
        assert len(data["sample_ports"]) <= 10    # Up to 10 samples
        assert len(data["sample_planets"]) <= 10  # Up to 10 samples
        
        # Verify sample data structure
        if data["sample_sectors"]:
            sector = data["sample_sectors"][0]
            assert "sector_number" in sector
            assert "security_level" in sector
            assert "development_level" in sector
            assert "traffic_level" in sector
        
        if data["sample_ports"]:
            port = data["sample_ports"][0]
            assert "sector_id" in port
            assert "port_class" in port
            assert "port_type" in port
            assert "docking_fee" in port
        
        if data["sample_planets"]:
            planet = data["sample_planets"][0]
            assert "sector_id" in planet
            assert "planet_type" in planet
            assert "population" in planet
            assert "development_level" in planet
    
    def test_districts_list_comprehensive(
        self, 
        client: TestClient,
        complex_nexus_setup,
        admin_auth_headers: dict
    ):
        """Test districts list with comprehensive data"""
        nexus_region, sectors = complex_nexus_setup
        
        url = f"{settings.API_V1_STR}/nexus/districts"
        response = client.get(url, headers=admin_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        
        # Should have at least our 3 districts
        assert len(data) >= 3
        
        # Find our specific districts
        districts = {d["district_type"]: d for d in data}
        
        # Verify commerce central
        commerce = districts["commerce_central"]
        assert commerce["name"] == "Commerce Central"
        assert commerce["sectors_count"] == 500
        assert commerce["sector_range"] == [1, 500]
        assert commerce["security_level"] == 8.0
        assert commerce["development_level"] == 9.0
        
        # Verify industrial zone
        industrial = districts["industrial_zone"]
        assert industrial["name"] == "Industrial Zone"
        assert industrial["sectors_count"] == 600
        assert industrial["sector_range"] == [801, 1400]
        assert industrial["security_level"] == 6.0
        assert industrial["development_level"] == 7.0
    
    def test_performance_with_large_dataset(
        self, 
        client: TestClient,
        complex_nexus_setup,
        admin_auth_headers: dict
    ):
        """Test API performance with larger dataset"""
        import time
        
        nexus_region, sectors = complex_nexus_setup
        
        # Test status endpoint performance
        start_time = time.time()
        url = f"{settings.API_V1_STR}/nexus/status"
        response = client.get(url, headers=admin_auth_headers)
        status_time = time.time() - start_time
        
        assert response.status_code == 200
        assert status_time < 2.0  # Should complete within 2 seconds
        
        # Test statistics endpoint performance
        start_time = time.time()
        url = f"{settings.API_V1_STR}/nexus/stats"
        response = client.get(url, headers=admin_auth_headers)
        stats_time = time.time() - start_time
        
        assert response.status_code == 200
        assert stats_time < 5.0  # Should complete within 5 seconds
        
        # Test districts list performance
        start_time = time.time()
        url = f"{settings.API_V1_STR}/nexus/districts"
        response = client.get(url, headers=admin_auth_headers)
        districts_time = time.time() - start_time
        
        assert response.status_code == 200
        assert districts_time < 3.0  # Should complete within 3 seconds