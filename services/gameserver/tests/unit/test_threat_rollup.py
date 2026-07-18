"""WO-UI2-TACTICAL-THREAT-ENDPOINT — threat_service.compute_threat_rollup +
GET /api/v1/nav/threat.

STATIC-ONLY (REVISE): cipher + the lead confirmed a HIGH security leak in the
first pass (live per-sector hostile-NPC presence + live inbound-police-squad
composition, both cross-player/cross-sector real-time intel) and the
orchestrator RULED the rollup static-only. The hostile-archetype and
inbound-squad test classes that exercised the REMOVED inputs are gone along
with the inputs themselves — see threat_service.py's module docstring for
the full rationale. Every remaining test below exercises only the four
STATIC inputs that survived: security_level, hazard_level, last_combat
recency, and region-level pirate pressure.

DB-free: hand-built fakes, no real DB/app (mirrors test_nav_chart.py's
FakeChartSession — .filter() clauses are real SQLAlchemy expressions applied
against in-memory row stores, not asserted by call-arg inspection).

Scope boundary (deliberate, matching test_nav_chart.py's own convention —
"mock already-shipped calculators, don't re-fake internals"):
``NavService.get_known_sector_ids`` and
``pirate_ecosystem_service.compute_population_score`` are PRE-EXISTING,
already-shipped machinery (unchanged by this WO) — monkeypatched rather than
re-faked via PirateHolding/ARIAExplorationMap row stores. These tests
exercise threat_service's OWN new logic: given a known-sector set (however
sourced) and Sector rows, compute the right per-input contributions,
sum+clamp the score, band it, and scope the response to exactly the known
graph.

Acceptance-criteria map:
  banding boundaries (24/25, 49/50, 74/75)        -> TestBanding
  (c) security=10,hazard=0,none -> CLEAR           -> TestClearSector
  (d) security=1,hazard=8,recent-combat -> HOSTILE
      + contributors name low_security+hazard+
      recent_combat                                -> TestHostileSector
  (e) null/missing everything -> no crash          -> TestNullSafety
  (a) known-graph parity                           -> TestKnownGraphParity
  route wiring + shared auth dependency            -> TestRouteWiring
  input-specific unit coverage (recent-combat
      window, pirate-pressure clamp/missing-region) -> TestRecentCombat /
      TestPiratePressure
"""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, List

import pytest
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import Null

from src.models.sector import Sector
from src.services import threat_service
from src.services.nav_service import NavService
from src.services.threat_service import (
    BAND_CAUTION,
    BAND_CLEAR,
    BAND_HOSTILE,
    BAND_LETHAL,
    CONTRIB_HAZARD,
    CONTRIB_LOW_SECURITY,
    CONTRIB_PIRATE_PRESSURE,
    CONTRIB_RECENT_COMBAT,
    PIRATE_MAX,
    RECENT_COMBAT_W,
    RECENT_COMBAT_WINDOW_H,
    ThreatContributor,
    ThreatEntry,
    _band_for_score,
    compute_threat_rollup,
)

# --------------------------------------------------------------------------- #
# In-memory fake session — interprets the SUT's real filter() clauses
# (mirrors test_nav_chart.py's _FakeQuery/_condition_matches)
# --------------------------------------------------------------------------- #

def _condition_value(right: Any) -> Any:
    if isinstance(right, Null):
        return None
    return right.value


def _condition_matches(row: Any, condition: Any) -> bool:
    column = condition.left.key
    actual = getattr(row, column)
    op = condition.operator
    if op is operators.is_:
        return actual is None
    value = _condition_value(condition.right)
    if op is operators.eq:
        return actual == value
    if op is operators.in_op:
        return actual in value
    raise AssertionError(f"unhandled operator {op!r} on column {column!r}")


class _FakeQuery:
    def __init__(self, store: List[Any], columns: List[str] = None) -> None:
        self._store = store
        self._conditions: tuple = ()
        self._columns = columns

    def filter(self, *conditions: Any) -> "_FakeQuery":
        self._conditions = self._conditions + conditions
        return self

    def _matching(self) -> List[Any]:
        rows = [r for r in self._store if all(_condition_matches(r, c) for c in self._conditions)]
        if self._columns is None:
            return rows
        return [tuple(getattr(r, col) for col in self._columns) for r in rows]

    def all(self) -> List[Any]:
        return list(self._matching())


