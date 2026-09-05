"""Unit tests for pirate_holding_raid_service.initiate_raid (LEG-1105)."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from src.models.pirate_holding import PirateHoldingTier
from src.services import pirate_holding_raid_service as phrs
from src.services.pirate_holding_raid_service import PirateHoldingRaidError


class _FakeQuery:
    def __init__(self, holding):
        self._holding = holding

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._holding


class _FakeSession:
    def __init__(self, holding):
        self._holding = holding
        self.flushed = False

    def query(self, model):
        return _FakeQuery(self._holding)

    def flush(self):
        self.flushed = True


def _holding(**kwargs):
    defaults = {
        "id": uuid.uuid4(),
        "sector_id": 42,
        "tier": PirateHoldingTier.OUTPOST,
        "owner_player_id": None,
        "combat_lock_held_by": None,
        "combat_lock_team_snapshot": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _player(*, sector_id=42, player_id=None, team=None):
    return SimpleNamespace(
        id=player_id or uuid.uuid4(),
        current_sector_id=sector_id,
        team=team,
    )


def _team(member_ids):
    members = [SimpleNamespace(id=mid) for mid in member_ids]
    return SimpleNamespace(members=members)


class TestInitiateRaid:
    def test_missing_holding_404(self):
        db = _FakeSession(None)
        with pytest.raises(PirateHoldingRaidError) as exc:
            phrs.initiate_raid(db, uuid.uuid4(), _player())
        assert exc.value.status_code == 404
        assert exc.value.detail == "Pirate holding not found"

    def test_already_captured_400(self):
        holding = _holding(owner_player_id=uuid.uuid4())
        db = _FakeSession(holding)
        with pytest.raises(PirateHoldingRaidError) as exc:
            phrs.initiate_raid(db, holding.id, _player())
        assert exc.value.status_code == 400
        assert exc.value.detail == "Holding is already captured"

    def test_wrong_sector_403(self):
        holding = _holding(sector_id=99)
        db = _FakeSession(holding)
        with pytest.raises(PirateHoldingRaidError) as exc:
            phrs.initiate_raid(db, holding.id, _player(sector_id=42))
        assert exc.value.status_code == 403
        assert exc.value.detail == (
            "Player must be in the holding anchor sector to initiate a raid"
        )

    def test_camp_skips_lock(self):
        holding = _holding(tier=PirateHoldingTier.CAMP)
        db = _FakeSession(holding)
        result = phrs.initiate_raid(db, holding.id, _player())
        assert result["initiated"] is True
        assert result["lock_applied"] is False
        assert holding.combat_lock_held_by is None
        assert db.flushed is False

    def test_outpost_acquires_lock(self):
        holding = _holding(tier=PirateHoldingTier.OUTPOST)
        db = _FakeSession(holding)
        player_id = uuid.uuid4()
        mate_id = uuid.uuid4()
        player = _player(
            sector_id=holding.sector_id,
            player_id=player_id,
            team=_team([player_id, mate_id]),
        )

        result = phrs.initiate_raid(db, holding.id, player)

        assert result["lock_applied"] is True
        assert holding.combat_lock_held_by == player_id
        assert set(holding.combat_lock_team_snapshot) == {player_id, mate_id}
        assert db.flushed is True

    def test_locked_by_other_attacker_409(self):
        holder_id = uuid.uuid4()
        holding = _holding(
            tier=PirateHoldingTier.STRONGHOLD,
            combat_lock_held_by=holder_id,
            combat_lock_team_snapshot=[holder_id],
        )
        db = _FakeSession(holding)
        with pytest.raises(PirateHoldingRaidError) as exc:
            phrs.initiate_raid(db, holding.id, _player(sector_id=holding.sector_id))
        assert exc.value.status_code == 409
        assert exc.value.detail == "Holding is locked by another attacker"

    def test_late_join_teammate_not_in_snapshot_409(self):
        holder_id = uuid.uuid4()
        teammate_id = uuid.uuid4()
        late_join_id = uuid.uuid4()
        holding = _holding(
            tier=PirateHoldingTier.OUTPOST,
            combat_lock_held_by=holder_id,
            combat_lock_team_snapshot=[holder_id, teammate_id],
        )
        db = _FakeSession(holding)
        late_join = _player(
            sector_id=holding.sector_id,
            player_id=late_join_id,
            team=_team([holder_id, teammate_id, late_join_id]),
        )
        with pytest.raises(PirateHoldingRaidError) as exc:
            phrs.initiate_raid(db, holding.id, late_join)
        assert exc.value.status_code == 409
        assert exc.value.detail == "Holding is locked by another attacker"
