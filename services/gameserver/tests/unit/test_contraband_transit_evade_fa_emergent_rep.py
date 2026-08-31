"""LEG-3375 — emergent Fringe Alliance +10 on contraband transit scan evasion."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.core.illegal_commodities import ENABLED_COMMODITIES, cargo_key
from src.models.faction import FactionType
from src.services.contraband_service import (
    TRANSIT_SCAN_COOLDOWN_SECONDS,
    ContrabandService,
)
from src.services.emergent_reputation_service import EMERGENT_ACTIONS


def test_contraband_transit_evade_fa_registered() -> None:
    action = EMERGENT_ACTIONS["CONTRABAND_TRANSIT_EVADE_FA"]
    assert [(d.faction, d.delta) for d in action.deltas] == [
        (FactionType.OUTLAWS, 10)
    ]
    assert "evasion" in action.doc_source.lower()


def _player_and_ship():
    commodity = ENABLED_COMMODITIES[0]
    player = SimpleNamespace(
        id=uuid.uuid4(),
        personal_reputation=0,
        last_contraband_scan_at=None,
        last_contraband_scan_sector_id=None,
    )
    ship = SimpleNamespace(
        id=uuid.uuid4(),
        cargo={
            "contents": {cargo_key(commodity): 5},
            "capacity": 50,
            "used": 5,
        },
    )
    return player, ship


def _svc_with_ship(ship) -> tuple[ContrabandService, MagicMock]:
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = ship
    return ContrabandService(db), db


@pytest.mark.unit
class TestContrabandTransitEvadeFaEmergentRep:
    def test_clean_transit_dispatches_emergent_action_once(self, monkeypatch) -> None:
        player, ship = _player_and_ship()
        svc, _db = _svc_with_ship(ship)
        low = SimpleNamespace(security_level=3)
        high = SimpleNamespace(security_level=8)

        monkeypatch.setattr(
            svc,
            "_sector_by_number",
            lambda sector_id: low if sector_id == 1 else high,
        )
        monkeypatch.setattr(
            svc,
            "_lock_player_ship",
            lambda _pid, _sid: (player, ship, None),
        )
        monkeypatch.setattr(
            "src.services.contraband_service._RNG.random",
            lambda: 1.0,
        )

        with patch(
            "src.services.emergent_reputation_service.apply_emergent_action"
        ) as mock_apply:
            outcome = svc.scan_in_transit(
                player=player,
                ship_id=ship.id,
                origin_sector_id=1,
                destination_sector_id=2,
            )

        assert outcome["scanned"] is True
        assert outcome["detected"] is False
        mock_apply.assert_called_once_with(
            _db,
            player,
            "CONTRABAND_TRANSIT_EVADE_FA",
            {"sector_id": 2, "reason": "contraband_transit_scan"},
        )

    def test_busted_transit_does_not_dispatch_evade_action(self, monkeypatch) -> None:
        player, ship = _player_and_ship()
        svc, _db = _svc_with_ship(ship)
        low = SimpleNamespace(security_level=3)
        high = SimpleNamespace(security_level=8)

        monkeypatch.setattr(
            svc,
            "_sector_by_number",
            lambda sector_id: low if sector_id == 1 else high,
        )
        monkeypatch.setattr(
            svc,
            "_lock_player_ship",
            lambda _pid, _sid: (player, ship, None),
        )
        monkeypatch.setattr(
            "src.services.contraband_service._RNG.random",
            lambda: 0.0,
        )
        monkeypatch.setattr(
            svc,
            "_resolve_bust",
            lambda **kw: {
                "detected": True,
                "commodity": "WEAPONS",
                "fine": 100,
            },
        )

        with patch(
            "src.services.emergent_reputation_service.apply_emergent_action"
        ) as mock_apply:
            outcome = svc.scan_in_transit(
                player=player,
                ship_id=ship.id,
                origin_sector_id=1,
                destination_sector_id=2,
            )

        assert outcome["detected"] is True
        mock_apply.assert_not_called()

    def test_cooldown_skip_does_not_dispatch_evade_action(self, monkeypatch) -> None:
        player, ship = _player_and_ship()
        now = datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC)
        player.last_contraband_scan_at = now - timedelta(
            seconds=TRANSIT_SCAN_COOLDOWN_SECONDS // 2
        )
        player.last_contraband_scan_sector_id = 2

        svc, _db = _svc_with_ship(ship)
        low = SimpleNamespace(security_level=3)
        high = SimpleNamespace(security_level=8)

        monkeypatch.setattr(
            svc,
            "_sector_by_number",
            lambda sector_id: low if sector_id == 1 else high,
        )
        monkeypatch.setattr(
            "src.services.contraband_service.datetime",
            MagicMock(now=MagicMock(return_value=now)),
        )

        with patch(
            "src.services.emergent_reputation_service.apply_emergent_action"
        ) as mock_apply:
            outcome = svc.scan_in_transit(
                player=player,
                ship_id=ship.id,
                origin_sector_id=1,
                destination_sector_id=2,
            )

        assert outcome["scanned"] is False
        assert outcome["reason"] == "cooldown"
        mock_apply.assert_not_called()
