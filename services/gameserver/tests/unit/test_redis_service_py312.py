"""WO-SWEEP-AIOREDIS-PY312: pin redis_service.py off the dead aioredis package.

`aioredis` is archived upstream and unimportable on Python 3.12+ (its import
chain reaches for `distutils`, removed from the stdlib in 3.12) -- this is
what made `_track_player_login`'s activity tracking silently dead (caught by
`auth.py`'s blanket `except Exception:`, so login itself still 200'd).
redis-py >=4.2 absorbed the aioredis 2.x codebase as `redis.asyncio`, already
present in this repo's dependencies (`redis = {extras = ["hiredis"], version
= ">=5.0.1,<9.0.0"}` in pyproject.toml) -- this fix is an import swap, not a
new dependency. `aioredis` itself is still pinned in pyproject.toml/
poetry.lock, unused; pruning it needs a Max-blessed lockfile regen, not
done here.

A second, previously-masked latent bug in the same file is also pinned here:
`from core.config import settings` (missing the `src.` prefix) would have
raised `ModuleNotFoundError: No module named 'core'` the moment the aioredis
import stopped shadowing it -- confirmed by stubbing aioredis out and
re-importing before this fix existed.
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.services.redis_service as redis_service_module
from src.services.redis_service import RedisService


class TestNoDeadAioredisPackageImport:
    """Source-level pin, not just a runtime happy-path check: the dead
    `aioredis` package must never be imported directly, and the settings
    import must use the full `src.` path."""

    def test_source_has_no_direct_aioredis_package_import(self) -> None:
        source = inspect.getsource(redis_service_module)
        assert "import aioredis\n" not in source
        assert "from redis import asyncio as aioredis" in source

    def test_settings_import_uses_full_src_path(self) -> None:
        source = inspect.getsource(redis_service_module)
        assert "from src.core.config import settings" in source
        assert "from core.config import settings" not in source

    def test_module_imports_with_no_exception(self) -> None:
        """The real regression: importing redis_service.py used to raise
        ModuleNotFoundError (distutils, then -- once that was patched over --
        'core') before ever reaching the class definition. Reaching this
        assertion at all already proves the import succeeded; this makes
        that explicit rather than relying on collection not exploding."""
        assert RedisService is not None
        assert inspect.isclass(RedisService)

    def test_aioredis_name_resolves_to_redis_asyncio_not_the_archived_package(
        self,
    ) -> None:
        assert redis_service_module.aioredis.__name__ == "redis.asyncio"


class TestConnectUsesRedisAsyncio:
    """Behavior pin: connect() drives the redis.asyncio API surface with the
    exact same call shape it used to drive aioredis with -- the swap must be
    behavior-preserving, not just import-clean."""

    @pytest.mark.asyncio
    async def test_connect_success_builds_pool_and_sync_client(self) -> None:
        service = RedisService()
        fake_pool = AsyncMock()
        fake_pool.ping = AsyncMock(return_value=True)

        with patch.object(redis_service_module, "aioredis") as mock_aioredis, \
                patch.object(redis_service_module, "redis") as mock_redis:
            mock_aioredis.from_url = MagicMock(return_value=fake_pool)
            mock_redis.from_url = MagicMock(return_value=MagicMock())

            await service.connect()

            mock_aioredis.from_url.assert_called_once_with(
                redis_service_module.settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                max_connections=20,
            )
            fake_pool.ping.assert_awaited_once()
            assert service.redis_pool is fake_pool
            assert service.sync_redis is not None

    @pytest.mark.asyncio
    async def test_connect_failure_still_propagates_unchanged(self) -> None:
        """Pre-existing degrade contract: connect() logs then re-raises on
        failure -- callers (e.g. auth.py's _track_player_login) are what
        swallow it, not this layer. Must still hold true post-swap so the
        rest of the app's graceful-degrade behavior is unchanged."""
        service = RedisService()
        fake_pool = AsyncMock()
        fake_pool.ping = AsyncMock(side_effect=ConnectionRefusedError("no redis"))

        with patch.object(redis_service_module, "aioredis") as mock_aioredis:
            mock_aioredis.from_url = MagicMock(return_value=fake_pool)

            with pytest.raises(ConnectionRefusedError):
                await service.connect()

    @pytest.mark.asyncio
    async def test_disconnect_closes_pool_without_error(self) -> None:
        service = RedisService()
        service.redis_pool = AsyncMock()
        service.sync_redis = MagicMock()

        await service.disconnect()

        service.redis_pool.close.assert_awaited_once()
        service.sync_redis.close.assert_called_once()
