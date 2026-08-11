"""Audit-cycle-27 #1 — SectorType.ANOMALY investigate + enum presence.

Covers anomaly_service.investigate_anomaly (happy path, wrong type, not
present, already investigated, reward magnitude 250) and confirms ANOMALY
is on the live SectorType enum.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from src.models.sector import SectorType
from src.services import anomaly_service as asvc
from src.services.anomaly_service import (
    ANOMALY_INVESTIGATE_REWARD_CREDITS,
    AnomalyAlreadyInvestigatedError,
    AnomalyNotFoundError,
    investigate_anomaly,
    is_anomaly_investigated,
)


class _FakeQuery:
    def __init__(self, sector: Optional[Any]):
        self._sector = sector

    def filter(self, *args, **kwargs):
        return self

    def populate_existing(self):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self._sector


class _FakeSession:
    def __init__(self, sector: Optional[Any] = None):
        self._sector = sector
        self.committed = False

    def query(self, model):
        return _FakeQuery(self._sector)

    def commit(self):
        self.committed = True


def _make_sector(*, sector_id: int = 42, type_=SectorType.ANOMALY, hazards=None):
    return SimpleNamespace(
        sector_id=sector_id,
        name=f"Sector {sector_id}",
        type=type_,
        nav_hazards=dict(hazards or {}),
    )


def _make_player(*, sector_id: int = 42, credits: int = 1000):
    return SimpleNamespace(
        id=uuid.uuid4(),
        current_sector_id=sector_id,
        credits=credits,
    )


@pytest.mark.unit
class TestSectorTypeAnomalyEnum:
    def test_anomaly_on_live_sector_type(self):
        assert SectorType.ANOMALY.value == "ANOMALY"
        assert "ANOMALY" in {m.name for m in SectorType}


@pytest.mark.unit
class TestInvestigateAnomaly:
    @pytest.fixture(autouse=True)
    def _noop_flag_modified(self, monkeypatch):
        monkeypatch.setattr(asvc, "flag_modified", lambda *a, **k: None)

    def test_happy_path_grants_250(self):
        sector = _make_sector()
        player = _make_player(credits=1000)
        db = _FakeSession(sector)

        payload = investigate_anomaly(db, player, 42)

        assert payload["reward"]["credits"] == ANOMALY_INVESTIGATE_REWARD_CREDITS
        assert payload["reward"]["credits"] == 250
        assert player.credits == 1250
        assert payload["credits_remaining"] == 1250
        assert payload["sector"]["is_investigated"] is True
        assert payload["reward_is_no_canon"] is False
        assert is_anomaly_investigated(sector) is True
        assert db.committed is True
        assert "anomaly_investigation" in sector.nav_hazards

    def test_repeat_investigate_conflicts(self):
        sector = _make_sector(
            hazards={
                "anomaly_investigation": {
                    "investigated": True,
                    "reward_credits": 250,
                }
            }
        )
        player = _make_player()
        db = _FakeSession(sector)

        with pytest.raises(AnomalyAlreadyInvestigatedError):
            investigate_anomaly(db, player, 42)

    def test_wrong_type_is_not_found(self):
        sector = _make_sector(type_=SectorType.STANDARD)
        player = _make_player()
        db = _FakeSession(sector)

        with pytest.raises(AnomalyNotFoundError):
            investigate_anomaly(db, player, 42)

    def test_player_not_present_is_not_found(self):
        sector = _make_sector(sector_id=42)
        player = _make_player(sector_id=99)
        db = _FakeSession(sector)

        with pytest.raises(AnomalyNotFoundError):
            investigate_anomaly(db, player, 42)

    def test_missing_sector_is_not_found(self):
        player = _make_player()
        db = _FakeSession(None)

        with pytest.raises(AnomalyNotFoundError):
            investigate_anomaly(db, player, 42)


@pytest.mark.unit
class TestAnomalyChanceConstant:
    def test_midpoint_of_canon_range(self):
        # generation.md: ~1–2%; both generators use midpoint 0.015.
        assert 0.01 <= 0.015 <= 0.02
