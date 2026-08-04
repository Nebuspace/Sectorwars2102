"""Unit tests for WO-BEACON-REPORT-MODERATION (pure / lightly mocked)."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.services import message_beacon_service as mbs
from src.services.message_beacon_service import BeaconNotFoundError


def _beacon(**kwargs):
    defaults = dict(
        id=uuid4(),
        region_id=uuid4(),
        sector_id=7,
        flagged=False,
        deployer_player_id=uuid4(),
        deployer_nickname_at_deploy="Pilot",
        message="hello space",
        deployed_at=None,
        expiry=None,
        charge_expires_at=None,
        read_once=False,
        read_count=0,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestRebuildExcludesFlagged:
    def test_flagged_omitted_from_visible(self):
        ok = _beacon(flagged=False, message="ok")
        bad = _beacon(flagged=True, message="bad")
        sector = SimpleNamespace(message_beacons=None)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = sector
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            ok, bad,
        ]
        with patch.object(mbs, "flag_modified", lambda *a, **k: None):
            with patch.object(mbs, "_participation_weight", return_value=1.0):
                with patch.object(mbs, "_decay_state", return_value="ACTIVE"):
                    with patch.object(mbs, "_beacon_summary", side_effect=lambda b, now=None: {"id": str(b.id)}):
                        mbs._rebuild_sector_denorm(db, ok.region_id, ok.sector_id)
        assert len(sector.message_beacons) == 1
        assert sector.message_beacons[0]["id"] == str(ok.id)


class TestReport:
    def test_wrong_sector_is_not_found(self):
        beacon = _beacon(sector_id=1)
        player = SimpleNamespace(id=uuid4(), current_sector_id=99)
        db = MagicMock()
        with patch.object(mbs, "_load_beacon", return_value=beacon):
            db.query.return_value.filter.return_value.first.return_value = player
            with pytest.raises(BeaconNotFoundError):
                mbs.report(db, beacon.id, player.id)

    def test_sets_flagged_and_rebuilds(self):
        beacon = _beacon(sector_id=5, flagged=False)
        player = SimpleNamespace(id=uuid4(), current_sector_id=5)
        db = MagicMock()
        with patch.object(mbs, "_load_beacon", return_value=beacon):
            with patch.object(mbs, "_lock_sector"):
                with patch.object(mbs, "_rebuild_sector_denorm") as rebuild:
                    db.query.return_value.filter.return_value.first.return_value = player
                    result = mbs.report(db, beacon.id, player.id)
        assert beacon.flagged is True
        assert result["flagged"] is True
        assert result["already_flagged"] is False
        rebuild.assert_called_once()


class TestReadHidesFlagged:
    def test_flagged_read_raises_not_found(self):
        beacon = _beacon(sector_id=3, flagged=True)
        player = SimpleNamespace(id=uuid4(), current_sector_id=3)
        db = MagicMock()
        with patch.object(mbs, "_load_beacon", return_value=beacon):
            db.query.return_value.filter.return_value.first.return_value = player
            with pytest.raises(BeaconNotFoundError):
                mbs.read(db, beacon.id, player.id)


class TestClearFlag:
    def test_clears_and_rebuilds(self):
        beacon = _beacon(flagged=True)
        db = MagicMock()
        with patch.object(mbs, "_load_beacon", return_value=beacon):
            with patch.object(mbs, "_lock_sector"):
                with patch.object(mbs, "_rebuild_sector_denorm") as rebuild:
                    result = mbs.clear_flag(db, beacon.id)
        assert beacon.flagged is False
        assert result["flagged"] is False
        assert result["already_cleared"] is False
        rebuild.assert_called_once()

    def test_idempotent_when_already_clear(self):
        beacon = _beacon(flagged=False)
        db = MagicMock()
        with patch.object(mbs, "_load_beacon", return_value=beacon):
            with patch.object(mbs, "_lock_sector"):
                with patch.object(mbs, "_rebuild_sector_denorm") as rebuild:
                    result = mbs.clear_flag(db, beacon.id)
        assert result["already_cleared"] is True
        rebuild.assert_not_called()
