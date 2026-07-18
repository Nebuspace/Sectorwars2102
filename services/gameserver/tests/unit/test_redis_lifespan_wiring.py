"""WO-SWEEP-REDIS-LIFESPAN: pin Redis connect/disconnect wiring into
main.py's lifespan.

RedisService.connect()/disconnect() (WO-SWEEP-AIOREDIS-PY312) were fixed to
import cleanly on Python 3.12, but were never actually invoked anywhere --
main.py's lifespan handler never called init_redis()/close_redis(), so
redis_pool stayed None for the lifetime of every running process and the
entire Redis-backed subsystem (activity tracking, pub/sub, caching) silently
no-op'd even after the import fix. This suite pins the fix: connect() is
called on startup, disconnect() on shutdown, and a failed connection must
not prevent the gameserver from starting.

Driving the REAL lifespan() end-to-end (rather than source-grepping) is
deliberate but requires mocking every OTHER startup step first --
unmocked, create_default_admin/create_default_factions alone add ~12s of
real retry-with-backoff sleeps against the fake test DATABASE_URL (verified
empirically), and the async schema-init/orphan-recovery DB calls add several
more seconds on top. `_patched_lifespan_deps()` mocks every step except the
Redis wiring under test, so this suite can drive the real, unmocked async
generator fast (~5ms) and deterministically while only Redis varies between
tests.
"""
from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.core.database as db_mod
import src.main as main_mod
import src.services.redis_service as redis_service_module
from src.services.redis_service import RedisService


@contextlib.contextmanager
def _patched_lifespan_deps():
    """Mocks every DB-touching / retry-heavy startup step EXCEPT the Redis
    connect/disconnect calls under test, so lifespan() can be driven for
    real without a live Postgres and without the admin-bootstrap retry
    backoff. Callers patch `src.services.redis_service.init_redis` /
    `close_redis` themselves to control the scenario under test."""
    fake_engine_ctx = AsyncMock()
    fake_engine_ctx.__aenter__.return_value = AsyncMock()
    fake_engine_ctx.__aexit__.return_value = False
    fake_async_engine = MagicMock()
    fake_async_engine.begin = MagicMock(return_value=fake_engine_ctx)

    fake_session = AsyncMock()
    fake_result = MagicMock()
    fake_result.rowcount = 0
    fake_session.execute = AsyncMock(return_value=fake_result)
    fake_session_ctx = AsyncMock()
    fake_session_ctx.__aenter__.return_value = fake_session
    fake_session_ctx.__aexit__.return_value = False
    fake_async_session_local = MagicMock(return_value=fake_session_ctx)

    fake_translation_instance = MagicMock()
    fake_translation_instance.initialize_default_data = AsyncMock()
    fake_translation_instance.bulk_import_translations = AsyncMock(
        return_value={"imported": 0, "skipped": 0, "errors": 0}
    )

    with patch.object(main_mod, "async_engine", fake_async_engine), \
            patch.object(db_mod, "AsyncSessionLocal", fake_async_session_local), \
            patch("src.auth.admin.create_default_admin", new=MagicMock()), \
            patch("src.auth.admin.create_default_factions", new=MagicMock()), \
            patch(
                "src.services.haggle_service.seed_trader_personalities",
                new=MagicMock(return_value={"scanned": 0, "reseeded": 0}),
            ), \
            patch(
                "src.services.translation_service.TranslationService",
                new=MagicMock(return_value=fake_translation_instance),
            ), \
            patch(
                "src.core.ship_specifications_seeder.seed_ship_specifications",
                new=MagicMock(),
            ), \
            patch("src.services.medal_catalog.seed_medals", new=MagicMock()), \
            patch(
                "src.core.resource_registry_seeder.seed_resource_registry",
                new=MagicMock(),
            ):
        yield


