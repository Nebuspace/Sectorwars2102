"""LEG-3195: station takeover volume discounted by participation_weight."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from src.services import port_ownership_service as po


class _ChainQuery:
    """Minimal query stub for _weighted_transaction_volume tests."""

    def __init__(self, *, scalar_total=None, rows=None) -> None:
        self._scalar_total = scalar_total
        self._rows = rows or []

    def filter(self, *args, **kwargs) -> "_ChainQuery":
        return self

    def with_entities(self, *entities) -> "_ChainQuery":
        return self

    def scalar(self):
        return self._scalar_total

    def all(self):
        return self._rows


@pytest.mark.unit
class TestTakeoverVolumeParticipationWeight:
    def test_hard_flagged_player_volume_zero(self, monkeypatch) -> None:
        monkeypatch.setattr(po, "participation_weight", lambda db, pid: 0.0)
        pid = uuid.uuid4()
        q = _ChainQuery(scalar_total=50_000)
        assert po._weighted_transaction_volume(MagicMock(), q, player_id=pid) == 0

    def test_soft_flagged_player_volume_half(self, monkeypatch) -> None:
        monkeypatch.setattr(po, "participation_weight", lambda db, pid: 0.5)
        pid = uuid.uuid4()
        q = _ChainQuery(scalar_total=10_000)
        assert po._weighted_transaction_volume(MagicMock(), q, player_id=pid) == 5_000

    def test_clean_player_volume_unchanged(self, monkeypatch) -> None:
        monkeypatch.setattr(po, "participation_weight", lambda db, pid: 1.0)
        pid = uuid.uuid4()
        q = _ChainQuery(scalar_total=10_000)
        assert po._weighted_transaction_volume(MagicMock(), q, player_id=pid) == 10_000

    def test_aggregate_volume_weights_per_player(self, monkeypatch) -> None:
        p_clean = uuid.uuid4()
        p_soft = uuid.uuid4()

        def _pw(db, pid):
            return 0.5 if pid == p_soft else 1.0

        monkeypatch.setattr(po, "participation_weight", _pw)
        q = _ChainQuery(rows=[(p_clean, 1000), (p_soft, 10_000)])
        assert po._weighted_transaction_volume(MagicMock(), q) == 6_000

    def test_soft_needs_double_raw_for_same_effective_share(self, monkeypatch) -> None:
        """SOFT 2× raw volume matches clean 1× for share math."""
        soft_id = uuid.uuid4()
        clean_id = uuid.uuid4()
        weights = {soft_id: 0.5, clean_id: 1.0}

        monkeypatch.setattr(
            po,
            "participation_weight",
            lambda db, pid: weights[pid],
        )
        soft_vol = po._weighted_transaction_volume(
            MagicMock(), _ChainQuery(scalar_total=20_000), player_id=soft_id
        )
        clean_vol = po._weighted_transaction_volume(
            MagicMock(), _ChainQuery(scalar_total=10_000), player_id=clean_id
        )
        assert soft_vol == clean_vol == 10_000

    def test_hard_cannot_reach_majority_share_via_self_trade(self, monkeypatch) -> None:
        monkeypatch.setattr(po, "participation_weight", lambda db, pid: 0.0)
        pid = uuid.uuid4()
        raw = po._weighted_transaction_volume(
            MagicMock(), _ChainQuery(scalar_total=100_000), player_id=pid
        )
        station_total = raw  # only trader at station
        share = po.month_share_with_defense(station_total, raw, 0)
        assert raw == 0
        assert share == 0.0
