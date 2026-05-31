"""Orphan recovery tests for the bang startup hook.

The hook at ``services/gameserver/src/main.py`` (the `@app.on_event("startup")`
block around line 167) sweeps ``bang_generation_jobs`` for any row stuck
in ``RUNNING`` with ``started_at`` more than 5 minutes ago and flips it to
``FAILED`` with ``error_message='orphaned at startup'``.

We test the underlying recovery query as a unit (replicating the same
``UPDATE … WHERE status=RUNNING AND started_at < now() - interval 5min``
without spinning up the whole FastAPI app's lifespan). The test:

1. Inserts a RUNNING job with started_at = now() - 10 minutes
2. Inserts a fresh RUNNING job with started_at = now() (not orphaned)
3. Runs the recovery UPDATE
4. Asserts only the stale row was marked FAILED
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func as sa_func
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.config import settings
from src.models.bang_generation_job import (
    BangGenerationJob,
    BangGenerationJobStatus,
)


@pytest.fixture
def async_session_factory():
    url = str(settings.get_db_url()).replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    engine = create_async_engine(url, pool_pre_ping=True)
    Session = async_sessionmaker(bind=engine, expire_on_commit=False)  # noqa: N806
    yield Session
    asyncio.get_event_loop().run_until_complete(engine.dispose())


async def _run_orphan_recovery(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Same UPDATE the startup hook runs, returned rowcount."""
    async with session_factory() as session:
        stmt = (
            update(BangGenerationJob)
            .where(BangGenerationJob.status == BangGenerationJobStatus.RUNNING)
            .where(
                BangGenerationJob.started_at
                < sa_func.now() - timedelta(minutes=5)
            )
            .values(
                status=BangGenerationJobStatus.FAILED,
                error_message="orphaned at startup",
                completed_at=sa_func.now(),
            )
        )
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount or 0


@pytest.mark.integration
@pytest.mark.asyncio
class TestOrphanRecovery:
    """The startup sweep flips stale RUNNING jobs to FAILED."""

    async def test_stale_job_marked_failed(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        ten_min_ago = datetime.now(timezone.utc) - timedelta(minutes=10)
        stale = BangGenerationJob(
            id=uuid.uuid4(),
            admin_user_id=uuid.uuid4(),
            status=BangGenerationJobStatus.RUNNING,
            params_json={"seed": 1, "sectors": 100, "region_type": "player_owned"},
            started_at=ten_min_ago,
        )
        fresh = BangGenerationJob(
            id=uuid.uuid4(),
            admin_user_id=uuid.uuid4(),
            status=BangGenerationJobStatus.RUNNING,
            params_json={"seed": 2, "sectors": 100, "region_type": "player_owned"},
            started_at=datetime.now(timezone.utc),
        )
        async with async_session_factory() as session:
            session.add(stale)
            session.add(fresh)
            await session.commit()

        recovered = await _run_orphan_recovery(async_session_factory)
        assert recovered >= 1

        async with async_session_factory() as session:
            refetched_stale = await session.get(BangGenerationJob, stale.id)
            refetched_fresh = await session.get(BangGenerationJob, fresh.id)
            assert refetched_stale is not None
            assert refetched_fresh is not None
            assert refetched_stale.status == BangGenerationJobStatus.FAILED
            assert refetched_stale.error_message == "orphaned at startup"
            assert refetched_stale.completed_at is not None
            # The fresh job is left alone.
            assert refetched_fresh.status == BangGenerationJobStatus.RUNNING
            assert refetched_fresh.error_message is None

            # Cleanup
            await session.delete(refetched_stale)
            await session.delete(refetched_fresh)
            await session.commit()

    async def test_completed_jobs_untouched(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Even a very-old COMPLETE job stays COMPLETE — the filter is on status."""
        old_complete = BangGenerationJob(
            id=uuid.uuid4(),
            admin_user_id=uuid.uuid4(),
            status=BangGenerationJobStatus.COMPLETE,
            params_json={"seed": 3, "sectors": 100, "region_type": "player_owned"},
            started_at=datetime.now(timezone.utc) - timedelta(hours=1),
            completed_at=datetime.now(timezone.utc) - timedelta(minutes=58),
        )
        async with async_session_factory() as session:
            session.add(old_complete)
            await session.commit()

        await _run_orphan_recovery(async_session_factory)

        async with async_session_factory() as session:
            row = await session.get(BangGenerationJob, old_complete.id)
            assert row is not None
            assert row.status == BangGenerationJobStatus.COMPLETE
            await session.delete(row)
            await session.commit()
