"""C3 call-site fix — async websocket perms must not read User.is_admin.

Hub-cipher CRITICAL: session-attached hybrid getter runs sync Session.query
on AsyncSession → MissingGreenlet → except swallows → every connect gets
only ``{trading}``. Fix is async AdminScopeGrant lookup at the call site.
"""

from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.enhanced_websocket_service import EnhancedWebSocketService


def test_load_player_permissions_source_avoids_hybrid_is_admin_read():
    src = inspect.getsource(EnhancedWebSocketService._load_player_permissions)
    assert "user.is_admin" not in src
    assert "AdminScopeGrant" in src
    assert "await db.execute" in src


@pytest.mark.asyncio
async def test_load_player_permissions_admin_gets_admin_automation_ai_access():
    """Exact gap cipher named: admin connect must not collapse to {trading}."""
    player_id = str(uuid.uuid4())
    user_id = uuid.uuid4()
    player = SimpleNamespace(
        id=player_id,
        is_active=True,
        is_galactic_citizen=False,
    )
    user = SimpleNamespace(id=user_id, _is_admin=True)

    player_user_result = MagicMock()
    player_user_result.first.return_value = (player, user)

    grant_result = MagicMock()
    grant_result.first.return_value = (uuid.uuid4(),)  # any active grant row

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[player_user_result, grant_result])

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=None)

    svc = EnhancedWebSocketService(redis_client=None)
    with patch(
        "src.services.enhanced_websocket_service.AsyncSessionLocal",
        return_value=cm,
    ):
        perms = await svc._load_player_permissions(player_id)

    assert "trading" in perms
    assert "admin" in perms
    assert "automation" in perms
    assert "ai_access" in perms
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_load_player_permissions_no_grant_skips_admin_flags():
    player_id = str(uuid.uuid4())
    player = SimpleNamespace(
        id=player_id,
        is_active=True,
        is_galactic_citizen=False,
    )
    user = SimpleNamespace(id=uuid.uuid4(), _is_admin=True)  # phantom flat

    player_user_result = MagicMock()
    player_user_result.first.return_value = (player, user)

    grant_result = MagicMock()
    grant_result.first.return_value = None

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[player_user_result, grant_result])

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=None)

    svc = EnhancedWebSocketService(redis_client=None)
    with patch(
        "src.services.enhanced_websocket_service.AsyncSessionLocal",
        return_value=cm,
    ):
        perms = await svc._load_player_permissions(player_id)

    assert perms == {"trading", "ai_access"}
