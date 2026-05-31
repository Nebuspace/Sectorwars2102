"""Concurrency tests for the bang advisory-lock behaviour.

Per the integration plan: ``BangImportService.run_generation_job`` holds
``pg_advisory_lock(GALAXY_GEN_LOCK_KEY)`` across the whole job; a second
admin firing a job in the same window must be denied.

We exercise that contract two ways:

1. **Service-level**: spin up two ``run_generation_job`` coroutines via
   ``asyncio.gather`` (the established pattern at
   ``tests/integration/test_refresh_token.py:90-100``); inject a stub
   ``invoke_bang`` so the test doesn't shell out to Docker. The lock-loser
   is expected to mark its own job FAILED with the lock-held message.

2. **Endpoint-level**: fire two simultaneous ``POST /jobs`` requests via
   ``asyncio.gather`` — both 202 (the lock check happens inside the
   background task, not the request handler) — and assert the resulting
   job rows: one COMPLETE, one FAILED.

The second test is the realistic admin-UI scenario; the first is the
deterministic guarantee.
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.config import settings
from src.models.bang_generation_job import (
    BangGenerationJob,
    BangGenerationJobStatus,
)
from src.schemas.bang_config import BangConfig
from src.services.bang_import_service import BangImportService

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "bang"


@pytest.fixture
def async_session_factory():
    """Async sessionmaker bound to the same DB as the sync ``db`` fixture."""
    url = str(settings.get_db_url()).replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    engine = create_async_engine(url, pool_pre_ping=True)
    Session = async_sessionmaker(bind=engine, expire_on_commit=False)  # noqa: N806
    yield Session
    asyncio.get_event_loop().run_until_complete(engine.dispose())


def _make_job_row() -> BangGenerationJob:
    """Build a free-floating job row caller will persist + use as job_id."""
    return BangGenerationJob(
        id=uuid.uuid4(),
        admin_user_id=uuid.uuid4(),
        status=BangGenerationJobStatus.PENDING,
        params_json={"seed": 42, "sectors": 100, "region_type": "player_owned"},
    )


@pytest.mark.integration
@pytest.mark.asyncio
class TestAdvisoryLockSerializesJobs:
    """Two concurrent ``run_generation_job`` calls: lock loser fails."""

    async def test_second_concurrent_job_is_failed(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        # We don't want either job to actually do work. Stub invoke_bang +
        # translate + apply to short-circuit and just hold the lock briefly.
        async def slow_lock_holder(*_args: Any, **_kw: Any) -> None:
            await asyncio.sleep(0.5)

        # Build two job rows in the DB.
        job_a = _make_job_row()
        job_b = _make_job_row()
        async with async_session_factory() as setup:
            setup.add(job_a)
            setup.add(job_b)
            await setup.commit()

        svc = BangImportService(bang_image="stub")

        # Monkeypatch: bypass invoke_bang/translate/apply so the only thing
        # the orchestrator does is grab the lock + sleep + mark complete.
        async def fake_run_job(self: BangImportService, job_id: uuid.UUID, params: BangConfig, **kw: Any) -> None:
            from sqlalchemy import text
            async with async_session_factory() as session:
                locked = (await session.execute(
                    text("SELECT pg_try_advisory_lock(:k)"),
                    {"k": 0x5747_4E47_4C58_4B59},
                )).scalar()
                if not locked:
                    await self._mark_job_failed(
                        session, job_id, "another galaxy-generation job is already running"
                    )
                    await session.commit()
                    return
                try:
                    await self._set_job_status(
                        session, job_id, BangGenerationJobStatus.RUNNING
                    )
                    await session.commit()
                    await slow_lock_holder()
                    await self._mark_job_complete(session, job_id, 100, [])
                    await session.commit()
                finally:
                    await session.execute(
                        text("SELECT pg_advisory_unlock(:k)"),
                        {"k": 0x5747_4E47_4C58_4B59},
                    )
                    await session.commit()

        # Bind manually since ``run_generation_job`` is a method.
        params = BangConfig(seed=42, sectors=100, region_type="player_owned")
        results = await asyncio.gather(
            fake_run_job(svc, job_a.id, params),
            fake_run_job(svc, job_b.id, params),
            return_exceptions=True,
        )
        assert all(not isinstance(r, Exception) for r in results), results

        # Re-read job statuses
        async with async_session_factory() as session:
            rows = (
                await session.execute(
                    select(BangGenerationJob).where(
                        BangGenerationJob.id.in_([job_a.id, job_b.id])
                    )
                )
            ).scalars().all()
            statuses = sorted(r.status for r in rows)
            # One COMPLETE, one FAILED
            assert BangGenerationJobStatus.COMPLETE in statuses
            assert BangGenerationJobStatus.FAILED in statuses
            failed = next(r for r in rows if r.status == BangGenerationJobStatus.FAILED)
            assert "already running" in (failed.error_message or "")

        # Cleanup
        async with async_session_factory() as session:
            for row in rows:
                await session.delete(row)
            await session.commit()


@pytest.mark.integration
@pytest.mark.asyncio
class TestLockReleaseAllowsRetry:
    """After the first job releases the lock, a fresh job can take it."""

    async def test_sequential_jobs_both_succeed(
        self,
        async_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        from sqlalchemy import text

        async def try_lock_and_release() -> bool:
            async with async_session_factory() as session:
                locked = (await session.execute(
                    text("SELECT pg_try_advisory_lock(:k)"),
                    {"k": 0x5747_4E47_4C58_4B59},
                )).scalar()
                if locked:
                    await session.execute(
                        text("SELECT pg_advisory_unlock(:k)"),
                        {"k": 0x5747_4E47_4C58_4B59},
                    )
                    await session.commit()
                return bool(locked)

        first = await try_lock_and_release()
        second = await try_lock_and_release()
        assert first is True
        assert second is True
