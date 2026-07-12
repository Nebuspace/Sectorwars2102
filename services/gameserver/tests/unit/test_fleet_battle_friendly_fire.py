"""WO-COMBAT-FRIENDLY-FIRE sub-part (b) — ``FleetService.initiate_battle`` had
NO team check: two fleets on the SAME team could battle each other. This file
pins the guard added at ``fleet_service.py`` (immediately after the not-found
check, before the in-battle-status / sector / supply checks):

    if attacker.team_id is not None and attacker.team_id == defender.team_id:
        raise ValueError("Friendly-fire prevention: fleets on the same team cannot battle")

Placement: friendly-fire is a fundamental "not allowed at all" — it precedes
the situational status/sector/supply rejects and becomes the primary reason
returned when a same-team pair would otherwise ALSO fail one of those (see
``test_friendly_fire_takes_priority_over_in_battle_status`` below).

``Fleet.team_id`` is ``nullable=False`` at the model level
(``src/models/fleet.py:55``) — a fleet can never actually carry
``team_id=None`` in production. The ``is not None`` clause is a defensive,
None-safe mirror kept for symmetry regardless; ``TestNoneSafeBranch`` below
exercises it directly against hand-built stand-ins (not real, persisted
Fleet rows) to confirm it degrades sanely rather than crashing or
misfiring, since production can never exercise it organically.

No home among the four existing fleet-service test files fits: each is
scoped to a distinct prior WO (test_fleet_casualty_succession.py,
test_fleet_kill_lock_order.py, test_fleet_coordination_combat.py,
test_fleet_kill_lock_cluster_mack.py) — grepping ``initiate_battle`` across
tests/ turns up only test_fleets_route_dep_swap_mack.py, which is a
route-level async/sync dependency-injection wiring suite (a different WO,
DEFECT-fleet-battle-asyncsession) with no coverage of this service-level
business rule at all. This file follows the same established
one-topic-per-file convention as its four siblings instead of conflating
two unrelated WOs' scope into one file.

DB-free: a ``MagicMock`` db whose ``Fleet`` query resolves BY THE FILTERED
ID (not call order) — WO-FLEET-BATTLE-LOCKS changed ``initiate_battle`` to
lock both fleets via ``_lock_fleets_ascending``, which queries
``Fleet.id == fid`` in ASCENDING-id order (not attacker-then-defender call
order — the two random UUIDs sort either way), chained through
``.populate_existing().with_for_update().first()`` rather than a bare
``.filter().first()``. ``make_db`` below extracts the literal id off each
filter condition (mirrors test_fleet_kill_lock_order.py's ``_FakeQuery``
idiom) so it resolves correctly regardless of acquisition order.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.models.fleet import BattlePhase, FleetBattle, FleetStatus
from src.services.fleet_service import FleetService

FRIENDLY_FIRE_MSG = "Friendly-fire prevention: fleets on the same team cannot battle"


def make_fleet(*, team_id, status=FleetStatus.READY.value, sector_id=None, supply_level=100):
    return SimpleNamespace(
        id=uuid4(),
        team_id=team_id,
        status=status,
        sector_id=sector_id if sector_id is not None else uuid4(),
        supply_level=supply_level,
        total_ships=3,
    )


def make_db(attacker, defender):
    """MagicMock db resolving Fleet queries BY ID (WO-FLEET-BATTLE-LOCKS'
    ``_lock_fleets_ascending`` locks ascending-by-id, not attacker-then-
    defender call order) -- every ``Fleet.id == fid`` filter is answered
    from a small id-keyed dict regardless of which fleet's lock is
    acquired first."""
    fleets_by_id = {attacker.id: attacker, defender.id: defender}

    def _query(model, *a, **k):
        q = MagicMock()

        def _filter(cond, *args, **kwargs):
            fid = getattr(getattr(cond, "right", None), "value", None)
            locked = MagicMock()
            locked.populate_existing.return_value.with_for_update.return_value.first.side_effect = (
                lambda: fleets_by_id.get(fid)
            )
            return locked

        q.filter.side_effect = _filter
        return q

    db = MagicMock()
    db.query.side_effect = _query
    return db


# --------------------------------------------------------------------------- #
# (1) Same-team pair -> ValueError, zero state change.
# --------------------------------------------------------------------------- #

class TestSameTeamBlocked:
    def test_same_team_raises_friendly_fire_value_error(self):
        team_id = uuid4()
        sector_id = uuid4()
        attacker = make_fleet(team_id=team_id, sector_id=sector_id)
        defender = make_fleet(team_id=team_id, sector_id=sector_id)
        db = make_db(attacker, defender)

        with pytest.raises(ValueError, match=FRIENDLY_FIRE_MSG):
            FleetService(db).initiate_battle(attacker.id, defender.id)

    def test_same_team_creates_no_battle_row_and_leaves_statuses_unchanged(self):
        """Delta check scoped to THIS call's fixtures — never a global count
        (gameserver-tests-fixture-scoped-assertions convention)."""
        team_id = uuid4()
        sector_id = uuid4()
        attacker = make_fleet(team_id=team_id, sector_id=sector_id, status=FleetStatus.READY.value)
        defender = make_fleet(team_id=team_id, sector_id=sector_id, status=FleetStatus.READY.value)
        db = make_db(attacker, defender)

        with pytest.raises(ValueError):
            FleetService(db).initiate_battle(attacker.id, defender.id)

        db.add.assert_not_called()
        db.commit.assert_not_called()
        assert attacker.status == FleetStatus.READY.value
        assert defender.status == FleetStatus.READY.value

    def test_friendly_fire_takes_priority_over_in_battle_status(self):
        """Same-team pair that would ALSO fail the in-battle-status check
        must surface the friendly-fire reason, not the status reason --
        pins the guard's placement (before :638-642)."""
        team_id = uuid4()
        sector_id = uuid4()
        attacker = make_fleet(
            team_id=team_id, sector_id=sector_id, status=FleetStatus.IN_BATTLE.value,
        )
        defender = make_fleet(
            team_id=team_id, sector_id=sector_id, status=FleetStatus.IN_BATTLE.value,
        )
        db = make_db(attacker, defender)

        with pytest.raises(ValueError, match=FRIENDLY_FIRE_MSG):
            FleetService(db).initiate_battle(attacker.id, defender.id)


