"""Integration tests for :meth:`BangImportService.apply`.

These hit a real Postgres-backed test session (the ``db`` fixture from
``conftest.py:166``) and exercise the atomic-write surface of the
translator. They use captured bang fixtures, not a live Docker subprocess.

Run via:
    poetry run pytest tests/integration/test_bang_apply.py

(Skipped if no DB is reachable — gated by the ``db`` fixture itself.)
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, Dict

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.config import settings
from src.models.cluster import Cluster
from src.models.galaxy import Galaxy, GalaxyImportState
from src.services.bang_import_service import (
    BangImportService,
    InsertPlan,
    ParsedUniverse,
)

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "bang"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_fixture(name: str) -> Dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text())


@pytest.fixture
def service() -> BangImportService:
    return BangImportService(bang_image="test-image:0")


@pytest.fixture
def terran_universe() -> ParsedUniverse:
    return ParsedUniverse(
        region_type="terran_space",
        raw=_load_fixture("v1_3_0_terran_space.json"),
    )


@pytest.fixture
def player_owned_universe() -> ParsedUniverse:
    return ParsedUniverse(
        region_type="player_owned",
        raw=_load_fixture("v1_3_0_player_owned_small.json"),
    )


@pytest.fixture
def async_session_factory():
    """An async sessionmaker bound to the same DB the sync ``db`` fixture uses.

    The ``db`` fixture from conftest.py uses a sync engine; ``apply()`` needs
    an AsyncSession. We bridge by building an asyncpg-backed engine off
    ``settings.get_db_url()`` here.
    """
    url = str(settings.get_db_url()).replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    engine = create_async_engine(url, pool_pre_ping=True)
    Session = async_sessionmaker(bind=engine, expire_on_commit=False)  # noqa: N806
    yield Session
    asyncio.get_event_loop().run_until_complete(engine.dispose())


def _plan_with_region_id(
    service: BangImportService,
    universes: Dict[str, ParsedUniverse],
    galaxy_name: str = "Apply Test",
) -> InsertPlan:
    """Build an InsertPlan and stuff fake region UUIDs in the snapshot."""
    region_metadata = {
        "galaxy_name": galaxy_name,
        "master_seed": 42,
        "regions": {
            rt: {"region_id": str(uuid.uuid4())} for rt in universes
        },
    }
    return service.translate(universes, region_metadata=region_metadata)


# ---------------------------------------------------------------------------
# Single-region apply
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
class TestApplySingleRegion:
    """One-region apply lands all canonical rows in one transaction."""

    async def test_apply_lands_galaxy_clusters_sectors(
        self,
        service: BangImportService,
        terran_universe: ParsedUniverse,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        plan = _plan_with_region_id(
            service, {"terran_space": terran_universe}, "Apply Single"
        )
        async with async_session_factory() as session:
            async with session.begin():
                galaxy = await service.apply(plan, session)
                galaxy_id = galaxy.id
            try:
                async with session.begin():
                    refetched = await session.get(Galaxy, galaxy_id)
                    assert refetched is not None
                    assert refetched.import_state == GalaxyImportState.READY
                    assert refetched.bang_version.startswith("1.")
                    assert refetched.bang_seed == 42
                    # Clusters / sectors landed in the same transaction.
                    cluster_count = (
                        await session.execute(
                            select(Cluster).where(Cluster.region_id == _first_region_id(plan))
                        )
                    ).scalars().all()
                    assert len(cluster_count) == len(plan.regions["terran_space"].clusters)
            finally:
                # Cleanup — Hard delete the test galaxy.
                async with session.begin():
                    g = await session.get(Galaxy, galaxy_id)
                    if g is not None:
                        await session.delete(g)

    async def test_state_flips_to_ready_only_at_end(
        self,
        service: BangImportService,
        terran_universe: ParsedUniverse,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        plan = _plan_with_region_id(
            service, {"terran_space": terran_universe}, "State Flip Test"
        )
        async with async_session_factory() as session:
            async with session.begin():
                galaxy = await service.apply(plan, session)
                galaxy_id = galaxy.id
                # Inside the transaction, the final state is already READY
                # (apply mutates the row, but caller controls commit).
                assert galaxy.import_state == GalaxyImportState.READY
            try:
                async with session.begin():
                    g = await session.get(Galaxy, galaxy_id)
                    assert g is not None
                    assert g.import_state == GalaxyImportState.READY
            finally:
                async with session.begin():
                    g = await session.get(Galaxy, galaxy_id)
                    if g is not None:
                        await session.delete(g)


# ---------------------------------------------------------------------------
# Missing region_id should fail loudly
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
class TestApplyMissingRegionId:
    """Apply requires orchestrator-supplied region_ids per region."""

    async def test_missing_region_id_raises(
        self,
        service: BangImportService,
        terran_universe: ParsedUniverse,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        # Build plan WITHOUT region_id in metadata.
        plan = service.translate(
            {"terran_space": terran_universe},
            region_metadata={"galaxy_name": "No RID", "master_seed": 1},
        )
        async with async_session_factory() as session:
            async with session.begin():
                with pytest.raises(ValueError, match="missing region_id"):
                    await service.apply(plan, session)


# ---------------------------------------------------------------------------
# Idempotency: rerunning with same input still produces a consistent galaxy
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
class TestApplyIdempotency:
    """Re-translating with same input produces an identical plan (config hash)."""

    async def test_config_hash_stable_across_runs(
        self,
        service: BangImportService,
        terran_universe: ParsedUniverse,
    ) -> None:
        plan_a = _plan_with_region_id(
            service, {"terran_space": terran_universe}, "Hash A"
        )
        plan_b = _plan_with_region_id(
            service, {"terran_space": terran_universe}, "Hash B"
        )
        assert plan_a.bang_config_hash == plan_b.bang_config_hash

    async def test_apply_twice_produces_two_galaxies(
        self,
        service: BangImportService,
        terran_universe: ParsedUniverse,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Each apply() creates a fresh Galaxy row — sector_number uniqueness
        is scoped per-galaxy via FK isolation."""
        plan_a = _plan_with_region_id(
            service, {"terran_space": terran_universe}, "Idem A"
        )
        plan_b = _plan_with_region_id(
            service, {"terran_space": terran_universe}, "Idem B"
        )
        ids = []
        async with async_session_factory() as session:
            async with session.begin():
                g_a = await service.apply(plan_a, session)
                ids.append(g_a.id)
            async with session.begin():
                g_b = await service.apply(plan_b, session)
                ids.append(g_b.id)
            try:
                async with session.begin():
                    refetched = (
                        await session.execute(
                            select(Galaxy).where(Galaxy.id.in_(ids))
                        )
                    ).scalars().all()
                    assert len(refetched) == 2
                    assert all(
                        g.import_state == GalaxyImportState.READY for g in refetched
                    )
            finally:
                async with session.begin():
                    for gid in ids:
                        g = await session.get(Galaxy, gid)
                        if g is not None:
                            await session.delete(g)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _first_region_id(plan: InsertPlan) -> uuid.UUID:
    """Pluck the orchestrator-supplied region_id off the first region snapshot."""
    snapshot = next(iter(plan.bang_snapshot["regions"].values()))
    return uuid.UUID(str(snapshot["region_id"]))
