"""WO-BUILD-WANTED-TRIGGERS-STOLEN-SHIP-LOW-REP: the stolen-ship-piloting
and personal_reputation < -500 Wanted triggers (ranking.md "Wanted status").

Canon: sw2102-docs/FEATURES/gameplay/ranking.md#wanted-status -- ``Player.
is_wanted = True`` fires on EITHER (a) piloting a ship the registered owner
has reported stolen, or (b) ``personal_reputation < -500``, unioned with the
pre-existing Severe black-market-bust timer trigger
(``wanted_service.apply_wanted_event`` / ``wanted_until``).
``wanted_service.recompute_is_wanted`` is the single OR-combination point;
this file pins its own logic plus the ``personal_reputation_service``
integration. The stolen-ship report/retract integration is covered
separately in ``test_ship_registry_behaviors.py::TestWantedTriggerWiring``.

DB-free: ``_is_piloting_stolen_ship`` issues one query
(``db.query(Ship.id).filter(...).first()``); a minimal fake session below
returns a canned value for it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.models.player import Player
from src.models.ship import Ship
from src.services import wanted_service
from src.services.personal_reputation_service import PersonalReputationService

FROZEN_NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)


def _player(
    *,
    is_wanted: bool = False,
    wanted_until=None,
    wanted_declared_at=None,
    personal_reputation: int = 0,
) -> Player:
    return Player(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        is_wanted=is_wanted,
        wanted_until=wanted_until,
        wanted_declared_at=wanted_declared_at,
        personal_reputation=personal_reputation,
    )


class _FakeShipQuery:
    def __init__(self, hit: bool):
        self._hit = hit

    def filter(self, *a, **k):
        return self

    def first(self):
        return uuid.uuid4() if self._hit else None


class _FakeDb:
    """Stands in for ``_is_piloting_stolen_ship``'s single ``Ship.id`` query."""

    def __init__(self, *, piloting_stolen: bool = False):
        self._piloting_stolen = piloting_stolen

    def query(self, entity):
        assert entity is Ship.id
        return _FakeShipQuery(self._piloting_stolen)


@pytest.mark.unit
class TestRecomputeIsWanted:
    def test_reputation_trigger_flips_true_below_threshold(self):
        player = _player(personal_reputation=-501)
        db = _FakeDb(piloting_stolen=False)

        flipped = wanted_service.recompute_is_wanted(db, player, now=FROZEN_NOW)

        assert flipped is True
        assert player.is_wanted is True
        assert player.wanted_declared_at == FROZEN_NOW
        assert player.wanted_until is None  # not the timer-based trigger

    def test_reputation_exactly_at_threshold_does_not_trigger(self):
        player = _player(personal_reputation=-500)
        db = _FakeDb(piloting_stolen=False)

        wanted_service.recompute_is_wanted(db, player, now=FROZEN_NOW)

        assert player.is_wanted is False

    def test_reputation_recovery_clears_wanted(self):
        player = _player(
            is_wanted=True, personal_reputation=-499, wanted_declared_at=FROZEN_NOW - timedelta(hours=2),
        )
        db = _FakeDb(piloting_stolen=False)

        flipped = wanted_service.recompute_is_wanted(db, player, now=FROZEN_NOW)

        assert flipped is True
        assert player.is_wanted is False
        assert player.wanted_declared_at is None

    def test_stolen_piloting_trigger_flips_true(self):
        player = _player(personal_reputation=0)
        db = _FakeDb(piloting_stolen=True)

        wanted_service.recompute_is_wanted(db, player, now=FROZEN_NOW)

        assert player.is_wanted is True
        assert player.wanted_until is None

    def test_no_trigger_active_stays_false_and_is_a_noop(self):
        player = _player(personal_reputation=0)
        db = _FakeDb(piloting_stolen=False)

        flipped = wanted_service.recompute_is_wanted(db, player, now=FROZEN_NOW)

        assert flipped is False
        assert player.is_wanted is False

    def test_bust_timer_trigger_keeps_wanted_even_with_good_reputation(self):
        player = _player(
            is_wanted=True, personal_reputation=0, wanted_until=FROZEN_NOW + timedelta(hours=1),
            wanted_declared_at=FROZEN_NOW - timedelta(hours=1),
        )
        db = _FakeDb(piloting_stolen=False)

        flipped = wanted_service.recompute_is_wanted(db, player, now=FROZEN_NOW)

        assert flipped is False  # already wanted, stays wanted -- no-op
        assert player.is_wanted is True

    def test_already_wanted_from_one_trigger_does_not_restamp_declared_at(self):
        declared_at = FROZEN_NOW - timedelta(hours=3)
        player = _player(is_wanted=True, personal_reputation=-600, wanted_declared_at=declared_at)
        db = _FakeDb(piloting_stolen=False)

        wanted_service.recompute_is_wanted(db, player, now=FROZEN_NOW)

        assert player.wanted_declared_at == declared_at  # untouched, not a fresh acquisition

    def test_stolen_and_reputation_both_active_survives_one_clearing(self):
        """OR-combination: stolen-piloting clearing alone must not clear
        is_wanted while the reputation trigger still independently holds."""
        player = _player(is_wanted=True, personal_reputation=-600)
        db = _FakeDb(piloting_stolen=False)  # stolen condition already cleared by caller

        flipped = wanted_service.recompute_is_wanted(db, player, now=FROZEN_NOW)

        assert flipped is False
        assert player.is_wanted is True


class _FakeReputationQuery:
    def __init__(self, player):
        self._player = player

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._player


class _FakeReputationDb:
    """PersonalReputationService only ever queries Player (itself) + the
    wanted-trigger's Ship.id lookup -- both routed to canned results."""

    def __init__(self, player, *, piloting_stolen: bool = False):
        self._player = player
        self._piloting_stolen = piloting_stolen
        self.flushed = False

    def query(self, entity):
        if entity is Player:
            return _FakeReputationQuery(self._player)
        if entity is Ship.id:
            return _FakeShipQuery(self._piloting_stolen)
        raise AssertionError(f"unexpected query entity: {entity!r}")

    def flush(self):
        self.flushed = True


@pytest.mark.unit
class TestPersonalReputationServiceWantedIntegration:
    def test_adjust_reputation_below_threshold_sets_is_wanted(self):
        player = _player(personal_reputation=-450, is_wanted=False)
        db = _FakeReputationDb(player)
        svc = PersonalReputationService(db)

        svc.adjust_reputation(player.id, -100, reason="test")

        assert player.personal_reputation == -550
        assert player.is_wanted is True

    def test_adjust_reputation_recovering_above_threshold_clears_is_wanted(self):
        player = _player(personal_reputation=-520, is_wanted=True, wanted_declared_at=FROZEN_NOW)
        db = _FakeReputationDb(player)
        svc = PersonalReputationService(db)

        svc.adjust_reputation(player.id, 100, reason="test")

        assert player.personal_reputation == -420
        assert player.is_wanted is False
        assert player.wanted_declared_at is None

    def test_weekly_decay_recovering_above_threshold_clears_is_wanted(self):
        player = _player(personal_reputation=-501, is_wanted=True, wanted_declared_at=FROZEN_NOW)
        db = _FakeReputationDb(player)
        svc = PersonalReputationService(db)

        svc.apply_weekly_decay(player.id)

        assert player.personal_reputation == -496
        assert player.is_wanted is False
