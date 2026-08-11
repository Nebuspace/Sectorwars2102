#!/usr/bin/env python3
"""
Test script for faction system endpoints.
"""

import requests
import sys

# Base URL for the API (using container hostname)
BASE_URL = "http://gameserver:8080/api/v1"

# Test credentials
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin"
PLAYER_USERNAME = "testplayer"
PLAYER_PASSWORD = "testpass123"


def get_admin_token():
    """Get admin authentication token."""
    response = requests.post(
        f"{BASE_URL}/auth/login",
        data={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    if response.status_code == 200:
        return response.json()["access_token"]
    else:
        print(f"Failed to get admin token: {response.status_code} - {response.text}")
        return None


def get_player_token():
    """Get player authentication token."""
    # First try to login
    response = requests.post(
        f"{BASE_URL}/auth/login",
        data={"username": PLAYER_USERNAME, "password": PLAYER_PASSWORD}
    )
    
    if response.status_code == 200:
        return response.json()["access_token"]
    
    # If login fails, try to register
    print("Player not found, attempting to register...")
    response = requests.post(
        f"{BASE_URL}/auth/register",
        json={
            "username": PLAYER_USERNAME,
            "password": PLAYER_PASSWORD,
            "email": "testplayer@example.com"
        }
    )
    
    if response.status_code == 200:
        print("Registration successful, logging in...")
        # Now login
        response = requests.post(
            f"{BASE_URL}/auth/login",
            data={"username": PLAYER_USERNAME, "password": PLAYER_PASSWORD}
        )
        if response.status_code == 200:
            return response.json()["access_token"]
        else:
            print(f"Login after registration failed: {response.text}")
    
    print(f"Failed to get player token: {response.text}")
    return None


def test_public_faction_endpoints(player_token):
    """Test player-accessible faction endpoints."""
    headers = {"Authorization": f"Bearer {player_token}"}
    
    print("\n=== Testing Player Faction Endpoints ===")
    
    # List all factions
    print("\n1. Listing all factions:")
    response = requests.get(f"{BASE_URL}/factions/", headers=headers)
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        factions = response.json()
        for faction in factions:
            print(f"  - {faction['name']} ({faction['faction_type']})")
            print(f"    Territory: {faction['territory_count']} sectors")
        faction_id = factions[0]['id'] if factions else None
    else:
        print(f"Error: {response.text}")
        faction_id = None
    
    # Get player reputations
    print("\n2. Getting player reputations:")
    response = requests.get(f"{BASE_URL}/factions/reputation", headers=headers)
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        reputations = response.json()
        for rep in reputations:
            print(f"  - {rep['faction_name']}: {rep['current_value']} ({rep['title']})")
            print(f"    Trade modifier: {rep['trade_modifier']}, Combat: {rep['combat_response']}")
    else:
        print(f"Error: {response.text}")
    
    # Get specific faction reputation
    if faction_id:
        print(f"\n3. Getting reputation with specific faction {faction_id}:")
        response = requests.get(f"{BASE_URL}/factions/{faction_id}/reputation", headers=headers)
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            rep = response.json()
            print(f"  Faction: {rep['faction_name']}")
            print(f"  Reputation: {rep['current_value']} ({rep['title']})")
        else:
            print(f"Error: {response.text}")
    
    # Get available missions
    print("\n4. Getting available missions:")
    response = requests.get(f"{BASE_URL}/factions/missions", headers=headers)
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        missions = response.json()
        print(f"  Found {len(missions)} available missions")
        for mission in missions[:3]:  # Show first 3
            print(f"  - {mission['title']} from {mission['faction_name']}")
            print(f"    Rewards: {mission['credit_reward']} credits, {mission['reputation_reward']} reputation")
    else:
        print(f"Error: {response.text}")
    
    # Get faction territory
    if faction_id:
        print(f"\n5. Getting faction territory:")
        response = requests.get(f"{BASE_URL}/factions/{faction_id}/territory", headers=headers)
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            territory = response.json()
            print(f"  Faction: {territory['faction_name']}")
            print(f"  Controls {len(territory['sectors'])} sectors")
        else:
            print(f"Error: {response.text}")
    
    # Get pricing modifier
    if faction_id:
        print(f"\n6. Getting pricing modifier:")
        response = requests.get(f"{BASE_URL}/factions/{faction_id}/pricing-modifier", headers=headers)
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            pricing = response.json()
            print(f"  Faction: {pricing['faction_name']}")
            print(f"  Base modifier: {pricing['base_modifier']}")
            print(f"  Your modifier: {pricing['player_modifier']} ({pricing['description']})")
        else:
            print(f"Error: {response.text}")


def test_admin_faction_endpoints(admin_token):
    """Test admin faction management endpoints."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    print("\n=== Testing Admin Faction Endpoints ===")
    
    # List all factions with details
    print("\n1. Listing all factions (admin view):")
    response = requests.get(f"{BASE_URL}/admin/factions/", headers=headers)
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        factions = response.json()
        for faction in factions[:2]:  # Show first 2
            print(f"  - {faction['name']} ({faction['faction_type']})")
            print(f"    ID: {faction['id']}")
            print(f"    Pricing modifier: {faction['base_pricing_modifier']}")
            print(f"    Aggression: {faction['aggression_level']}, Stance: {faction['diplomacy_stance']}")
        faction_id = factions[0]['id'] if factions else None
    else:
        print(f"Error: {response.text}")
        faction_id = None
    
    # Create a test faction
    print("\n2. Creating a test faction:")
    test_faction_data = {
        "name": "Test Mining Consortium",
        "faction_type": "Merchants",
        "description": "A test faction for mining operations",
        "base_pricing_modifier": 0.85,
        "trade_specialties": ["ore", "minerals", "metals"],
        "aggression_level": 3,
        "diplomacy_stance": "friendly",
        "color_primary": "#FFD700",
        "color_secondary": "#696969"
    }
    response = requests.post(
        f"{BASE_URL}/admin/factions/",
        headers=headers,
        json=test_faction_data
    )
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        new_faction = response.json()
        print(f"  Created faction: {new_faction['name']} (ID: {new_faction['id']})")
        test_faction_id = new_faction['id']
    else:
        print(f"Error: {response.text}")
        test_faction_id = None
    
    # Update faction
    if test_faction_id:
        print(f"\n3. Updating test faction:")
        update_data = {
            "description": "Updated description for test faction",
            "base_pricing_modifier": 0.80,
            "aggression_level": 2
        }
        response = requests.put(
            f"{BASE_URL}/admin/factions/{test_faction_id}",
            headers=headers,
            json=update_data
        )
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            print("  Faction updated successfully")
        else:
            print(f"Error: {response.text}")
    
    # Create a mission
    if faction_id:
        print(f"\n4. Creating a mission for faction {faction_id}:")
        mission_data = {
            "title": "Deliver Medical Supplies",
            "description": "Transport urgently needed medical supplies to frontier colonies",
            "mission_type": "cargo_delivery",
            "credit_reward": 50000,
            "reputation_reward": 25,
            "min_reputation": -200,
            "min_level": 1,
            "cargo_type": "medical_supplies",
            "cargo_quantity": 100
        }
        response = requests.post(
            f"{BASE_URL}/admin/factions/{faction_id}/missions",
            headers=headers,
            json=mission_data
        )
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            result = response.json()
            print(f"  Created mission: {result['title']} (ID: {result['mission_id']})")
        else:
            print(f"Error: {response.text}")
    
    # List all missions
    print("\n5. Listing all missions:")
    response = requests.get(
        f"{BASE_URL}/admin/factions/missions/all",
        headers=headers
    )
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        missions = response.json()
        print(f"  Found {len(missions)} total missions")
        for mission in missions[:3]:  # Show first 3
            print(f"  - {mission['title']} ({mission['faction_name']})")
            print(f"    Type: {mission['mission_type']}, Rewards: {mission['credit_reward']} credits")
    else:
        print(f"Error: {response.text}")
    
    # DELETE /admin/factions/{id} retired (WO-CLEANUP-MINOR-DEAD-ADMIN-ENDPOINTS-BATCH);
    # no UI caller — leave test faction in place for manual cleanup if needed.
    if test_faction_id:
        print(f"\n6. Skip delete — admin faction DELETE route retired (id={test_faction_id})")


def main():
    """Run all faction system tests."""
    print("Testing Faction System Endpoints")
    print("=" * 50)
    
    # Get tokens
    admin_token = get_admin_token()
    if not admin_token:
        print("Failed to get admin token, exiting...")
        sys.exit(1)
    
    player_token = get_player_token()
    if not player_token:
        print("Failed to get player token, exiting...")
        sys.exit(1)
    
    # Run tests
    test_public_faction_endpoints(player_token)
    test_admin_faction_endpoints(admin_token)
    
    print("\n" + "=" * 50)
    print("Faction system testing complete!")


if __name__ == "__main__":
    main()