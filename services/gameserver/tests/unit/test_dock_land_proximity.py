"""Server-gated dock/land proximity (WO-ISP-DOCKPROX).

The player-client already hides the Dock/Land button beyond DOCK_RANGE_EM
(WindshieldTableau.tsx) -- these tests prove the SERVER now enforces the
same distance itself (assert_dock_land_proximity, the single call site both
/trading/dock and /planets/land use), so a direct REST call can no longer
bypass the client-only gate. Live-host REST proof was deliberately NOT used
for this WO (workers must never sync uncommitted code onto the live dev
host) -- this suite is the full substitute: it drives the SAME production
functions the routes call, DB-free, including a HTTPException-raising path
proven via pytest.raises exactly as it would fire out of the real route.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from src.services.intrasystem_layout import LAYOUT_BAND_REM_PX, SectorLayout
from src.services.intrasystem_movement_service import (
    DOCK_LAND_PROXIMITY_RANGE_EM,
    _pose_distance_px,
    assert_dock_land_proximity,
    current_player_pose_xy,
    is_within_dock_land_range,
    resolve_target_position,
    start_burn,
)


class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, *, sector=None, planets=None, stations=None, celestial_row=None):
        self._sector = sector
        self._planets = planets or []
        self._stations = stations or []
        self._celestial_row = celestial_row

    def query(self, model):
        name = getattr(model, "__name__", "")
        if name == "Sector":
            return _FakeQuery([self._sector] if self._sector is not None else [])
        if name == "Planet":
            return _FakeQuery(self._planets)
        if name == "Station":
            return _FakeQuery(self._stations)
        if name == "SectorCelestial":
            return _FakeQuery([self._celestial_row] if self._celestial_row is not None else [])
        return _FakeQuery([])


def _fake_sector(sector_id: int):
    return _Row(id=f"uuid-{sector_id}", sector_id=sector_id)


def _fake_planet(planet_id, *, habitability_score=90, kind="TERRAN"):
    return _Row(
        id=planet_id,
        habitability_score=habitability_score,
        position=3,
        type=_Row(value=kind, name=kind),
        display_name=f"Planet-{str(planet_id)[:8]}",
        discovered_by=None,
        owner_id=None,
    )


def _fake_station(station_id, *, name="Dock"):
    return _Row(id=station_id, name=name, type=_Row(value="TRADING_POST"), sector_id=None)


def _fake_player(*, sector_id: int, x_pct: float, y_pct: float, player_id="player-1"):
    return _Row(
        id=player_id,
        current_sector_id=sector_id,
        current_ship_id=None,
        intrasystem_pose={
            "x_pct": x_pct, "y_pct": y_pct, "heading_deg": 0.0,
            "phase": "idle", "burning": False, "leg": None,
        },
    )


# ---------------------------------------------------------------------------
# Pure math
# ---------------------------------------------------------------------------


def test_pose_distance_px_is_aspect_correct_not_a_naive_pct_diff():
    """%-of-width and %-of-height are NOT interchangeable on the wide-short
    band (1440x334.7) -- a 10%-y delta must read as a much SHORTER real
    distance than a 10%-x delta, not the same."""
    dx_only = _pose_distance_px(0.0, 0.0, 10.0, 0.0)
    dy_only = _pose_distance_px(0.0, 0.0, 0.0, 10.0)
    assert dx_only > dy_only
    assert dx_only == pytest.approx(144.0)  # 10% of 1440px
    assert dy_only == pytest.approx(33.47)  # 10% of 334.7px


def test_is_within_dock_land_range_boundary_is_at_the_threshold():
    """The cutoff sits at DOCK_LAND_PROXIMITY_RANGE_EM*remPx px, not some
    other value -- a point 0.5px inside it passes, 0.5px outside it fails
    (avoids asserting bit-exact equality AT the float boundary, which is
    fragile to round-trip noise; the meaningful claim is WHERE the cutoff
    is, not whether <= vs < is used at the exact float edge)."""
    threshold_px = DOCK_LAND_PROXIMITY_RANGE_EM * LAYOUT_BAND_REM_PX
    just_inside_pct = ((threshold_px - 0.5) / 1440.0) * 100.0
    just_outside_pct = ((threshold_px + 0.5) / 1440.0) * 100.0
    assert is_within_dock_land_range(0.0, 50.0, just_inside_pct, 50.0) is True
    assert is_within_dock_land_range(0.0, 50.0, just_outside_pct, 50.0) is False


def test_is_within_dock_land_range_far_and_near():
    assert is_within_dock_land_range(10.0, 20.0, 74.4368, 68.6415) is False
    assert is_within_dock_land_range(74.4368, 68.6415, 74.4368, 68.6415) is True


# ---------------------------------------------------------------------------
# current_player_pose_xy -- mid-flight interpolation awareness
# ---------------------------------------------------------------------------


def test_current_player_pose_xy_reads_idle_position():
    player = _fake_player(sector_id=1, x_pct=42.0, y_pct=17.0)
    assert current_player_pose_xy(player) == (42.0, 17.0)


def test_current_player_pose_xy_interpolates_mid_burn():
    """A player who initiated a burn TOWARD the target and is now mid-leg
    reads as progressively closer -- not stuck at their departure point."""
    idle = {
        "x_pct": 0.0, "y_pct": 50.0, "heading_deg": 0.0,
        "phase": "idle", "burning": False, "leg": None,
    }
    pose = start_burn(idle, to_x=100.0, to_y=50.0, sector_id=1, ship_key="p1")
    import datetime as _dt

    from src.services.intrasystem_movement_service import MOVE_MS, ORIENT_MS

    started = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(milliseconds=ORIENT_MS + MOVE_MS // 2)
    pose["leg"]["started_at"] = started.isoformat()
    player = _Row(id="p1", current_sector_id=1, current_ship_id=None, intrasystem_pose=pose)

    x, y = current_player_pose_xy(player)
    assert 0.0 < x < 100.0  # partway through the burn, not stuck at x=0
    assert abs(y - 50.0) < 0.5  # straight chord


# ---------------------------------------------------------------------------
# resolve_target_position -- reuses sector_destination_pools
# ---------------------------------------------------------------------------


def test_resolve_target_position_finds_station():
    sector_id = 50
    station_id = uuid.UUID(int=5001)
    session = _FakeSession(
        sector=_fake_sector(sector_id),
        stations=[_fake_station(station_id)],
        celestial_row=_Row(composition={"bodies": [], "stations": []}),
    )
    layout = SectorLayout(sector_id)
    got = resolve_target_position(session, sector_id, "k", "station", str(station_id))
    assert got is not None
    # Sanity: a real %-space point, not a placeholder.
    assert 0.0 <= got[0] <= 100.0 and 0.0 <= got[1] <= 100.0
    del layout  # unused beyond the sanity check above


def test_resolve_target_position_returns_none_for_unknown_target():
    sector_id = 51
    session = _FakeSession(
        sector=_fake_sector(sector_id),
        celestial_row=_Row(composition={"bodies": [], "stations": []}),
    )
    assert resolve_target_position(session, sector_id, "k", "station", "does-not-exist") is None


# ---------------------------------------------------------------------------
# assert_dock_land_proximity -- the ONE call site both routes use
# ---------------------------------------------------------------------------


def test_assert_dock_land_proximity_rejects_far_station():
    sector_id = 52
    station_id = uuid.UUID(int=5201)
    station = _fake_station(station_id, name="Alpha Dock")
    session = _FakeSession(
        sector=_fake_sector(sector_id),
        stations=[station],
        celestial_row=_Row(composition={"bodies": [], "stations": []}),
    )
    target_xy = resolve_target_position(session, sector_id, "peek", "station", str(station_id))
    far_x = 0.0 if target_xy[0] > 50 else 100.0  # guaranteed far on the x axis alone
    player = _fake_player(sector_id=sector_id, x_pct=far_x, y_pct=target_xy[1])

    with pytest.raises(HTTPException) as exc:
        assert_dock_land_proximity(
            session, player,
            sector_id=sector_id, target_kind="station", target_id=str(station_id),
            target_label=station.name, action_word="dock",
        )
    assert exc.value.status_code == 400
    assert "Alpha Dock" in exc.value.detail
    assert "dock" in exc.value.detail


def test_assert_dock_land_proximity_allows_near_station():
    sector_id = 53
    station_id = uuid.UUID(int=5301)
    station = _fake_station(station_id, name="Beta Dock")
    session = _FakeSession(
        sector=_fake_sector(sector_id),
        stations=[station],
        celestial_row=_Row(composition={"bodies": [], "stations": []}),
    )
    target_xy = resolve_target_position(session, sector_id, "peek", "station", str(station_id))
    player = _fake_player(sector_id=sector_id, x_pct=target_xy[0], y_pct=target_xy[1])

    # Exactly at the target's own position -- must not raise (pytest fails
    # this test on its own if it does).
    assert_dock_land_proximity(
        session, player,
        sector_id=sector_id, target_kind="station", target_id=str(station_id),
        target_label=station.name, action_word="dock",
    )


def test_assert_dock_land_proximity_rejects_far_planet():
    sector_id = 54
    planet_id = uuid.UUID(int=5401)
    planet = _fake_planet(planet_id, kind="TERRAN")
    celestial_row = _Row(composition={"bodies": [], "stations": []})
    session = _FakeSession(sector=_fake_sector(sector_id), planets=[planet], celestial_row=celestial_row)
    target_xy = resolve_target_position(session, sector_id, "peek", "planet", str(planet_id))
    far_x = 0.0 if target_xy[0] > 50 else 100.0
    player = _fake_player(sector_id=sector_id, x_pct=far_x, y_pct=target_xy[1])

    with pytest.raises(HTTPException) as exc:
        assert_dock_land_proximity(
            session, player,
            sector_id=sector_id, target_kind="planet", target_id=str(planet_id),
            target_label=f"Planet-{str(planet_id)[:8]}", action_word="land",
        )
    assert exc.value.status_code == 400
    assert "land" in exc.value.detail


def test_assert_dock_land_proximity_allows_near_planet():
    sector_id = 55
    planet_id = uuid.UUID(int=5501)
    planet = _fake_planet(planet_id, kind="TERRAN")
    celestial_row = _Row(composition={"bodies": [], "stations": []})
    session = _FakeSession(sector=_fake_sector(sector_id), planets=[planet], celestial_row=celestial_row)
    target_xy = resolve_target_position(session, sector_id, "peek", "planet", str(planet_id))
    player = _fake_player(sector_id=sector_id, x_pct=target_xy[0], y_pct=target_xy[1])

    assert_dock_land_proximity(
        session, player,
        sector_id=sector_id, target_kind="planet", target_id=str(planet_id),
        target_label="whatever", action_word="land",
    )


def test_assert_dock_land_proximity_fails_closed_when_target_unresolvable():
    """resolve_target_position returning None (target not found) must DENY,
    not silently allow -- a security-relevant gate fails closed."""
    sector_id = 56
    session = _FakeSession(
        sector=_fake_sector(sector_id),
        celestial_row=_Row(composition={"bodies": [], "stations": []}),
    )
    player = _fake_player(sector_id=sector_id, x_pct=50.0, y_pct=50.0)

    with pytest.raises(HTTPException) as exc:
        assert_dock_land_proximity(
            session, player,
            sector_id=sector_id, target_kind="station", target_id="ghost-id",
            target_label="Ghost Station", action_word="dock",
        )
    assert exc.value.status_code == 400
