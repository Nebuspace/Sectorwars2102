"""LEG-391: players_present carries Ship.attack_turn_cost for Combat HUD tips.

Canon: FEATURES/gameplay/combat.md — defender-side turn cost is the hull's
attack_turn_cost. Payload must surface the stored column (or JSON null when
unset) — never invent the combat resolver's ``or 2`` floor.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.services.intrasystem_movement_service import (
    build_presence_entry,
    enrich_presence_with_live_pose,
)


def test_build_presence_entry_seeded_hull_cost() -> None:
    """Seeder-style cost (Light Freighter = 20) lands on the tip key."""
    entry = build_presence_entry(
        player_id=uuid.uuid4(),
        username="pilot",
        ship_id=uuid.uuid4(),
        ship_name="Hauler",
        ship_type="LIGHT_FREIGHTER",
        team_id=None,
        arrived_at=datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc),
        attack_turn_cost=20,
    )
    assert entry["attack_turn_cost"] == 20


def test_build_presence_entry_unset_is_json_null() -> None:
    """Unset cost stays None in the payload — no invented default."""
    entry = build_presence_entry(
        player_id=uuid.uuid4(),
        username="podless",
        ship_id=None,
        ship_name=None,
        ship_type=None,
        team_id=None,
        arrived_at=datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc),
        attack_turn_cost=None,
    )
    assert entry["attack_turn_cost"] is None
    assert "attack_turn_cost" in entry


def test_enrich_rederives_attack_turn_cost_from_ship_row() -> None:
    """REST enrich overwrites tip field from live Ship.attack_turn_cost."""
    pid = uuid.uuid4()
    ship_id = uuid.uuid4()
    ship = SimpleNamespace(id=ship_id, attack_turn_cost=12)
    player = SimpleNamespace(
        id=pid,
        current_ship_id=ship_id,
        intrasystem_pose=None,
        reputation_tier=None,
    )

    db = MagicMock()
    # Order of .all() calls inside enrich: players, medal counts (skipped
    # when human_ids path runs medals query), ships. Structure the mock
    # chain to return players then ships for the two entity queries that
    # matter; medal query returns empty.
    player_q = MagicMock()
    player_q.filter.return_value = player_q
    player_q.all.return_value = [player]

    medal_q = MagicMock()
    medal_q.filter.return_value = medal_q
    medal_q.group_by.return_value = medal_q
    medal_q.all.return_value = []

    ship_q = MagicMock()
    ship_q.filter.return_value = ship_q
    ship_q.all.return_value = [ship]

    def _query(entity, *args):
        name = getattr(entity, "__name__", None) or getattr(
            getattr(entity, "class_", None), "__name__", ""
        )
        # PlayerMedal.player_id column path vs Ship/Player entity
        ent_str = str(entity)
        if "PlayerMedal" in ent_str or (
            hasattr(entity, "class_") and "PlayerMedal" in str(entity.class_)
        ):
            return medal_q
        if name == "Ship" or "Ship" in ent_str and "Player" not in ent_str:
            # Distinguish Ship entity from Player
            pass
        # First query is Player (via _enrich_player_lookup_query), then
        # medals, then Ship — use call count.
        return player_q

    call_n = {"n": 0}

    def _query_seq(entity, *args):
        call_n["n"] += 1
        if call_n["n"] == 1:
            return player_q
        if call_n["n"] == 2:
            return medal_q
        return ship_q

    db.query.side_effect = _query_seq

    stale = [
        {
            "player_id": str(pid),
            "username": "scout",
            "ship_id": str(ship_id),
            "ship_name": "Dart",
            "ship_type": "SCOUT",
            "team_id": None,
            "arrived_at": "2026-08-17T12:00:00+00:00",
            # pre-LEG-391 mirror omitted the key entirely
        }
    ]
    out = enrich_presence_with_live_pose(db, stale)
    assert len(out) == 1
    assert out[0]["attack_turn_cost"] == 12


def test_enrich_unset_ship_cost_stays_null() -> None:
    pid = uuid.uuid4()
    ship_id = uuid.uuid4()
    ship = SimpleNamespace(id=ship_id, attack_turn_cost=None)
    player = SimpleNamespace(
        id=pid,
        current_ship_id=ship_id,
        intrasystem_pose=None,
        reputation_tier=None,
    )

    player_q = MagicMock()
    player_q.filter.return_value = player_q
    player_q.all.return_value = [player]
    medal_q = MagicMock()
    medal_q.filter.return_value = medal_q
    medal_q.group_by.return_value = medal_q
    medal_q.all.return_value = []
    ship_q = MagicMock()
    ship_q.filter.return_value = ship_q
    ship_q.all.return_value = [ship]

    db = MagicMock()
    call_n = {"n": 0}

    def _query_seq(entity, *args):
        call_n["n"] += 1
        if call_n["n"] == 1:
            return player_q
        if call_n["n"] == 2:
            return medal_q
        return ship_q

    db.query.side_effect = _query_seq

    out = enrich_presence_with_live_pose(
        db,
        [{"player_id": str(pid), "username": "x", "is_npc": False}],
    )
    assert out[0]["attack_turn_cost"] is None
