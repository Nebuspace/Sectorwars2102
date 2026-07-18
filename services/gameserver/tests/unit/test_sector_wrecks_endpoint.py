"""DB-free pins for WO-CMB-SALVAGE-LOOP-1 Lane 1: GET
``/sectors/{sector_id}/wrecks`` (routes/sectors.py).

Determinism: same frozen-``datetime`` technique as test_salvage_turn_cost.py
-- ``salvage_service.datetime`` (which the route calls into via
``salvage_service.grace_status`` for the ``would_flag_suspect`` preview) is
monkeypatched to a fixed ``FROZEN_NOW`` for every test in this file.

Harness: a permissive DB-free ``_FakeQuery``/``_FakeSession`` (filter/
options/order_by/limit are all no-op passthroughs; only ``.first()``/
``.all()`` return real fixture data) -- the same convention as every other
trading-adjacent test file in this suite. Because ``.filter()`` doesn't
actually filter, this harness alone can't catch "compared the wrong
column" regressions (e.g. numeric sector_id vs. the resolved UUID) -- that
specific risk is covered by a structural/source pin instead
(TestNumericToUuidSectorResolution), not by the fake's runtime behavior.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import pytest

from src.api.routes.sectors import WRECK_LISTING_LIMIT, get_sector_wrecks
from src.models.cargo_wreck import CargoWreck, WreckCause
from src.models.player import Player
from src.models.sector import Sector
from src.models.ship import ShipType
from src.services import salvage_service

FROZEN_NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FROZEN_NOW


@pytest.fixture(autouse=True)
def _frozen_now(monkeypatch):
    # Two SEPARATE `datetime` imports need freezing: sectors.py's own (for
    # age_seconds and the `now` it passes into grace_status) and
    # salvage_service's (grace_status falls back to its own datetime.now()
    # whenever a caller doesn't pass `now=`, which this route always does --
    # frozen anyway for symmetry / defense against a future call-site change).
    from src.api.routes import sectors as sectors_routes
    monkeypatch.setattr(sectors_routes, "datetime", _FrozenDateTime)
    monkeypatch.setattr(salvage_service, "datetime", _FrozenDateTime)


# ---------------------------------------------------------------------------
# Fake DB session
# ---------------------------------------------------------------------------


class _FakeQuery:
    def __init__(self, *, first: Any = None, all_results=None) -> None:
        self._first = first
        self._all = list(all_results) if all_results is not None else []

    def filter(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def options(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def order_by(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def limit(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def first(self) -> Any:
        return self._first

    def all(self) -> list:
        return self._all


class _FakeSession:
    def __init__(self, specs: Dict[type, _FakeQuery]) -> None:
        self._specs = specs

    def query(self, target: Any) -> _FakeQuery:
        assert target in self._specs, f"unexpected query for {target!r}"
        return self._specs[target]


def _player(*, team_id=None, nickname: str | None = None) -> Player:
    return Player(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        turns=10,
        max_turns=1000,
        credits=0,
        current_sector_id=5,
        current_region_id=None,
        current_ship_id=uuid.uuid4(),
        team_id=team_id,
        is_suspect=False,
        nickname=nickname,
    )


def _sector(*, sector_num: int = 5) -> Sector:
    return Sector(id=uuid.uuid4(), sector_id=sector_num, name="Test Sector", region_id=None)


def _wreck(*, sector_uuid, cargo=None, created_at=FROZEN_NOW, original_owner=None,
           original_team_id=None, killing_blow_pilot_id=None,
           cause: WreckCause = WreckCause.COMBAT,
           destroyed_ship_type: ShipType = ShipType.CARGO_HAULER) -> CargoWreck:
    wreck = CargoWreck(
        id=uuid.uuid4(),
        sector_id=sector_uuid,
        original_owner_id=original_owner.id if original_owner else None,
        original_team_id=original_team_id,
        killing_blow_pilot_id=killing_blow_pilot_id,
        destroyed_ship_id=None,
        destroyed_ship_type=destroyed_ship_type,
        cargo=dict(cargo if cargo is not None else {"ore": 100}),
        created_at=created_at,
        cause=cause,
    )
    # Direct relationship assignment (in-memory, no join needed) -- mirrors
    # how the trading-hooks harness assigns player.current_ship directly.
    wreck.original_owner = original_owner
    return wreck


def _session_for(sector: Sector, wrecks: list) -> _FakeSession:
    return _FakeSession({
        Sector: _FakeQuery(first=sector),
        CargoWreck: _FakeQuery(all_results=wrecks),
    })


# ---------------------------------------------------------------------------
# Sector resolution: [] vs 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSectorResolution:
    async def test_unknown_sector_404s(self):
        db = _FakeSession({Sector: _FakeQuery(first=None)})
        player = _player()

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await get_sector_wrecks(sector_id=999, player=player, db=db)

        assert exc_info.value.status_code == 404

    async def test_known_sector_with_no_wrecks_returns_empty_list(self):
        sector = _sector()
        db = _session_for(sector, [])
        player = _player()

        result = await get_sector_wrecks(sector_id=5, player=player, db=db)

        assert result == []


# ---------------------------------------------------------------------------
# Field completeness -- and damage_type's deliberate ABSENCE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFieldCompleteness:
    async def test_wreck_response_has_every_documented_field(self):
        owner = _player(nickname="Ace")
        sector = _sector()
        created_at = FROZEN_NOW - timedelta(minutes=10)
        wreck = _wreck(
            sector_uuid=sector.id,
            cargo={"ore": 40, "organics": 15},
            created_at=created_at,
            original_owner=owner,
        )
        db = _session_for(sector, [wreck])
        caller = _player()  # a stranger, distinct from owner

        result = await get_sector_wrecks(sector_id=5, player=caller, db=db)

        assert len(result) == 1
        r = result[0]
        assert r.id == str(wreck.id)
        assert r.original_owner_id == str(owner.id)
        assert r.original_owner_name == "Ace"
        assert r.destroyed_ship_type == "CARGO_HAULER"
        assert r.cause == "COMBAT"
        assert r.created_at == created_at.isoformat()
        assert r.age_seconds == pytest.approx(600.0)  # 10 minutes
        assert r.cargo == {"ore": 40, "organics": 15}
        assert r.would_flag_suspect is True  # stranger, inside grace

    async def test_damage_type_key_is_absent(self):
        """CargoWreck has no damage_type column (Max-parked NO-CANON) --
        the response must not fabricate one."""
        sector = _sector()
        wreck = _wreck(sector_uuid=sector.id)
        db = _session_for(sector, [wreck])
        player = _player()

        result = await get_sector_wrecks(sector_id=5, player=player, db=db)

        assert "damage_type" not in result[0].model_dump()

    async def test_wreck_with_no_owner_reports_none(self):
        """HAZARD-cause wrecks (or a purged owner) carry a NULL
        original_owner_id -- the response must not crash resolving a name."""
        sector = _sector()
        wreck = _wreck(sector_uuid=sector.id, cause=WreckCause.HAZARD, original_owner=None)
        db = _session_for(sector, [wreck])
        player = _player()

        result = await get_sector_wrecks(sector_id=5, player=player, db=db)

        assert result[0].original_owner_id is None
        assert result[0].original_owner_name is None


# ---------------------------------------------------------------------------
# would_flag_suspect matrix: owner / team-mate / killer / stranger, x2 grace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestWouldFlagSuspectMatrix:
    async def _wreck_for(self, sector, *, relation: str, in_grace: bool):
        caller = _player(team_id=uuid.uuid4() if relation == "team_member" else None)
        created_at = FROZEN_NOW - (timedelta(minutes=10) if in_grace else timedelta(hours=2))

        if relation == "owner":
            wreck = _wreck(sector_uuid=sector.id, original_owner=caller, created_at=created_at)
        elif relation == "team_member":
            wreck = _wreck(sector_uuid=sector.id, original_team_id=caller.team_id, created_at=created_at)
        elif relation == "killer":
            wreck = _wreck(sector_uuid=sector.id, killing_blow_pilot_id=caller.id, created_at=created_at)
        elif relation == "stranger":
            wreck = _wreck(sector_uuid=sector.id, created_at=created_at)
        else:
            raise ValueError(relation)
        return caller, wreck

    @pytest.mark.parametrize("relation", ["owner", "team_member", "killer"])
    @pytest.mark.parametrize("in_grace", [True, False])
    async def test_exempt_relations_never_flag(self, relation, in_grace):
        sector = _sector()
        caller, wreck = await self._wreck_for(sector, relation=relation, in_grace=in_grace)
        db = _session_for(sector, [wreck])

        result = await get_sector_wrecks(sector_id=5, player=caller, db=db)

        assert result[0].would_flag_suspect is False

    async def test_stranger_flags_inside_grace_only(self):
        sector = _sector()
        caller, wreck = await self._wreck_for(sector, relation="stranger", in_grace=True)
        db = _session_for(sector, [wreck])

        result = await get_sector_wrecks(sector_id=5, player=caller, db=db)
        assert result[0].would_flag_suspect is True

    async def test_stranger_never_flags_outside_grace(self):
        sector = _sector()
        caller, wreck = await self._wreck_for(sector, relation="stranger", in_grace=False)
        db = _session_for(sector, [wreck])

        result = await get_sector_wrecks(sector_id=5, player=caller, db=db)
        assert result[0].would_flag_suspect is False


# ---------------------------------------------------------------------------
# Structural pins: numeric-vs-UUID sector resolution + the response cap
# ---------------------------------------------------------------------------


class TestNumericToUuidSectorResolution:
    """The permissive fake query above can't catch a "compared the wrong
    column" regression (its .filter() never actually filters) -- these pin
    the real comparison expressions from source instead."""

    def test_cargo_wreck_query_filters_by_the_resolved_sector_uuid(self):
        import inspect

        from src.api.routes import sectors as sectors_routes

        source = inspect.getsource(sectors_routes.get_sector_wrecks)
        assert "CargoWreck.sector_id == sector.id" in source
        # The raw numeric route param must never be compared directly
        # against CargoWreck.sector_id (a UUID column).
        assert "CargoWreck.sector_id == sector_id" not in source

    def test_response_is_capped_at_the_listing_limit(self):
        import inspect

        from src.api.routes import sectors as sectors_routes

        assert WRECK_LISTING_LIMIT == 100
        source = inspect.getsource(sectors_routes.get_sector_wrecks)
        assert ".limit(WRECK_LISTING_LIMIT)" in source
