"""LEG-3874 — nexus unexpected failures return structured 500s."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import nexus as nexus_mod
from src.api.routes.nexus import (
    ERR_NEXUS_CLUSTER_DETAILS_FAILED,
    ERR_NEXUS_CLUSTERS_FAILED,
    ERR_NEXUS_GENERATE_FAILED,
    ERR_NEXUS_STATISTICS_FAILED,
    ERR_NEXUS_STATUS_FAILED,
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
async def test_generate_central_nexus_returns_structured_500():
    secret = "secret-nexus-generate-should-not-leak"
    with patch.object(nexus_mod, "log_admin_action"), patch.object(nexus_mod, "generate_nexus_task"):
        with pytest.raises(HTTPException) as excinfo:
            await generate_central_nexus(
                request=NexusGenerationRequest(),
                background_tasks=MagicMock(),
                current_admin=SimpleNamespace(id=uuid.uuid4()),
                session=_async_session_boom(secret),
                db=MagicMock(),
            )
    assert excinfo.value.detail == {"error_code": ERR_NEXUS_GENERATE_FAILED, "detail": "Failed to start generation"}
    assert secret not in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_get_nexus_status_returns_structured_500():
    secret = "secret-nexus-status-should-not-leak"
    with pytest.raises(HTTPException) as excinfo:
        await get_nexus_status(current_user=SimpleNamespace(id=uuid.uuid4()), session=_async_session_boom(secret))
    assert excinfo.value.detail == {"error_code": ERR_NEXUS_STATUS_FAILED, "detail": "Failed to get status"}


@pytest.mark.asyncio
async def test_get_nexus_statistics_returns_structured_500():
    secret = "secret-nexus-statistics-should-not-leak"
    with pytest.raises(HTTPException) as excinfo:
        await get_nexus_statistics(current_user=SimpleNamespace(id=uuid.uuid4()), session=_async_session_boom(secret))
    assert excinfo.value.detail == {"error_code": ERR_NEXUS_STATISTICS_FAILED, "detail": "Failed to get statistics"}


@pytest.mark.asyncio
async def test_get_clusters_info_returns_structured_500():
    secret = "secret-nexus-clusters-should-not-leak"
    with pytest.raises(HTTPException) as excinfo:
        await get_clusters_info(current_user=SimpleNamespace(id=uuid.uuid4()), session=_async_session_boom(secret))
    assert excinfo.value.detail == {"error_code": ERR_NEXUS_CLUSTERS_FAILED, "detail": "Failed to get clusters information"}


@pytest.mark.asyncio
async def test_get_cluster_details_returns_structured_500():
    secret = "secret-nexus-cluster-details-should-not-leak"
    with pytest.raises(HTTPException) as excinfo:
        await get_cluster_details(cluster_id=str(uuid.uuid4()), current_user=SimpleNamespace(id=uuid.uuid4()), session=_async_session_boom(secret))
    assert excinfo.value.detail == {"error_code": ERR_NEXUS_CLUSTER_DETAILS_FAILED, "detail": "Failed to get cluster details"}


def test_nexus_http500_catches_are_structured():
    src = Path(nexus_mod.__file__).read_text(encoding="utf-8")
    for code in (ERR_NEXUS_GENERATE_FAILED, ERR_NEXUS_STATUS_FAILED, ERR_NEXUS_STATISTICS_FAILED, ERR_NEXUS_CLUSTERS_FAILED, ERR_NEXUS_CLUSTER_DETAILS_FAILED):
        assert code in src
    assert src.count("route_internal_error(") >= 5
    assert 'detail="Failed to start generation"' not in src
