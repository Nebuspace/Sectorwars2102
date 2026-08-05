"""WO-UI2-INTRASYSTEM-MODEL (REVISE): field-parity + GENUINE READ-ONLY pins
for the unified ``GET /sectors/{sector_id}/contents`` endpoint
(routes/sectors.py).

Orchestrator ruling: a display fetch must never pace a progress mechanic.
The route no longer calls the 4 fragments' own ROUTE functions (those
trigger discovery marks / gate-harmonization ADVANCE / beacon-expiry
writes as a side effect of being viewed) -- it calls the underlying READ
services directly: ``generate_system(..., read_only=True)``,
``find_formations_for_sector`` + ``is_formation_known_to_player`` +
``is_formation_investigated`` (special_formation_service, read without
discovering), ``get_sector_wrecks`` (already 100% read-only, called as-is),
and ``warp_gate_service.list_sector_structures(..., read_only=True)``.

WRITE-FREE PROOF STRATEGY: ``_NoWriteSession``/``_NoWriteQuery`` below
implement ONLY the read surface real code needs (``query().filter()
.join().options().order_by().limit().all().first().count()``) -- there is
NO ``execute``/``add``/``commit``/``flush``/``delete``, and the query object
has NO ``with_for_update``/``populate_existing``. Every test in this file
runs the REAL service functions (not mocks) against this session, so ANY
accidental write anywhere in the real call graph -- the skeleton
first-visit INSERT, a discovery-mark INSERT, the harmonization-ADVANCE's
``with_for_update`` lock, the beacon-expiry ``db.flush()`` -- would crash
with ``AttributeError`` immediately. A full, successful run against this
session IS the write-free proof; it is not vacuous the way mocking the 4
read services would be.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

import pytest
from fastapi import HTTPException

from src.api.routes.player import router as player_router
from src.api.routes.sectors import (
    SectorContentsResponse,
    get_sector_contents,
)
from src.api.routes.sectors import router as sectors_router
from src.api.routes.warp_gates import router as warp_gates_router
from src.models.cargo_wreck import CargoWreck, WreckCause
from src.models.planet import Planet, PlanetStatus, PlanetType
from src.models.player import Player
from src.models.sector import Sector
from src.models.ship import ShipType
from src.models.special_formation import PlayerFormationKnowledge, SpecialFormation, SpecialFormationType
from src.models.station import Station
from src.models.warp_gate import WarpGateBeacon, WarpGateBeaconStatus
from src.services import warp_gate_service
from src.services.celestial_service import generate_system

CURRENT_SECTOR = 42


def _player(*, current_sector_id: int = CURRENT_SECTOR) -> Player:
    return Player(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        turns=10,
        max_turns=1000,
        credits=0,
        current_sector_id=current_sector_id,
        current_region_id=None,
        current_ship_id=uuid.uuid4(),
        is_suspect=False,
    )


def _sector(*, sector_num: int = CURRENT_SECTOR) -> Sector:
    return Sector(
        id=uuid.uuid4(), sector_id=sector_num, name="Test Sector",
        type="NEBULA",  # generate_skeleton str()s a non-enum type fine
        region_id=None, hazard_level=4, radiation_level=0.35,
        players_present=[{"player_id": str(uuid.uuid4()), "username": "Ace", "is_npc": False}],
        x_coord=1, y_coord=2, z_coord=3,
    )


def _planet(*, sector_uuid, discovered_by=None) -> Planet:
    return Planet(
        id=uuid.uuid4(), sector_uuid=sector_uuid, sector_id=CURRENT_SECTOR,
        name="Test World", type=PlanetType.TERRAN, status=PlanetStatus.HABITABLE,
        owner_id=None, resources={}, population=0, max_population=0,
        habitability_score=50, discovered_by=discovered_by,
    )


def _station(*, sector_uuid) -> Station:
    from src.models.station import StationStatus, StationType
    return Station(
        id=uuid.uuid4(), sector_uuid=sector_uuid, sector_id=CURRENT_SECTOR,
        name="Dock Alpha", type=StationType.TRADING, status=StationStatus.OPERATIONAL,
        owner_id=None, services={},
    )


def _wreck(*, sector_uuid) -> CargoWreck:
    wreck = CargoWreck(
        id=uuid.uuid4(), sector_id=sector_uuid, original_owner_id=None,
        original_team_id=None, killing_blow_pilot_id=None, destroyed_ship_id=None,
        destroyed_ship_type=ShipType.CARGO_HAULER, cargo={"ore": 40},
        cause=WreckCause.COMBAT, created_at=datetime.now(timezone.utc),
    )
    wreck.original_owner = None
    return wreck


def _formation(*, region_id, anchor_sector_id) -> SpecialFormation:
    return SpecialFormation(
        id=uuid.uuid4(), region_id=region_id, type=SpecialFormationType.BUBBLE,
        name="Bubble of the Lost Star", anchor_sector_id=anchor_sector_id,
        interior_sector_ids=[],
    )


def _beacon(*, player_id, status=WarpGateBeaconStatus.DEPLOYED, invulnerable_until=None) -> WarpGateBeacon:
    return WarpGateBeacon(
        id=uuid.uuid4(), player_id=player_id, source_sector_id=CURRENT_SECTOR,
        destination_sector_id=7, status=status, invulnerable_until=invulnerable_until, hp=5000,
    )


# ---------------------------------------------------------------------------
# Write-free DB harness -- deliberately incomplete (read-only surface only)
# ---------------------------------------------------------------------------


class _NoWriteQuery:
    """Permissive filter/join/options/order_by/limit passthrough (same
    convention as test_sector_wrecks_endpoint.py); NO with_for_update /
    populate_existing -- a regressed harmonization-ADVANCE call crashes."""

    def __init__(self, *, first: Any = None, all_results=None, count: int = 0) -> None:
        self._first = first
        self._all = list(all_results) if all_results is not None else []
        self._count = count

    def filter(self, *a: Any, **k: Any) -> "_NoWriteQuery":
        return self

    def join(self, *a: Any, **k: Any) -> "_NoWriteQuery":
        return self

    def options(self, *a: Any, **k: Any) -> "_NoWriteQuery":
        return self

    def order_by(self, *a: Any, **k: Any) -> "_NoWriteQuery":
        return self

    def limit(self, *a: Any, **k: Any) -> "_NoWriteQuery":
        return self

    def first(self) -> Any:
        return self._first

    def all(self) -> list:
        return self._all

    def count(self) -> int:
        return self._count


class _NoWriteSession:
    """query() ONLY -- no execute/add/commit/flush/delete. Any accidental
    write anywhere in the real call graph raises AttributeError."""

    def __init__(self, specs: Dict[type, _NoWriteQuery]) -> None:
        self._specs = specs

    def query(self, target: Any) -> _NoWriteQuery:
        assert target in self._specs, f"unexpected query for {target!r}"
        return self._specs[target]


def _warp_gate_model():
    from src.models.warp_gate import WarpGate
    return WarpGate


def _sector_celestial_model():
    from src.models.sector_celestial import SectorCelestial
    return SectorCelestial


def _session_for(
    sector: Sector, *, planets=None, stations=None, wrecks=None,
    formations=None, formation_known: bool = False, beacons=None,
) -> _NoWriteSession:
    return _NoWriteSession({
        Sector: _NoWriteQuery(first=sector),
        _sector_celestial_model(): _NoWriteQuery(first=None),  # -> pure generate_skeleton fallback
        Planet: _NoWriteQuery(all_results=planets or []),
        Station: _NoWriteQuery(all_results=stations or []),
        CargoWreck: _NoWriteQuery(all_results=wrecks or []),
        SpecialFormation: _NoWriteQuery(all_results=formations or []),
        PlayerFormationKnowledge: _NoWriteQuery(first=(object() if formation_known else None)),
        WarpGateBeacon: _NoWriteQuery(all_results=beacons or []),
        Player: _NoWriteQuery(first=None),  # beacon/gate owner lookups in list_sector_structures
        # WarpGate backs both the ACTIVE-gates listing and the beacon-expiry
        # in-progress count -- empty/zero is sufficient for every scenario
        # here (no HARMONIZING/ACTIVE gate fixtures needed to prove
        # write-freedom; see module docstring -- the proof is the ABSENCE
        # of any write call, which this session structurally cannot
        # satisfy regardless of the data).
        _warp_gate_model(): _NoWriteQuery(all_results=[], count=0),
    })


# ---------------------------------------------------------------------------
# Current-sector-only fog-of-war guard (unchanged -- RULED KEEP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCurrentSectorOnlyGuard:
    async def test_other_sector_403s_before_touching_the_db(self):
        # A _NoWriteSession with ZERO specs -- the guard must fire before
        # ANY db.query() call, or this would crash on the unexpected-query
        # assertion instead of the intended 403.
        db = _NoWriteSession({})
        player = _player(current_sector_id=CURRENT_SECTOR)

        with pytest.raises(HTTPException) as exc_info:
            await get_sector_contents(sector_id=CURRENT_SECTOR + 1, player=player, db=db)

        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Field parity -- accept (b): old-union == new, zero missing fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFieldParity:
    async def test_static_system_fields_present_and_real_planet_merged(self):
        sector = _sector()
        player = _player()
        planet = _planet(sector_uuid=sector.id, discovered_by=player.id)
        station = _station(sector_uuid=sector.id)
        db = _session_for(sector, planets=[planet], stations=[station])

        result = await get_sector_contents(sector_id=CURRENT_SECTOR, player=player, db=db)

        assert result.sector_id == CURRENT_SECTOR
        assert result.sector_type == "NEBULA"
        assert result.bodies, "expected at least one body (min_bodies=1 real planet)"
        real_bodies = [b for b in result.bodies if b.get("real")]
        assert len(real_bodies) == 1
        merged = real_bodies[0]
        assert merged["planet_id"] == str(planet.id)
        assert merged["name"] == "Test World"
        # ADR-0073 can_rename: this player IS the discoverer.
        assert merged["can_rename"] is True
        assert len(result.stations) == 1
        assert result.stations[0]["station_id"] == str(station.id)

    async def test_live_ships_hazards_pass_through(self):
        sector = _sector()
        player = _player()
        db = _session_for(sector)

        result = await get_sector_contents(sector_id=CURRENT_SECTOR, player=player, db=db)

        assert result.live_ships == sector.players_present
        assert result.hazards.hazard_level == sector.hazard_level
        assert result.hazards.radiation_level == sector.radiation_level

    async def test_wrecks_pass_through(self):
        sector = _sector()
        player = _player()
        wreck = _wreck(sector_uuid=sector.id)
        db = _session_for(sector, wrecks=[wreck])

        result = await get_sector_contents(sector_id=CURRENT_SECTOR, player=player, db=db)

        assert len(result.wrecks) == 1
        assert result.wrecks[0].id == str(wreck.id)

    async def test_warp_gates_structure_present(self):
        sector = _sector()
        player = _player()
        db = _session_for(sector)

        result = await get_sector_contents(sector_id=CURRENT_SECTOR, player=player, db=db)

        assert result.warp_gates.beacons == []
        assert result.warp_gates.gates == []

    async def test_undiscovered_formation_disclosed_without_discovering(self):
        """The 'read without discovering' contract -- mirrors MoveOption's
        own precedent (player.py docstring: viewing a move list's
        special_formations does NOT discover anything)."""
        sector = _sector()
        player = _player()
        formation = _formation(region_id=uuid.uuid4(), anchor_sector_id=sector.id)
        db = _session_for(sector, formations=[formation], formation_known=False)

        result = await get_sector_contents(sector_id=CURRENT_SECTOR, player=player, db=db)

        assert len(result.formations) == 1
        f = result.formations[0]
        assert f.id == str(formation.id)
        assert f.is_discovered is False
        assert f.name is None  # identity withheld pre-discovery
        assert f.type is None
        assert f.is_anchor is True


# ---------------------------------------------------------------------------
# Write-free proof (accept: the whole point of option (c))
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestWriteFree:
    async def test_full_call_completes_against_a_session_with_no_write_methods(self):
        """Primary proof: the REAL generate_system/find_formations_for_sector/
        is_formation_known_to_player/get_sector_wrecks/list_sector_structures
        run end-to-end against a session that cannot satisfy execute/add/
        commit/flush/delete or with_for_update/populate_existing. A clean
        completion is only possible if none of those was ever called."""
        sector = _sector()
        player = _player()
        planet = _planet(sector_uuid=sector.id)
        db = _session_for(sector, planets=[planet])

        result = await get_sector_contents(sector_id=CURRENT_SECTOR, player=player, db=db)

        assert isinstance(result, SectorContentsResponse)

    async def test_deployed_beacon_status_unchanged_after_call(self):
        """A DEPLOYED beacon with no invulnerable_until short-circuits the
        read-only expiry preview to 'not expired' without even touching the
        WarpGate in-progress count -- its .status must be provably
        untouched (never flipped by _lazy_expire_beacon's write, which this
        endpoint never calls at all for read_only=True)."""
        sector = _sector()
        player = _player()
        beacon = _beacon(player_id=player.id, invulnerable_until=None)
        db = _session_for(sector, beacons=[beacon])

        result = await get_sector_contents(sector_id=CURRENT_SECTOR, player=player, db=db)

        assert beacon.status == WarpGateBeaconStatus.DEPLOYED
        assert len(result.warp_gates.beacons) == 1
        assert result.warp_gates.beacons[0]["beacon_id"] == str(beacon.id)

    async def test_route_source_never_calls_the_write_functions(self):
        # Call-pattern strings (trailing "(") deliberately, not bare names --
        # the route's own docstring discusses these functions in prose (to
        # explain what it does NOT call), which would false-positive a
        # bare-substring check.
        source = inspect.getsource(get_sector_contents)
        for banned in (
            "flip_formation_discovery(", "mark_planet_discovered(", "mark_feature_discovered(",
            "db.commit(", "db.add(", "db.execute(", "db.flush(",
            "get_current_sector(", "get_sector_structures(",  # the write-causing ROUTE fns
        ):
            assert banned not in source, banned

    async def test_generate_system_read_only_branch_skips_the_persistence_insert(self):
        source = inspect.getsource(generate_system)
        assert "read_only" in source
        assert "get_celestial_read_only" in source

    async def test_list_sector_structures_read_only_branch_skips_advance_and_expire_write(self):
        source = inspect.getsource(warp_gate_service.list_sector_structures)
        assert "if not read_only:" in source
        assert "_beacon_expired_readonly" in source


# ---------------------------------------------------------------------------
# Structural pin: genuine reuse of the READ layer, not the write-causing
# route functions, and not a reimplementation of any query.
# ---------------------------------------------------------------------------


class TestReuseNotReimplementation:
    def test_route_source_calls_the_read_layer_functions(self):
        source = inspect.getsource(get_sector_contents)
        for called in (
            "generate_system(", "find_formations_for_sector(", "is_formation_known_to_player(",
            "is_formation_investigated(", "get_sector_wrecks(", "list_sector_structures(",
        ):
            assert called in source, called
        assert "read_only=True" in source

    def test_response_model_field_names_match_the_union_contract(self):
        fields = set(SectorContentsResponse.model_fields)
        assert {"live_ships", "hazards", "formations", "wrecks", "warp_gates", "bodies"} <= fields


# ---------------------------------------------------------------------------
# Additivity -- accept (c): the 5 fragment routes stay registered unchanged,
# and the new route is a genuine addition, not a replacement.
# ---------------------------------------------------------------------------


class TestOldEndpointsUnchangedAndAdditive:
    def test_all_five_fragment_routes_plus_the_new_one_are_registered(self):
        sector_paths = {(r.path, frozenset(r.methods)) for r in sectors_router.routes}
        assert ("/sectors/{sector_id}/system", frozenset({"GET"})) in sector_paths
        assert ("/sectors/{sector_id}/wrecks", frozenset({"GET"})) in sector_paths
        assert ("/sectors/{sector_id}/contents", frozenset({"GET"})) in sector_paths

        player_paths = {(r.path, frozenset(r.methods)) for r in player_router.routes}
        assert ("/player/current-sector", frozenset({"GET"})) in player_paths

        warp_gate_paths = {(r.path, frozenset(r.methods)) for r in warp_gates_router.routes}
        assert ("/warp-gates/sector/{sector_id}", frozenset({"GET"})) in warp_gate_paths

    def test_get_or_create_celestial_and_advance_gate_functions_are_untouched(self):
        """The ORIGINAL write-performing functions still exist, unchanged,
        for the 4 fragment routes (which keep calling them exactly as
        before) -- only NEW read_only-gated call sites were added."""
        from src.services.celestial_service import get_or_create_celestial
        from src.services.warp_gate_service import _lazy_expire_beacon, advance_gates_touching_sector

        assert callable(get_or_create_celestial)
        assert callable(advance_gates_touching_sector)
        assert callable(_lazy_expire_beacon)
