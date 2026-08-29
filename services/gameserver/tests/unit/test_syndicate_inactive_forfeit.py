"""LEG-2015: 90-day inactive syndicate stake forfeit (port-ownership.md:455)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.services import port_ownership_service as po
from src.services.station_governance_service import (
    INACTIVE_DAYS,
    INACTIVE_FORFEIT_DAYS,
    counted_stake,
    player_forfeit_eligible,
)

UTC = timezone.utc
FIXED_NOW = datetime(2102, 6, 1, 12, 0, 0, tzinfo=UTC)


def _player(last_login: datetime | None):
    return SimpleNamespace(
        id=uuid4(),
        last_game_login=last_login,
        last_activity_at=None,
    )


def _syndicate_station(primary_id, others, faction="Mercantile Guild"):
    shares = [{"player_id": str(primary_id), "pct": 100 - sum(o[1] for o in others)}]
    for oid, pct in others:
        shares.append({"player_id": str(oid), "pct": pct})
    return SimpleNamespace(
        id=uuid4(),
        owner_id=primary_id,
        faction_affiliation=faction,
        ownership={
            po.SYNDICATE_MODE_KEY: "syndicate",
            po.SYNDICATE_SHARES_KEY: shares,
        },
    )


class TestInactiveGovernanceThresholds:
    def test_30d_halves_vote_weight_not_forfeit(self):
        login = FIXED_NOW - timedelta(days=INACTIVE_DAYS)
        player = _player(login)
        assert player_forfeit_eligible(player, FIXED_NOW) is False
        assert counted_stake(40, inactive=True) == 20.0

    def test_90d_forfeit_eligible(self):
        login = FIXED_NOW - timedelta(days=INACTIVE_FORFEIT_DAYS)
        player = _player(login)
        assert player_forfeit_eligible(player, FIXED_NOW) is True


class TestForfeitInactiveSyndicateStakes:
    def test_90d_inactive_coowner_forfeited_and_rebalanced(self):
        primary = uuid4()
        inactive = uuid4()
        active = uuid4()
        station = _syndicate_station(primary, [(inactive, 30), (active, 20)])
        inactive_player = _player(FIXED_NOW - timedelta(days=INACTIVE_FORFEIT_DAYS))
        inactive_player.id = inactive
        active_player = _player(FIXED_NOW - timedelta(days=5))
        active_player.id = active
        primary_player = _player(FIXED_NOW - timedelta(days=5))
        primary_player.id = primary

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [
            primary_player,
            inactive_player,
            active_player,
        ]

        with patch.object(po, "_lock_station", return_value=station):
            result = po.forfeit_inactive_syndicate_stakes(db, station, FIXED_NOW)

        assert result["forfeited"] == 30
        assert result["controlling_faction"] == "Mercantile Guild"
        shares = station.ownership[po.SYNDICATE_SHARES_KEY]
        by_id = {s["player_id"]: s["pct"] for s in shares}
        assert str(inactive) not in by_id
        assert by_id[str(primary)] == 72
        assert by_id[str(active)] == 28
        assert sum(by_id.values()) == 100

    def test_solo_unchanged(self):
        primary = uuid4()
        station = SimpleNamespace(
            id=uuid4(),
            owner_id=primary,
            faction_affiliation="Mercantile Guild",
            ownership={po.SYNDICATE_MODE_KEY: "solo"},
        )
        db = MagicMock()
        result = po.forfeit_inactive_syndicate_stakes(db, station, FIXED_NOW)
        assert result["forfeited"] == 0

    def test_30d_inactive_not_forfeited(self):
        primary = uuid4()
        inactive = uuid4()
        station = _syndicate_station(primary, [(inactive, 40)])
        inactive_player = _player(FIXED_NOW - timedelta(days=INACTIVE_DAYS))
        inactive_player.id = inactive
        primary_player = _player(FIXED_NOW - timedelta(days=5))
        primary_player.id = primary

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [
            primary_player,
            inactive_player,
        ]

        with patch.object(po, "_lock_station", return_value=station):
            result = po.forfeit_inactive_syndicate_stakes(db, station, FIXED_NOW)

        assert result["forfeited"] == 0
        shares = station.ownership[po.SYNDICATE_SHARES_KEY]
        assert len(shares) == 2