class TestLifespanCallsInitRedisOnStartup:
    @pytest.mark.asyncio
    async def test_startup_calls_init_redis(self, caplog) -> None:
        fake_init_redis = AsyncMock()
        with _patched_lifespan_deps(), \
                patch("src.services.redis_service.init_redis", new=fake_init_redis), \
                patch("src.services.redis_service.close_redis", new=AsyncMock()), \
                caplog.at_level("INFO", logger="src.main"):
            async with main_mod.lifespan(None):
                pass

        fake_init_redis.assert_awaited_once()
        assert any(
            "Redis service connected" in r.getMessage() for r in caplog.records
        )
        assert any(
            "started successfully" in r.getMessage() for r in caplog.records
        )


class TestConnectFailureDegradesGracefully:
    @pytest.mark.asyncio
    async def test_app_still_starts_and_warns_when_connect_fails(self, caplog) -> None:
        fake_init_redis = AsyncMock(side_effect=ConnectionRefusedError("no redis"))
        with _patched_lifespan_deps(), \
                patch("src.services.redis_service.init_redis", new=fake_init_redis), \
                patch("src.services.redis_service.close_redis", new=AsyncMock()), \
                caplog.at_level("INFO", logger="src.main"):
            # Must not raise -- that's the whole point of this test: a
            # failed Redis connection cannot prevent the app from starting.
            async with main_mod.lifespan(None):
                pass

        fake_init_redis.assert_awaited_once()
        assert any(
            "Redis connection failed at startup" in r.getMessage()
            and "non-fatal" in r.getMessage()
            for r in caplog.records
        )
        assert any(
            "started successfully" in r.getMessage() for r in caplog.records
        ), "startup must reach its normal completion log even when Redis is unreachable"

    @pytest.mark.asyncio
    async def test_redis_pool_stays_none_when_from_url_itself_fails(self) -> None:
        """Isolated, non-global-state proof of the 'pool stays None' half of
        the contract. RedisService.connect() assigns self.redis_pool from
        aioredis.from_url()'s return value BEFORE awaiting ping() -- so a
        failure at from_url() itself (bad URL, DNS failure, refused at the
        socket layer) never reaches that assignment, and redis_pool is
        provably still None afterward. Uses a fresh RedisService(), not the
        process-wide redis_service singleton main.py actually calls, so this
        can't leak state into or depend on ordering with other test modules
        under pytest-randomly."""
        service = RedisService()
        assert service.redis_pool is None  # precondition

        with patch.object(redis_service_module, "aioredis") as mock_aioredis:
            mock_aioredis.from_url = MagicMock(
                side_effect=ConnectionRefusedError("bad redis url")
            )
            with pytest.raises(ConnectionRefusedError):
                await service.connect()

        assert service.redis_pool is None


class TestLifespanCallsCloseRedisOnShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_calls_close_redis(self, caplog) -> None:
        fake_close_redis = AsyncMock()
        with _patched_lifespan_deps(), \
                patch("src.services.redis_service.init_redis", new=AsyncMock()), \
                patch("src.services.redis_service.close_redis", new=fake_close_redis), \
                caplog.at_level("INFO", logger="src.main"):
            async with main_mod.lifespan(None):
                # Still inside startup/yield -- shutdown must not have run yet.
                fake_close_redis.assert_not_awaited()

        fake_close_redis.assert_awaited_once()
        assert any(
            "Redis service disconnected" in r.getMessage() for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_shutdown_close_failure_does_not_propagate(self, caplog) -> None:
        """Mirrors the startup contract: disconnect() is best-effort too --
        a shutdown-time Redis failure must not raise out of the lifespan
        context manager, which would abort the rest of app teardown."""
        fake_close_redis = AsyncMock(side_effect=RuntimeError("pool already closed"))
        with _patched_lifespan_deps(), \
                patch("src.services.redis_service.init_redis", new=AsyncMock()), \
                patch("src.services.redis_service.close_redis", new=fake_close_redis), \
                caplog.at_level("WARNING", logger="src.main"):
            async with main_mod.lifespan(None):
                pass  # must not raise on the way out either

        fake_close_redis.assert_awaited_once()
        assert any(
            "Redis disconnect failed during shutdown" in r.getMessage()
            for r in caplog.records
        )
