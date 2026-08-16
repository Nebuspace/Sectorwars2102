"""LEG-75 — WS get_sector_players mirrors REST medal pin fields (DB-free)."""

from __future__ import annotations

from src.services.websocket_service import ConnectionManager


def test_get_sector_players_surfaces_pinned_medal_fields():
    cm = ConnectionManager()
    cm.sector_connections[7] = {"user-1"}
    cm.connection_metadata["user-1"] = {
        "user_data": {
            "username": "Ace",
            "reputation_tier": "Neutral",
            "pinned_medal_id": "bronze_cluster",
            "medal_count": 3,
        },
    }
    players = cm.get_sector_players(7)
    assert len(players) == 1
    assert players[0]["pinned_medal_id"] == "bronze_cluster"
    assert players[0]["medal_count"] == 3


def test_get_sector_players_medal_fields_default_none_when_absent():
    cm = ConnectionManager()
    cm.sector_connections[1] = {"u"}
    cm.connection_metadata["u"] = {
        "user_data": {"username": "p", "reputation_tier": "Lawful"},
    }
    players = cm.get_sector_players(1)
    assert players[0]["pinned_medal_id"] is None
    assert players[0]["medal_count"] is None


def test_get_sector_players_respects_explicit_null_count_privacy():
    """Client checks typeof medal_count === 'number'; null hides the count."""
    cm = ConnectionManager()
    cm.sector_connections[2] = {"u2"}
    cm.connection_metadata["u2"] = {
        "user_data": {
            "username": "Quiet",
            "reputation_tier": "Heroic",
            "pinned_medal_id": "x",
            "medal_count": None,
        },
    }
    players = cm.get_sector_players(2)
    assert players[0]["pinned_medal_id"] == "x"
    assert players[0]["medal_count"] is None
