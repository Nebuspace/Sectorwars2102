"""Deepen coverage for wanted_service timer APIs (apply / is_live / clear).

Companion to test_wanted_triggers.py (recompute + reputation integration).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.models.player import Player
from src.services import wanted_service

FROZEN_NOW = datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)


def _player(**kw) -> Player:
    defaults = dict(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        is_wanted=False,
        wanted_until=None,
        wanted_declared_at=None,
        personal_reputation=0,
    )
    defaults.update(kw)
    return Player(**defaults)


class _ClearDb:
    def __init__(self, players):
        self._players = list(players)
        self.flushed = False

    def query(self, entity):
        assert entity is Player
        return self

    def filter(self, *a, **k):
        return self

    def all(self):
        return list(self._players)

    def flush(self):
        self.flushed = True


@pytest.mark.unit
def test_is_live_wanted_requires_flag_and_future_until():
    assert wanted_service.is_live_wanted(_player(), now=FROZEN_NOW) is False
    assert (
        wanted_service.is_live_wanted(
            _player(is_wanted=True, wanted_until=None), now=FROZEN_NOW,
        )
        is False
    )
    assert (
        wanted_service.is_live_wanted(
            _player(
                is_wanted=True,
                wanted_until=FROZEN_NOW - timedelta(seconds=1),
            ),
            now=FROZEN_NOW,
        )
        is False
    )
    assert (
        wanted_service.is_live_wanted(
            _player(
                is_wanted=True,
                wanted_until=FROZEN_NOW + timedelta(hours=1),
            ),
            now=FROZEN_NOW,
        )
        is True
    )


@pytest.mark.unit
def test_is_live_wanted_tolerates_simple_namespace_without_law_flags():
    """Regression: combat DB-free mocks often omit is_wanted/wanted_until.

    Closer bounce on LEG-4137/#1473 — unconditional attr access broke 9
    unit tests after attack_player started calling is_live_wanted.
    """
    from types import SimpleNamespace

    stub = SimpleNamespace(id=uuid.uuid4())
    assert wanted_service.is_live_wanted(stub, now=FROZEN_NOW) is False


@pytest.mark.unit
def test_apply_wanted_event_first_acquisition_stamps_declared_at():
    player = _player()
    first = wanted_service.apply_wanted_event(None, player, now=FROZEN_NOW)

    assert first is True
    assert player.is_wanted is True
    assert player.wanted_declared_at == FROZEN_NOW
    assert player.wanted_until == FROZEN_NOW + wanted_service.WANTED_DURATION


@pytest.mark.unit
def test_apply_wanted_event_refresh_does_not_restamp_declared_at():
    declared = FROZEN_NOW - timedelta(hours=2)
    player = _player(
        is_wanted=True,
        wanted_declared_at=declared,
        wanted_until=FROZEN_NOW + timedelta(hours=1),
    )
    first = wanted_service.apply_wanted_event(None, player, now=FROZEN_NOW)

    assert first is False
    assert player.wanted_declared_at == declared
    assert player.wanted_until == FROZEN_NOW + wanted_service.WANTED_DURATION


@pytest.mark.unit
def test_clear_expired_wanted_clears_and_flushes():
    expired = _player(
        is_wanted=True,
        wanted_until=FROZEN_NOW - timedelta(minutes=1),
        wanted_declared_at=FROZEN_NOW - timedelta(hours=5),
    )
    live = _player(
        is_wanted=True,
        wanted_until=FROZEN_NOW + timedelta(hours=1),
        wanted_declared_at=FROZEN_NOW,
    )
    # Fake returns only the expired set (filter is applied in production;
    # we feed the query result the sweep would see).
    db = _ClearDb([expired])

    cleared = wanted_service.clear_expired_wanted(db, now=FROZEN_NOW)

    assert cleared == 1
    assert expired.is_wanted is False
    assert expired.wanted_until is None
    assert expired.wanted_declared_at is None
    assert db.flushed is True
    # live player not in the fake query result — untouched by construction
    assert live.is_wanted is True