# --------------------------------------------------------------------------- #
# (2) Cross-team pair -> proceeds normally (existing behavior untouched).
# --------------------------------------------------------------------------- #

class TestCrossTeamProceedsNormally:
    def test_cross_team_creates_battle_and_flips_both_fleets_in_battle(self):
        sector_id = uuid4()
        attacker = make_fleet(team_id=uuid4(), sector_id=sector_id)
        defender = make_fleet(team_id=uuid4(), sector_id=sector_id)
        db = make_db(attacker, defender)

        battle = FleetService(db).initiate_battle(attacker.id, defender.id)

        assert isinstance(battle, FleetBattle)
        assert battle.attacker_fleet_id == attacker.id
        assert battle.defender_fleet_id == defender.id
        assert battle.phase == BattlePhase.ENGAGEMENT.value  # preparation phase already advanced it
        assert attacker.status == FleetStatus.IN_BATTLE.value
        assert defender.status == FleetStatus.IN_BATTLE.value
        db.add.assert_called_once_with(battle)
        assert db.commit.call_count >= 1


# --------------------------------------------------------------------------- #
# (3) None-safe branch — Fleet.team_id is NOT NULL by construction
# (src/models/fleet.py:55), so this cannot occur via real, persisted Fleet
# rows. Exercised defensively against hand-built stand-ins to confirm the
# `is not None` guard degrades sanely rather than crashing or over-blocking.
# --------------------------------------------------------------------------- #

class TestNoneSafeBranch:
    def test_both_teamless_does_not_raise_friendly_fire(self):
        sector_id = uuid4()
        attacker = make_fleet(team_id=None, sector_id=sector_id)
        defender = make_fleet(team_id=None, sector_id=sector_id)
        db = make_db(attacker, defender)

        battle = FleetService(db).initiate_battle(attacker.id, defender.id)

        assert isinstance(battle, FleetBattle)
        assert attacker.status == FleetStatus.IN_BATTLE.value
        assert defender.status == FleetStatus.IN_BATTLE.value

    def test_attacker_teamless_defender_teamed_does_not_raise_friendly_fire(self):
        sector_id = uuid4()
        attacker = make_fleet(team_id=None, sector_id=sector_id)
        defender = make_fleet(team_id=uuid4(), sector_id=sector_id)
        db = make_db(attacker, defender)

        battle = FleetService(db).initiate_battle(attacker.id, defender.id)

        assert isinstance(battle, FleetBattle)