class FakeThreatSession:
    """db double for compute_threat_rollup: .query(Sector) routes to the
    in-memory sector store (real filter() clauses applied).
    compute_population_score (PirateHolding) is monkeypatched at the module
    level instead of faked here — see the module docstring's scope-boundary
    note. STATIC-ONLY (REVISE): no NPCCharacter/PendingEngagement stores —
    those inputs were removed from the SUT entirely."""

    def __init__(self, *, sectors: List[Any]) -> None:
        self._stores = {Sector: sectors}

    def query(self, *entities: Any) -> _FakeQuery:
        if len(entities) == 1 and isinstance(entities[0], type):
            model = entities[0]
            assert model in self._stores, f"unexpected query for {model!r}"
            return _FakeQuery(self._stores[model])
        model = entities[0].class_
        assert model in self._stores, f"unexpected query for {entities!r}"
        columns = [e.key for e in entities]
        return _FakeQuery(self._stores[model], columns=columns)


# --------------------------------------------------------------------------- #
# Fixture helpers
# --------------------------------------------------------------------------- #

def _sector(
    sector_id: int,
    *,
    security_level: Any = 5,
    hazard_level: Any = 0,
    last_combat: Any = None,
    region_id: Any = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        sector_id=sector_id,
        security_level=security_level,
        hazard_level=hazard_level,
        last_combat=last_combat,
        region_id=region_id,
    )


def _player(*, current_sector_id: int) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), current_sector_id=current_sector_id, team_id=None)


def _patch_known(monkeypatch: pytest.MonkeyPatch, known_ids: set) -> None:
    monkeypatch.setattr(NavService, "get_known_sector_ids", lambda self, player: set(known_ids))


def _patch_pirate_score(monkeypatch: pytest.MonkeyPatch, scores_by_region: dict) -> None:
    monkeypatch.setattr(
        threat_service,
        "compute_population_score",
        lambda db, region_id: scores_by_region.get(region_id, 0),
    )


# --------------------------------------------------------------------------- #
# Banding boundaries: 24/25, 49/50, 74/75
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestBanding:
    @pytest.mark.parametrize(
        "score,expected_band",
        [
            (0, BAND_CLEAR),
            (24, BAND_CLEAR),
            (25, BAND_CAUTION),
            (49, BAND_CAUTION),
            (50, BAND_HOSTILE),
            (74, BAND_HOSTILE),
            (75, BAND_LETHAL),
            (100, BAND_LETHAL),
        ],
    )
    def test_band_boundaries(self, score: int, expected_band: str) -> None:
        assert _band_for_score(score) == expected_band


