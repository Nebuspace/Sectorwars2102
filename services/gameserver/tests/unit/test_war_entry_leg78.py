"""LEG-78 — WarEntry response_model retains victory/cease fields (DB-free)."""

from __future__ import annotations

from src.api.routes.teams import WarEntry


def test_war_entry_preserves_victory_fields_from_service_payload():
    """team_war_service writes these keys onto ceased wars; list_wars must
    not strip them via a narrow response_model."""
    raw = {
        "target_team_id": "team-b",
        "declared_by": "player-1",
        "declared_at": "2026-08-16T12:00:00+00:00",
        "reason": "skirmish",
        "status": "ceased",
        "score": {"us": 10, "them": 2},
        "ceased_at": "2026-08-16T13:00:00+00:00",
        "cease_reason": "victory",
        "winner_team_id": "team-a",
        "loser_team_id": "team-b",
        "victory_at": "2026-08-16T13:00:00+00:00",
    }
    entry = WarEntry.model_validate(raw)
    dumped = entry.model_dump()
    assert dumped["cease_reason"] == "victory"
    assert dumped["winner_team_id"] == "team-a"
    assert dumped["loser_team_id"] == "team-b"
    assert dumped["victory_at"] == "2026-08-16T13:00:00+00:00"
    assert dumped["ceased_at"] == "2026-08-16T13:00:00+00:00"


def test_war_entry_active_war_omits_victory_keys_as_null():
    raw = {
        "target_team_id": "team-b",
        "declared_by": "player-1",
        "declared_at": "2026-08-16T12:00:00+00:00",
        "reason": "",
        "status": "active",
        "score": {"us": 0, "them": 0},
    }
    entry = WarEntry.model_validate(raw)
    dumped = entry.model_dump()
    assert dumped["status"] == "active"
    assert dumped["cease_reason"] is None
    assert dumped["winner_team_id"] is None
    assert dumped["loser_team_id"] is None
    assert dumped["victory_at"] is None


def test_war_entry_preserves_manual_ceasefire_fields():
    raw = {
        "target_team_id": "team-b",
        "declared_by": "player-1",
        "declared_at": "2026-08-16T12:00:00+00:00",
        "reason": "",
        "status": "ceased",
        "score": {"us": 1, "them": 1},
        "ceased_at": "2026-08-16T12:30:00+00:00",
        "ceased_by": "player-2",
    }
    entry = WarEntry.model_validate(raw)
    dumped = entry.model_dump()
    assert dumped["ceased_by"] == "player-2"
    assert dumped["cease_reason"] is None
    assert dumped["winner_team_id"] is None
