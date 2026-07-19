"""WO-CLAIM-PROXIMITY: planet claim gets the SAME server-side proximity gate
dock/land already enforce (Max, 2026-07-17: "a claim that lands is a
landing" -- claim_planet auto-lands the player on success).

test_dock_land_proximity.py already proves assert_dock_land_proximity /
resolve_target_position / is_within_dock_land_range in isolation -- that is
NOT re-proven here. This file proves the WIRING instead: that the
/planets/{id}/claim ROUTE (src.api.routes.planets.claim_planet) actually
calls the gate, by invoking the real production coroutine directly (no
HTTP, no Depends -- planet_id/player/db passed as plain kwargs, exactly how
FastAPI would resolve them) and asserting a far pose is rejected BEFORE any
of the claim route's own credits/colonists/ownership logic ever runs, while
a near pose passes the gate and falls through to the route's NEXT check.

DB fake: the same duck-typed _Row/_FakeSession/_FakeQuery shape
test_dock_land_proximity.py already uses and proves sufficient for
resolve_target_position's celestial_service.generate_system consumption
surface (single-planet/single-sector scenarios, so the FakeQuery's
condition-blind `filter()` -- it always returns the full backing list,
never actually evaluates the SQLAlchemy expression -- cannot mask a
wiring bug here: there is nothing else in any list to wrongly match). What
this DOES prove that a helper-level test cannot: that claim_planet's OWN
code invokes assert_dock_land_proximity, not a reimplementation of it in
the test. Genuine two-connection lock/identity-map races are out of scope
for this gate (no such property is under test) -- that's the real-Postgres
follow-up the rest of this WO-family already defers to, matching this
file's own sibling.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import HTTPException

from src.api.routes.planets import claim_planet
from src.services.intrasystem_layout import LAYOUT_BAND_REM_PX
from src.services.intrasystem_movement_service import (
    DOCK_LAND_PROXIMITY_RANGE_EM,
    resolve_target_position,
)


class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self, *args, **kwargs):
        return self

    def populate_existing(self):
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


def _fake_planet(planet_id, *, sector_id: int, name: str, owner_id=None, habitability_score=90):
    return _Row(
        id=planet_id,
        sector_id=sector_id,
        name=name,
        owner_id=owner_id,
        habitability_score=habitability_score,
        position=3,
        type=_Row(value="TERRAN", name="TERRAN"),
        display_name=name,
        discovered_by=None,
    )


def _fake_player(*, sector_id: int, x_pct: float, y_pct: float, player_id="claimant-1"):
    return _Row(
        id=player_id,
        current_sector_id=sector_id,
        current_ship_id=None,
        is_docked=False,
        is_landed=False,
        intrasystem_pose={
            "x_pct": x_pct, "y_pct": y_pct, "heading_deg": 0.0,
            "phase": "idle", "burning": False, "leg": None,
        },
    )


def _run(coro):
    return asyncio.run(coro)


def _target_xy(session, sector_id, planet_id):
    got = resolve_target_position(session, sector_id, "peek", "planet", str(planet_id))
    assert got is not None, "test setup: fake planet must resolve to a position"
    return got


# ---------------------------------------------------------------------------
# Far pose: the route itself must reject BEFORE reaching the "already
# owned" check below the new gate -- proves the gate fires from inside
# claim_planet, not that it merely exists somewhere in the module.
# ---------------------------------------------------------------------------


def test_claim_planet_rejects_far_pose():
    sector_id = 601
    planet_id = uuid.uuid4()
    planet = _fake_planet(planet_id, sector_id=sector_id, name="Farhaven")
    session = _FakeSession(
        sector=_fake_sector(sector_id),
        planets=[planet],
        celestial_row=_Row(composition={"bodies": [], "stations": []}),
    )
    target_x, target_y = _target_xy(session, sector_id, planet_id)
    far_x = 0.0 if target_x > 50 else 100.0  # guaranteed far on the x axis alone
    player = _fake_player(sector_id=sector_id, x_pct=far_x, y_pct=target_y)

    with pytest.raises(HTTPException) as exc:
        _run(claim_planet(planet_id=str(planet_id), player=player, db=session))

    assert exc.value.status_code == 400
    assert "too far" in exc.value.detail
    assert "claim" in exc.value.detail
    # Proves the route stopped at the NEW gate, not somewhere else: an
    # already-owned planet would otherwise raise a DIFFERENT message.
    assert "already claimed" not in exc.value.detail


# ---------------------------------------------------------------------------
# Near pose: the gate must NOT fire -- proven by observing the route fall
# through to its own NEXT check (already-owned), a message the proximity
# gate itself never produces. A full happy-path claim (credits/colonists/
# ship/cargo/ranking/medal/faction fan-out) is a separate, much larger
# surface already outside this WO's scope; proving pass-through here is
# sufficient to prove "near is allowed by the gate".
# ---------------------------------------------------------------------------


def test_claim_planet_allows_near_pose_and_falls_through_to_next_check():
    sector_id = 602
    planet_id = uuid.uuid4()
    planet = _fake_planet(
        planet_id, sector_id=sector_id, name="Nearhaven", owner_id="someone-else",
    )
    session = _FakeSession(
        sector=_fake_sector(sector_id),
        planets=[planet],
        celestial_row=_Row(composition={"bodies": [], "stations": []}),
    )
    target_x, target_y = _target_xy(session, sector_id, planet_id)
    player = _fake_player(sector_id=sector_id, x_pct=target_x, y_pct=target_y)

    with pytest.raises(HTTPException) as exc:
        _run(claim_planet(planet_id=str(planet_id), player=player, db=session))

    assert exc.value.status_code == 400
    assert "already claimed" in exc.value.detail
    assert "too far" not in exc.value.detail


# ---------------------------------------------------------------------------
# Boundary: same DOCK_LAND_PROXIMITY_RANGE_EM cutoff dock/land use, exercised
# through the route (not the raw math -- that's test_dock_land_proximity.py's
# job).
# ---------------------------------------------------------------------------


def test_claim_planet_boundary_matches_dock_land_threshold():
    sector_id = 603
    planet_id = uuid.uuid4()
    planet = _fake_planet(
        planet_id, sector_id=sector_id, name="Edgeworld", owner_id="someone-else",
    )
    session = _FakeSession(
        sector=_fake_sector(sector_id),
        planets=[planet],
        celestial_row=_Row(composition={"bodies": [], "stations": []}),
    )
    target_x, target_y = _target_xy(session, sector_id, planet_id)

    threshold_px = DOCK_LAND_PROXIMITY_RANGE_EM * LAYOUT_BAND_REM_PX
    just_inside_x = target_x + (((threshold_px - 0.5) / 1440.0) * 100.0)
    just_outside_x = target_x + (((threshold_px + 0.5) / 1440.0) * 100.0)

    inside_player = _fake_player(sector_id=sector_id, x_pct=just_inside_x, y_pct=target_y)
    with pytest.raises(HTTPException) as inside_exc:
        _run(claim_planet(planet_id=str(planet_id), player=inside_player, db=session))
    assert "already claimed" in inside_exc.value.detail  # passed the gate

    outside_player = _fake_player(sector_id=sector_id, x_pct=just_outside_x, y_pct=target_y)
    with pytest.raises(HTTPException) as outside_exc:
        _run(claim_planet(planet_id=str(planet_id), player=outside_player, db=session))
    assert "too far" in outside_exc.value.detail  # rejected by the gate
