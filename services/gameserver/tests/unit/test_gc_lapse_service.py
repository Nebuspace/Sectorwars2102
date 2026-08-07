"""ADR-0054 X-D3 -- GC-emergency-relocation one-time-per-cycle consumption
proof (src.services.gc_lapse_service.emergency_relocate)."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.services import gc_lapse_service as svc
from src.services.gc_lapse_service import GCLapseError


def _make_player(*, gc_lapsed_at, gc_relocation_used_at, home_region_id):
    return SimpleNamespace(
        id=uuid.uuid4(),
        gc_lapsed_at=gc_lapsed_at,
        gc_relocation_used_at=gc_relocation_used_at,
        home_region_id=home_region_id,
        current_sector_id=100,
        current_region_id=home_region_id,
        is_docked=True,
        is_landed=False,
        current_port_id="port-1",
        current_planet_id=None,
        current_ship=SimpleNamespace(id=uuid.uuid4(), sector_id=100),
    )


def _make_db(player, planet=None, station=None):
    db = MagicMock()

    def query(model):
        m = MagicMock()
        if model.__name__ == "Player":
            locked = m.filter.return_value.populate_existing.return_value.with_for_update.return_value
            locked.first.return_value = player
        elif model.__name__ == "Planet":
            m.filter.return_value.first.return_value = planet
        elif model.__name__ == "Station":
            m.filter.return_value.first.return_value = station
        return m

    db.query.side_effect = query
    return db


@patch("src.services.contraband_service.scan_in_transit_best_effort", return_value=None)
@patch("src.services.docking_service.release", return_value=True)
@patch("src.services.movement_service.MovementService")
def test_happy_path_consumes_the_grant_and_moves_the_player(mock_movement, mock_release, mock_scan):
    home_region = uuid.uuid4()
    foreign_region = uuid.uuid4()
    player = _make_player(gc_lapsed_at="2026-08-01", gc_relocation_used_at=None, home_region_id=home_region)
    planet = SimpleNamespace(id=uuid.uuid4(), owner_id=player.id, region_id=foreign_region, sector_id=500)
    db = _make_db(player, planet=planet)

    result = svc.emergency_relocate(db, player.id, "planet", planet.id)

    assert result["destination_sector_id"] == 500
    assert player.current_sector_id == 500
    assert player.current_region_id == foreign_region
    assert player.current_ship.sector_id == 500
    assert player.gc_relocation_used_at is not None
    mock_release.assert_called_once()
    mock_movement.return_value._update_player_presence.assert_called_once()
    # WO-K2b: this is still a border crossing (mirrors slipdrive/quantum) --
    # the emergency bypass must not skip the customs scan.
    mock_scan.assert_called_once_with(
        db,
        player=player,
        ship_id=player.current_ship.id,
        origin_sector_id=100,
        destination_sector_id=500,
    )


def test_not_lapsed_rejected():
    player = _make_player(gc_lapsed_at=None, gc_relocation_used_at=None, home_region_id=uuid.uuid4())
    db = _make_db(player)
    with pytest.raises(GCLapseError, match="not_lapsed"):
        svc.emergency_relocate(db, player.id, "planet", uuid.uuid4())


def test_already_used_this_cycle_rejected():
    player = _make_player(gc_lapsed_at="2026-08-01", gc_relocation_used_at="2026-08-02", home_region_id=uuid.uuid4())
    db = _make_db(player)
    with pytest.raises(GCLapseError, match="already_used"):
        svc.emergency_relocate(db, player.id, "planet", uuid.uuid4())


def test_not_owner_rejected():
    home_region = uuid.uuid4()
    foreign_region = uuid.uuid4()
    player = _make_player(gc_lapsed_at="2026-08-01", gc_relocation_used_at=None, home_region_id=home_region)
    other_player_id = uuid.uuid4()
    planet = SimpleNamespace(id=uuid.uuid4(), owner_id=other_player_id, region_id=foreign_region, sector_id=500)
    db = _make_db(player, planet=planet)
    with pytest.raises(GCLapseError, match="not_owner"):
        svc.emergency_relocate(db, player.id, "planet", planet.id)


def test_home_region_destination_rejected():
    """The bypass is for FOREIGN-region holdings only -- a home-region
    destination is not what this one-time grant is for."""
    home_region = uuid.uuid4()
    player = _make_player(gc_lapsed_at="2026-08-01", gc_relocation_used_at=None, home_region_id=home_region)
    planet = SimpleNamespace(id=uuid.uuid4(), owner_id=player.id, region_id=home_region, sector_id=500)
    db = _make_db(player, planet=planet)
    with pytest.raises(GCLapseError, match="not_foreign"):
        svc.emergency_relocate(db, player.id, "planet", planet.id)
