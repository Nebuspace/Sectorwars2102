"""LEG-3806 — nexus.py HTTP 500 catches must not echo Exception text.

Mirrors LEG-3794 planets / LEG-3796 regions opaque densify.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import nexus as nexus_mod
from src.api.routes.nexus import (
    NexusGenerationRequest,
    generate_central_nexus,
    get_cluster_details,
    get_clusters_info,
    get_nexus_statistics,
    get_nexus_status,
)


def _async_session_boom(secret: str) -> AsyncMock:
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=RuntimeError(secret))
    return session


@pytest.mark.asyncio
async def test_generate_central_nexus_unexpected_is_opaque_500():
    secret = "secret-nexus-generate-should-not-leak"
    request = NexusGenerationRequest()
    db = MagicMock()
    background_tasks = MagicMock()

    with patch.object(
        nexus_mod,
        "log_admin_action",
    ), patch.object(
        nexus_mod,
        "generate_nexus_task",
    ):
        with pytest.raises(HTTPException) as excinfo:
            await generate_central_nexus(
                request=request,
                background_tasks=background_tasks,
                current_admin=SimpleNamespace(id=uuid.uuid4()),
                session=_async_session_boom(secret),
                db=db,
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to start generation"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_nexus_status_unexpected_is_opaque_500():
    secret = "secret-nexus-status-should-not-leak"

    with pytest.raises(HTTPException) as excinfo:
        await get_nexus_status(
            current_user=SimpleNamespace(id=uuid.uuid4()),
            session=_async_session_boom(secret),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to get status"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_nexus_statistics_unexpected_is_opaque_500():
    secret = "secret-nexus-statistics-should-not-leak"

    with pytest.raises(HTTPException) as excinfo:
        await get_nexus_statistics(
            current_user=SimpleNamespace(id=uuid.uuid4()),
            session=_async_session_boom(secret),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to get statistics"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_clusters_info_unexpected_is_opaque_500():
    secret = "secret-nexus-clusters-should-not-leak"

    with pytest.raises(HTTPException) as excinfo:
        await get_clusters_info(
            current_user=SimpleNamespace(id=uuid.uuid4()),
            session=_async_session_boom(secret),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to get clusters information"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_cluster_details_unexpected_is_opaque_500():
    secret = "secret-nexus-cluster-details-should-not-leak"

    with pytest.raises(HTTPException) as excinfo:
        await get_cluster_details(
            cluster_id=str(uuid.uuid4()),
            current_user=SimpleNamespace(id=uuid.uuid4()),
            session=_async_session_boom(secret),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to get cluster details"
    assert secret not in str(exc.detail)


def test_nexus_http500_catches_have_no_detail_str_e():
    """LEG-3806 — static pin: all five HTTP 500 catch paths stay opaque."""
    src = Path(nexus_mod.__file__).read_text(encoding="utf-8")
    for stable in (
        'detail="Failed to start generation"',
        'detail="Failed to get status"',
        'detail="Failed to get statistics"',
        'detail="Failed to get clusters information"',
        'detail="Failed to get cluster details"',
    ):
        assert stable in src
    assert "Failed to start generation: {str(e)}" not in src
    assert "Failed to get status: {str(e)}" not in src
    assert "Failed to get statistics: {str(e)}" not in src
    assert "Failed to get clusters information: {str(e)}" not in src
    assert "Failed to get cluster details: {str(e)}" not in src
