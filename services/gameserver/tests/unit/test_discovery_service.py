"""Unit tests — discovery_service.py (planet + sector-feature discovery,
ADR-0073 / 2026-08-04 orchestrator ruling on exploration.void_walker).

No test file directly targets this module's own functions -- three other
test files exercise `mark_sector_discovered`/`mark_planet_discovered`/
`mark_feature_discovered` only indirectly, through quantum-jump/movement/
sector-contents orchestration. Adds direct, DB-free unit tests.

Sections:
  TestMarkSectorDiscovered — first-wins idempotency, the returned bool,
    and the discovery_date being set only on the newly-set path.
  TestMarkPlanetDiscovered — same shape for Planet.discovered_by/_at.
  TestMarkFeatureDiscovered — the INSERT-ON-CONFLICT-DO-NOTHING race-safe
    idempotency via `result.rowcount`, and the exact insert values.
"""

from uuid import uuid4

from src.models.planet import Planet
from src.models.sector import Sector
from src.services.discovery_service import (
    mark_feature_discovered,
    mark_planet_discovered,
    mark_sector_discovered,
)


class _FakeResult:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _FakeDb:
    def __init__(self, execute_rowcount=1):
        self.flush_calls = 0
        self.executed = []
        self._execute_rowcount = execute_rowcount

    def flush(self):
        self.flush_calls += 1

    def execute(self, stmt):
        self.executed.append(stmt)
        return _FakeResult(self._execute_rowcount)


def _sector(discovered_by_id=None):
    s = Sector()
    s.id = uuid4()
    s.discovered_by_id = discovered_by_id
    s.discovery_date = None
    return s


def _planet(discovered_by=None):
    p = Planet()
    p.id = uuid4()
    p.discovered_by = discovered_by
    p.discovered_at = None
    return p


class TestMarkSectorDiscovered:
    def test_first_discovery_sets_id_and_date_and_returns_true(self):
        sector = _sector(discovered_by_id=None)
        player_id = uuid4()
        db = _FakeDb()

        result = mark_sector_discovered(db, sector, player_id)

        assert result is True
        assert sector.discovered_by_id == player_id
        assert sector.discovery_date is not None
        assert db.flush_calls == 1

    def test_already_discovered_is_a_no_op_and_returns_false(self):
        original_discoverer = uuid4()
        sector = _sector(discovered_by_id=original_discoverer)
        db = _FakeDb()

        result = mark_sector_discovered(db, sector, uuid4())

        assert result is False
        assert sector.discovered_by_id == original_discoverer
        assert sector.discovery_date is None
        assert db.flush_calls == 0


class TestMarkPlanetDiscovered:
    def test_first_discovery_sets_id_and_date_and_returns_true(self):
        planet = _planet(discovered_by=None)
        player_id = uuid4()
        db = _FakeDb()

        result = mark_planet_discovered(db, planet, player_id)

        assert result is True
        assert planet.discovered_by == player_id
        assert planet.discovered_at is not None
        assert db.flush_calls == 1

    def test_already_discovered_is_a_no_op_and_returns_false(self):
        original_discoverer = uuid4()
        planet = _planet(discovered_by=original_discoverer)
        db = _FakeDb()

        result = mark_planet_discovered(db, planet, uuid4())

        assert result is False
        assert planet.discovered_by == original_discoverer
        assert planet.discovered_at is None
        assert db.flush_calls == 0


class TestMarkFeatureDiscovered:
    def test_new_insert_returns_true_and_flushes(self):
        db = _FakeDb(execute_rowcount=1)
        sector_uuid = uuid4()
        player_id = uuid4()

        result = mark_feature_discovered(db, sector_uuid, "nebula", player_id)

        assert result is True
        assert db.flush_calls == 1
        assert len(db.executed) == 1

    def test_conflicting_insert_returns_false(self):
        db = _FakeDb(execute_rowcount=0)

        result = mark_feature_discovered(db, uuid4(), "belt", uuid4())

        assert result is False
        assert db.flush_calls == 1

    def test_insert_values_carry_the_given_arguments(self):
        db = _FakeDb(execute_rowcount=1)
        sector_uuid = uuid4()
        player_id = uuid4()

        mark_feature_discovered(db, sector_uuid, "debris", player_id)

        stmt = db.executed[0]
        compiled_params = stmt.compile().params
        assert compiled_params["sector_uuid"] == sector_uuid
        assert compiled_params["feature_type"] == "debris"
        assert compiled_params["discovered_by"] == player_id
        assert compiled_params["discovered_at"] is not None