# --------------------------------------------------------------------------- #
# Accept (c): security=10, hazard=0, no threats -> CLEAR, zero contributors
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestClearSector:
    def test_full_security_no_hazard_no_threats_is_clear(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sector = _sector(100, security_level=10, hazard_level=0, region_id=None)
        _patch_known(monkeypatch, {100})
        db = FakeThreatSession(sectors=[sector])
        player = _player(current_sector_id=100)

        rollup = compute_threat_rollup(db, player)

        assert len(rollup) == 1
        entry = rollup[0]
        assert entry.sector_id == 100
        assert entry.score == 0
        assert entry.band == BAND_CLEAR
        assert entry.contributors == []


# --------------------------------------------------------------------------- #
# Accept (d): security=1, hazard=8, recent combat -> HOSTILE, named contribs
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestHostileSector:
    def test_low_security_high_hazard_recent_combat_scores_hostile(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # (10-1)*SEC_W(4)=36 + 8*HAZ_W(2)=16 + RECENT_COMBAT_W(15) = 67 -> HOSTILE (50-74)
        sector = _sector(
            200, security_level=1, hazard_level=8, region_id=None,
            last_combat=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        _patch_known(monkeypatch, {200})
        db = FakeThreatSession(sectors=[sector])
        player = _player(current_sector_id=200)

        rollup = compute_threat_rollup(db, player)
        entry = rollup[0]

        assert entry.score == 67
        assert entry.band == BAND_HOSTILE
        contributor_inputs = {c.input for c in entry.contributors}
        assert CONTRIB_LOW_SECURITY in contributor_inputs
        assert CONTRIB_HAZARD in contributor_inputs
        assert CONTRIB_RECENT_COMBAT in contributor_inputs

    def test_worst_case_static_sector_reaches_lethal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # (10-1)*4=36 + 10*2=20 + 15 + PIRATE_MAX(15) = 86 -> LETHAL (>=75) --
        # confirms the band is actually reachable via static inputs alone.
        region_id = uuid.uuid4()
        sector = _sector(
            201, security_level=1, hazard_level=10, region_id=region_id,
            last_combat=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        _patch_known(monkeypatch, {201})
        _patch_pirate_score(monkeypatch, {region_id: 999})  # clamps to PIRATE_MAX
        db = FakeThreatSession(sectors=[sector])
        player = _player(current_sector_id=201)

        rollup = compute_threat_rollup(db, player)
        entry = rollup[0]

        assert entry.score == 86
        assert entry.band == BAND_LETHAL
        contributor_inputs = {c.input for c in entry.contributors}
        assert contributor_inputs == {
            CONTRIB_LOW_SECURITY, CONTRIB_HAZARD, CONTRIB_RECENT_COMBAT, CONTRIB_PIRATE_PRESSURE,
        }


# --------------------------------------------------------------------------- #
# Accept (e): null/missing everything -> no crash, correct null-safe defaults
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestNullSafety:
    def test_null_security_no_last_combat_no_region_never_crashes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sector = _sector(300, security_level=None, hazard_level=0, last_combat=None, region_id=None)
        _patch_known(monkeypatch, {300})
        db = FakeThreatSession(sectors=[sector])
        player = _player(current_sector_id=300)

        rollup = compute_threat_rollup(db, player)  # must not raise

        assert len(rollup) == 1
        entry = rollup[0]
        assert isinstance(entry.score, int)
        assert 0 <= entry.score <= 100
        assert entry.band in (BAND_CLEAR, BAND_CAUTION, BAND_HOSTILE, BAND_LETHAL)

        contributor_inputs = {c.input for c in entry.contributors}
        # null security_level defaults to 5 (its own column default) per the
        # WO's explicit null-safety instruction -- a non-zero low_security
        # contribution is the CORRECT behaviour here, not a violation.
        assert CONTRIB_RECENT_COMBAT not in contributor_inputs  # last_combat is null
        assert CONTRIB_PIRATE_PRESSURE not in contributor_inputs  # region_id is null

    def test_missing_region_pirate_state_contributes_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        region_id = uuid.uuid4()
        sector = _sector(301, security_level=10, hazard_level=0, region_id=region_id)
        _patch_known(monkeypatch, {301})
        _patch_pirate_score(monkeypatch, {})  # region absent from the map -- .get(..., 0)
        db = FakeThreatSession(sectors=[sector])
        player = _player(current_sector_id=301)

        rollup = compute_threat_rollup(db, player)
        entry = rollup[0]

        assert entry.score == 0
        assert not any(c.input == CONTRIB_PIRATE_PRESSURE for c in entry.contributors)


# --------------------------------------------------------------------------- #
# Accept (a): known-graph parity / data-scoping
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestKnownGraphParity:
    def test_rollup_sector_set_matches_known_sector_ids_exactly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        s1, s2, s3 = _sector(800), _sector(801), _sector(802)
        unknown = _sector(999)  # present in the Sector store but NOT in the known set
        known_ids = {800, 801, 802}
        _patch_known(monkeypatch, known_ids)
        db = FakeThreatSession(sectors=[s1, s2, s3, unknown])
        player = _player(current_sector_id=800)

        rollup = compute_threat_rollup(db, player)

        assert {e.sector_id for e in rollup} == known_ids
        assert 999 not in {e.sector_id for e in rollup}  # data-scoping: unknown sector never leaks

    def test_empty_known_set_returns_empty_rollup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_known(monkeypatch, set())
        db = FakeThreatSession(sectors=[])
        player = _player(current_sector_id=1)

        assert compute_threat_rollup(db, player) == []


# --------------------------------------------------------------------------- #
# recent_combat — window edge
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestRecentCombat:
    def test_last_combat_within_window_contributes_full_points(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sector = _sector(
            700, security_level=10, hazard_level=0,
            last_combat=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        _patch_known(monkeypatch, {700})
        db = FakeThreatSession(sectors=[sector])
        player = _player(current_sector_id=700)

        rollup = compute_threat_rollup(db, player)
        entry = rollup[0]

        combat_contrib = next(c for c in entry.contributors if c.input == CONTRIB_RECENT_COMBAT)
        assert combat_contrib.points == RECENT_COMBAT_W

    def test_last_combat_outside_window_contributes_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sector = _sector(
            701, security_level=10, hazard_level=0,
            last_combat=datetime.now(timezone.utc) - timedelta(hours=RECENT_COMBAT_WINDOW_H + 1),
        )
        _patch_known(monkeypatch, {701})
        db = FakeThreatSession(sectors=[sector])
        player = _player(current_sector_id=701)

        rollup = compute_threat_rollup(db, player)
        entry = rollup[0]

        assert not any(c.input == CONTRIB_RECENT_COMBAT for c in entry.contributors)


# --------------------------------------------------------------------------- #
# pirate_modifier — clamp at PIRATE_MAX
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestPiratePressure:
    def test_region_pirate_population_score_contributes_capped_at_pirate_max(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        region_id = uuid.uuid4()
        sector = _sector(400, security_level=10, hazard_level=0, region_id=region_id)
        _patch_known(monkeypatch, {400})
        _patch_pirate_score(monkeypatch, {region_id: 999})  # far above PIRATE_MAX
        db = FakeThreatSession(sectors=[sector])
        player = _player(current_sector_id=400)

        rollup = compute_threat_rollup(db, player)
        entry = rollup[0]

        pirate_contrib = next(c for c in entry.contributors if c.input == CONTRIB_PIRATE_PRESSURE)
        assert pirate_contrib.points == PIRATE_MAX  # clamped, not the raw 999
        assert entry.score == PIRATE_MAX  # every other input is zero in this fixture


# --------------------------------------------------------------------------- #
# Route wiring — GET /nav/threat delegates + shares /nav/chart's auth dep
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestRouteWiring:
    @pytest.mark.asyncio
    async def test_get_nav_threat_route_delegates_to_threat_service(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.api.routes import nav as nav_routes

        sentinel_entry = ThreatEntry(
            sector_id=1, score=10, band=BAND_CLEAR,
            contributors=[ThreatContributor(input="low_security", points=5)],
        )
        captured: dict = {}

        def _fake_compute_threat_rollup(db: Any, player: Any) -> list:
            captured["db"] = db
            captured["player"] = player
            return [sentinel_entry]

        monkeypatch.setattr(nav_routes, "compute_threat_rollup", _fake_compute_threat_rollup)

        fake_db = object()
        fake_player = object()
        result = await nav_routes.get_nav_threat(db=fake_db, current_player=fake_player)

        assert captured["db"] is fake_db
        assert captured["player"] is fake_player
        assert len(result) == 1
        assert result[0].sector_id == 1
        assert result[0].score == 10
        assert result[0].band == BAND_CLEAR
        assert result[0].contributors[0].input == "low_security"
        assert result[0].contributors[0].points == 5

    def test_get_nav_threat_uses_same_auth_dependency_as_get_nav_chart(self) -> None:
        from src.api.routes import nav as nav_routes

        chart_dep = inspect.signature(nav_routes.get_nav_chart).parameters["current_player"].default
        threat_dep = inspect.signature(nav_routes.get_nav_threat).parameters["current_player"].default

        assert chart_dep.dependency is nav_routes.get_current_player
        assert threat_dep.dependency is nav_routes.get_current_player
        assert chart_dep.dependency is threat_dep.dependency
