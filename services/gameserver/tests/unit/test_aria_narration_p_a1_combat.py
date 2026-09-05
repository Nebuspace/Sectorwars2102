"""LEG-4258: combat-resolve victory emits ADR-0068 P-A1 aria_narration.

Exercises CombatService._emit_aria_combat_victory_narration (the hook called
from attack_player on ATTACKER_VICTORY / DEFENDER_VICTORY) with mocked
narration kernel + WS push — no DB combat fixture required.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.services.aria_narration_service import NarrationLine
from src.services.combat_service import CombatService


def _line(player_id: str = "winner") -> NarrationLine:
    return NarrationLine(
        event_id="P-A1",
        player_id=player_id,
        text=(
            "Got 'em. Their hull went at the third volley — looks like "
            "the laser stack worked. Logged."
        ),
        priority_rank=2,
        created_at=datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc),
        delivered_immediately=True,
    )


def test_combat_victory_attacker_emits_p_a1_and_pushes():
    """ATTACKER_VICTORY path: winner=attacker, dedupe_key=defender.id."""
    db = MagicMock()
    svc = CombatService(db)
    winner = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4())
    loser = SimpleNamespace(id=uuid.uuid4())
    line = _line(str(winner.id))
    narration = MagicMock()
    narration.record_event.return_value = line

    with (
        patch(
            "src.services.aria_narration_service.get_aria_narration_service",
            return_value=narration,
        ),
        patch(
            "src.services.aria_narration_service.resolve_assistance_level",
            return_value="standard",
        ),
        patch(
            "src.services.aria_narration_service.dispatch_narration_push",
        ) as push,
    ):
        svc._emit_aria_combat_victory_narration(winner, loser)

    narration.record_event.assert_called_once_with(
        "P-A1",
        winner.id,
        assistance_level="standard",
        dedupe_key=str(loser.id),
        context={},
    )
    push.assert_called_once_with(winner, line)
    payload = line.to_payload()
    assert payload["type"] == "aria_narration"
    assert payload["event_id"] == "P-A1"


def test_combat_victory_defender_emits_p_a1_and_pushes():
    """DEFENDER_VICTORY path: winner=defender, dedupe_key=attacker.id."""
    db = MagicMock()
    svc = CombatService(db)
    winner = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4())
    loser = SimpleNamespace(id=uuid.uuid4())
    line = _line(str(winner.id))
    narration = MagicMock()
    narration.record_event.return_value = line

    with (
        patch(
            "src.services.aria_narration_service.get_aria_narration_service",
            return_value=narration,
        ),
        patch(
            "src.services.aria_narration_service.resolve_assistance_level",
            return_value="full",
        ),
        patch(
            "src.services.aria_narration_service.dispatch_narration_push",
        ) as push,
    ):
        svc._emit_aria_combat_victory_narration(winner, loser)

    narration.record_event.assert_called_once_with(
        "P-A1",
        winner.id,
        assistance_level="full",
        dedupe_key=str(loser.id),
        context={},
    )
    push.assert_called_once_with(winner, line)


def test_combat_victory_suppressed_line_skips_push():
    """When record_event returns None (suppressed/gated), no WS push."""
    db = MagicMock()
    svc = CombatService(db)
    winner = SimpleNamespace(id=uuid.uuid4())
    loser = SimpleNamespace(id=uuid.uuid4())
    narration = MagicMock()
    narration.record_event.return_value = None

    with (
        patch(
            "src.services.aria_narration_service.get_aria_narration_service",
            return_value=narration,
        ),
        patch(
            "src.services.aria_narration_service.resolve_assistance_level",
            return_value="standard",
        ),
        patch(
            "src.services.aria_narration_service.dispatch_narration_push",
        ) as push,
    ):
        svc._emit_aria_combat_victory_narration(winner, loser)

    push.assert_not_called()


def test_attack_player_aria_block_wires_p_a1_for_attacker_and_defender_victory():
    """Source pin: attack_player victory ARIA block calls the P-A1 helper
    with winner/loser; draws leave winner None (no call)."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "src" / "services" / "combat_service.py"
    ).read_text()
    # Hook lives inside the existing winner ARIA block.
    assert "_emit_aria_combat_victory_narration" in source
    assert "loser=defender if winner is attacker else attacker" in source
