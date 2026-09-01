"""LEG-3582 / LEG-3626 — admin_comprehensive HTTP 500 catches must not echo Exception text.

Mirrors LEG-3570 admin_colonization / LEG-3569 claim_ship / LEG-3605 admin_economy opaque densify.
Representative cluster: players/sectors/ports/planets/analytics/health/warp/teams.
Security cluster (LEG-3626): risk/status/logs/action routes.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import admin_comprehensive as ac
from src.api.routes.admin_comprehensive import (
    PlayerSecurityAction,
    get_player_risk_assessment,
    get_player_security_status,
    get_players_comprehensive,
    list_player_security_logs,
    take_security_action,
)

_PLAYER_ID = "00000000-0000-0000-0000-000000000001"


def _run(coro):
    import asyncio

    return asyncio.run(coro)


class _BoomDB:
    def query(self, *args, **kwargs):
        raise RuntimeError("secret-admin-comp-query-should-not-leak")


@pytest.mark.asyncio
async def test_get_players_comprehensive_unexpected_is_opaque_500():
    """LEG-3582 — players catch must not echo raw Exception text."""
    with pytest.raises(HTTPException) as excinfo:
        await get_players_comprehensive(
            current_admin=SimpleNamespace(username="admin"),
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to fetch players"
    assert "secret-admin-comp-query-should-not-leak" not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_player_risk_assessment_unexpected_is_opaque_500():
    """LEG-3626 — risk assessment catch must not echo raw Exception text."""
    secret = "secret-risk-assessment-should-not-leak"

    with patch.object(ac, "get_security_service") as svc_cls:
        svc_cls.return_value.get_player_risk_assessment.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await get_player_risk_assessment(
                player_id=_PLAYER_ID,
                current_admin=SimpleNamespace(username="admin"),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to get player risk assessment"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_player_security_status_unexpected_is_opaque_500():
    """LEG-3626 — security status catch must not echo raw Exception text."""
    secret = "secret-security-status-should-not-leak"

    with patch.object(ac, "get_security_service") as svc_cls:
        svc_cls.return_value.get_player_security_status.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await get_player_security_status(
                player_id=_PLAYER_ID,
                current_admin=SimpleNamespace(username="admin"),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to get player security status"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_list_player_security_logs_unexpected_is_opaque_500():
    """LEG-3626 — security logs catch must not echo raw Exception text."""
    secret = "secret-security-logs-should-not-leak"
    db = MagicMock()
    db.query.side_effect = RuntimeError(secret)

    with pytest.raises(HTTPException) as excinfo:
        await list_player_security_logs(
            player_id=_PLAYER_ID,
            current_admin=SimpleNamespace(username="admin"),
            db=db,
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to list player security logs"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_take_security_action_unexpected_is_opaque_500():
    """LEG-3626 — take_security_action catch must not echo raw Exception text."""
    secret = "secret-security-action-should-not-leak"
    player_row = SimpleNamespace(id=_PLAYER_ID)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = player_row

    with patch.object(ac, "get_security_service") as svc_cls:
        svc_cls.return_value.get_or_create_player_profile.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await take_security_action(
                player_id=_PLAYER_ID,
                action=PlayerSecurityAction(action="unblock"),
                current_admin=SimpleNamespace(username="admin"),
                db=db,
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to take security action"
    assert secret not in str(exc.detail)


def test_admin_comprehensive_representative_cluster_http500_opaque():
    """LEG-3582 — static pin: representative GET cluster 500 details stay opaque."""
    src = Path(ac.__file__).read_text(encoding="utf-8")
    for stable in (
        'detail="Failed to fetch players"',
        'detail="Failed to fetch sectors"',
        'detail="Failed to fetch ports"',
        'detail="Failed to fetch planets"',
        'detail="Failed to fetch analytics"',
        'detail="Failed to get system health"',
        'detail="Failed to fetch warp tunnels"',
        'detail="Failed to fetch teams"',
    ):
        assert stable in src
    assert "Failed to fetch players: {str(e)}" not in src
    assert "Failed to fetch sectors: {str(e)}" not in src
    assert "Failed to fetch ports: {str(e)}" not in src
    assert "Failed to fetch planets: {str(e)}" not in src
    assert "Failed to get system health: {str(e)}" not in src
    assert "Failed to fetch warp tunnels: {str(e)}" not in src
    assert "Failed to fetch teams: {str(e)}" not in src


def test_admin_comprehensive_security_cluster_http500_opaque():
    """LEG-3626 — static pin: security cluster 500 details stay opaque."""
    src = Path(ac.__file__).read_text(encoding="utf-8")
    for stable in (
        'detail="Failed to generate security report"',
        'detail="Failed to get security alerts"',
        'detail="Failed to get player risk assessment"',
        'detail="Failed to get player security status"',
        'detail="Failed to list player security logs"',
        'detail="Failed to clean up security data"',
        'detail="Failed to take security action"',
    ):
        assert stable in src
    assert "Failed to generate security report: {str(e)}" not in src
    assert "Failed to get security alerts: {str(e)}" not in src
    assert "Failed to get player risk assessment: {str(e)}" not in src
    assert "Failed to get player security status: {str(e)}" not in src
    assert "Failed to list player security logs: {str(e)}" not in src
    assert "Failed to clean up security data: {str(e)}" not in src
    assert "Failed to take security action: {str(e)}" not in src
