"""Unit tests for ADR-0050 SK17 -- per-phase row-count sentinels.

DB-free: exercises the pure ``_record_phase_checksum`` helper directly and
``verify_region_phase_checksums`` against a hand-rolled FakeSession that
returns canned COUNT-query results, mirroring the project's established
fake-session pattern for query-shape tests (no live Postgres needed).
"""
from __future__ import annotations

import time
import uuid
from typing import Any, List

import pytest

from src.models.region import Region
from src.services.bang_import_service import (
    _record_phase_checksum,
    verify_region_phase_checksums,
)

# ---------------------------------------------------------------------------
# _record_phase_checksum
# ---------------------------------------------------------------------------


def _region(checksums=None):
    # A real (unpersisted) SQLAlchemy-mapped instance -- _record_phase_checksum
    # calls flag_modified(), which requires _sa_instance_state and so cannot
    # target a plain SimpleNamespace stand-in.
    region = Region(id=uuid.uuid4())
    region.generation_phase_checksums = checksums
    return region


def test_record_phase_checksum_writes_shape():
    region = _region()
    t0 = time.monotonic()
    _record_phase_checksum(region, "clusters", 4, t0)

    assert "clusters" in region.generation_phase_checksums
    entry = region.generation_phase_checksums["clusters"]
    assert entry["row_count"] == 4
    assert isinstance(entry["duration_ms"], int)
    assert entry["duration_ms"] >= 0
    assert isinstance(entry["completed_at"], str)
    assert "T" in entry["completed_at"]  # ISO 8601


def test_record_phase_checksum_merges_does_not_clobber_prior_phases():
    region = _region(checksums={"clusters": {"row_count": 4, "duration_ms": 1, "completed_at": "x"}})
    _record_phase_checksum(region, "sectors", 850, time.monotonic())

    assert region.generation_phase_checksums["clusters"]["row_count"] == 4
    assert region.generation_phase_checksums["sectors"]["row_count"] == 850


def test_record_phase_checksum_overwrites_same_phase_on_rerun():
    region = _region(checksums={"clusters": {"row_count": 1, "duration_ms": 1, "completed_at": "x"}})
    _record_phase_checksum(region, "clusters", 4, time.monotonic())

    assert region.generation_phase_checksums["clusters"]["row_count"] == 4


def test_record_phase_checksum_noop_on_none_region():
    # Must not raise -- defensive no-op per the docstring.
    _record_phase_checksum(None, "clusters", 4, time.monotonic())


# ---------------------------------------------------------------------------
# verify_region_phase_checksums
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class _FakeSession:
    """Returns queued scalar results in call order; records call count."""

    def __init__(self, values: List[int]) -> None:
        self._values = list(values)
        self.calls = 0

    async def execute(self, _stmt: Any) -> _FakeResult:
        self.calls += 1
        return _FakeResult(self._values.pop(0))


@pytest.mark.asyncio
async def test_verify_no_stored_checksums_short_circuits():
    region = _region(checksums={})
    session = _FakeSession([])

    mismatches = await verify_region_phase_checksums(session, region)

    assert mismatches == {}
    assert session.calls == 0


@pytest.mark.asyncio
async def test_verify_all_match_returns_empty():
    region = _region(
        checksums={
            "clusters": {"row_count": 4, "duration_ms": 1, "completed_at": "x"},
            "sectors": {"row_count": 850, "duration_ms": 2, "completed_at": "x"},
        }
    )
    # _SK17_PHASE_MODELS iteration order: clusters, sectors, stations,
    # planets, formations (warps handled separately after). Only clusters
    # and sectors are present in `stored`, so exactly 2 queries fire, live
    # counts matching stored.
    session = _FakeSession([4, 850])

    mismatches = await verify_region_phase_checksums(session, region)

    assert mismatches == {}
    assert session.calls == 2


@pytest.mark.asyncio
async def test_verify_detects_mismatch():
    region = _region(
        checksums={
            "clusters": {"row_count": 4, "duration_ms": 1, "completed_at": "x"},
        }
    )
    session = _FakeSession([3])  # live count disagrees with stored 4

    mismatches = await verify_region_phase_checksums(session, region)

    assert mismatches == {"clusters": {"stored": 4, "live": 3}}


@pytest.mark.asyncio
async def test_verify_detects_warp_mismatch():
    region = _region(
        checksums={
            "warps": {"row_count": 1200, "duration_ms": 1, "completed_at": "x"},
        }
    )
    session = _FakeSession([1199])

    mismatches = await verify_region_phase_checksums(session, region)

    assert mismatches == {"warps": {"stored": 1200, "live": 1199}}


@pytest.mark.asyncio
async def test_verify_only_queries_stored_phases():
    # Region only ever recorded "planets" -- verify must not query the
    # other 5 phase models at all.
    region = _region(
        checksums={"planets": {"row_count": 12, "duration_ms": 1, "completed_at": "x"}}
    )
    session = _FakeSession([12])

    mismatches = await verify_region_phase_checksums(session, region)

    assert mismatches == {}
    assert session.calls == 1
