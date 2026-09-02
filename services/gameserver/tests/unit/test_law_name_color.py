"""LEG-4135 — Wanted/Suspect name_color override (invent=0).

Canon ranking.md: Wanted red overrides Suspect amber overrides tier color.
Hex pinned to RankDisplay law chrome (#FF4444 / #FFAA44).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.models.player import Player
from src.services import suspect_service, wanted_service
from src.services.law_name_color import (
    SUSPECT_NAME_COLOR,
    WANTED_NAME_COLOR,
    apply_law_name_color,
)
from src.services.personal_reputation_service import PersonalReputationService

FROZEN_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)


def _player(**kw) -> Player:
    defaults = dict(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        is_wanted=False,
        is_suspect=False,
        wanted_until=None,
        wanted_declared_at=None,
        suspect_until=None,
        suspect_declared_at=None,
        personal_reputation=0,
        reputation_tier="Neutral",
        name_color="#FFFFFF",
    )
    defaults.update(kw)
    return Player(**defaults)


class _RepDb:
    """Minimal session for PersonalReputationService.adjust_reputation."""

    def __init__(self, player: Player):
        self._player = player
        self.flushed = False

    def query(self, entity):
        self._entity = entity
        return self

    def filter(self, *a, **k):
        return self

    def first(self):
        # Ship stolen-pilot check in recompute_is_wanted — no stolen ship.
        from src.models.ship import Ship

        if self._entity is Ship or getattr(self._entity, "class_", None) is Ship:
            return None
        if self._entity is Player or self._entity is Player.id:
            return self._player
        return None

    def flush(self):
        self.flushed = True


@pytest.mark.unit
def test_apply_law_name_color_wanted_overrides_suspect():
    player = _player(is_wanted=True, is_suspect=True, personal_reputation=100)
    assert apply_law_name_color(player) == WANTED_NAME_COLOR
    assert player.name_color == WANTED_NAME_COLOR


@pytest.mark.unit
def test_apply_law_name_color_suspect_alone():
    player = _player(is_suspect=True, personal_reputation=100)
    assert apply_law_name_color(player) == SUSPECT_NAME_COLOR
    assert player.name_color == SUSPECT_NAME_COLOR


@pytest.mark.unit
def test_apply_law_name_color_restores_tier_when_clear():
    player = _player(personal_reputation=300, name_color=WANTED_NAME_COLOR)
    color = apply_law_name_color(player)
    _tier, expected = PersonalReputationService._get_tier_for_score(300)
    assert color == expected
    assert player.name_color == expected


@pytest.mark.unit
def test_apply_wanted_event_sets_wanted_red():
    player = _player(is_suspect=True, name_color=SUSPECT_NAME_COLOR)
    wanted_service.apply_wanted_event(None, player, now=FROZEN_NOW)
    assert player.is_wanted is True
    assert player.name_color == WANTED_NAME_COLOR


@pytest.mark.unit
def test_clear_expired_wanted_restores_suspect_amber():
    player = _player(
        is_wanted=True,
        is_suspect=True,
        wanted_until=FROZEN_NOW - timedelta(minutes=1),
        wanted_declared_at=FROZEN_NOW - timedelta(hours=5),
        name_color=WANTED_NAME_COLOR,
        personal_reputation=50,
    )

    class _ClearDb:
        def query(self, entity):
            assert entity is Player
            return self

        def filter(self, *a, **k):
            return self

        def all(self):
            return [player]

        def flush(self):
            pass

    wanted_service.clear_expired_wanted(_ClearDb(), now=FROZEN_NOW)
    assert player.is_wanted is False
    assert player.is_suspect is True
    assert player.name_color == SUSPECT_NAME_COLOR


@pytest.mark.unit
def test_clear_expired_suspects_restores_tier_color():
    player = _player(
        is_suspect=True,
        suspect_until=FROZEN_NOW - timedelta(minutes=1),
        suspect_declared_at=FROZEN_NOW - timedelta(hours=2),
        name_color=SUSPECT_NAME_COLOR,
        personal_reputation=300,
    )

    class _ClearDb:
        def query(self, entity):
            assert entity is Player
            return self

        def filter(self, *a, **k):
            return self

        def all(self):
            return [player]

        def flush(self):
            pass

    suspect_service.clear_expired_suspects(_ClearDb(), now=FROZEN_NOW)
    assert player.is_suspect is False
    _tier, expected = PersonalReputationService._get_tier_for_score(300)
    assert player.name_color == expected


@pytest.mark.unit
def test_reputation_adjust_while_wanted_keeps_red():
    player = _player(
        is_wanted=True,
        wanted_until=FROZEN_NOW + timedelta(hours=12),
        wanted_declared_at=FROZEN_NOW,
        personal_reputation=0,
        name_color=WANTED_NAME_COLOR,
    )
    db = _RepDb(player)
    result = PersonalReputationService(db).adjust_reputation(
        player.id, 10, "test_while_wanted"
    )
    assert result["success"] is True
    assert player.is_wanted is True
    assert player.name_color == WANTED_NAME_COLOR
    assert result["color"] == WANTED_NAME_COLOR


@pytest.mark.unit
def test_apply_suspect_event_sets_amber(monkeypatch):
    player = _player(personal_reputation=0, name_color="#FFFFFF")

    # Skip rep penalty DB path; still assert law color after event.
    monkeypatch.setattr(
        suspect_service,
        "_apply_rep_penalty",
        lambda *a, **k: None,
    )
    first = suspect_service.apply_suspect_event(
        MagicMock(), player, reason="early_salvage", now=FROZEN_NOW
    )
    assert first is True
    assert player.is_suspect is True
    assert player.name_color == SUSPECT_NAME_COLOR
