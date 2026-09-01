"""LEG-3582 / LEG-3626 / LEG-3638 / LEG-3646 — admin_comprehensive HTTP 500 catches must not echo Exception text.

Mirrors LEG-3570 admin_colonization / LEG-3569 claim_ship / LEG-3605 admin_economy opaque densify.
Representative cluster: players/sectors/ports/planets/analytics/health/warp/teams.
Security cluster (LEG-3626): risk/status/logs/action routes.
Economy cluster (LEG-3638): analytics snapshot, port stock levels, AI trading admin routes.
Ship/player mutation cluster (LEG-3646): create/update/delete/teleport ship, update player.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import admin_comprehensive as ac
from src.api.routes.admin_comprehensive import (
    PlayerSecurityAction,
    PlayerUpdateRequest,
    ShipCreateRequest,
    ShipUpdateRequest,
    create_analytics_snapshot,
    create_ship,
    delete_ship,
    get_ai_models,
    get_ai_player_profiles,
    get_player_risk_assessment,
    get_player_security_status,
    get_players_comprehensive,
    list_player_security_logs,
    take_security_action,
    teleport_ship,
    update_all_port_stock_levels,
    update_player,
    update_ship,
)

_PLAYER_ID = "00000000-0000-0000-0000-000000000001"
_SHIP_ID = "00000000-0000-0000-0000-000000000002"


@contextmanager
def _noop_admin_action_attempt(*_args, **_kwargs):
    yield MagicMock()


class _BoomDB:
    def query(self, *args, **kwargs):
        raise RuntimeError("secret-admin-comp-query-should-not-leak")

    def rollback(self):
        pass


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


@pytest.mark.asyncio
async def test_create_analytics_snapshot_unexpected_is_opaque_500():
    """LEG-3638 — analytics snapshot catch must not echo raw Exception text."""
    secret = "secret-analytics-snapshot-should-not-leak"
    db = MagicMock()

    with patch.object(ac, "log_admin_action"):
        with patch.object(ac, "AnalyticsService") as svc_cls:
            svc_cls.return_value.create_analytics_snapshot.side_effect = RuntimeError(secret)
            with pytest.raises(HTTPException) as excinfo:
                await create_analytics_snapshot(
                    snapshot_type="manual",
                    current_admin=SimpleNamespace(username="admin"),
                    db=db,
                )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to create analytics snapshot"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_update_all_port_stock_levels_unexpected_is_opaque_500():
    """LEG-3638 — port stock-level update catch must not echo raw Exception text."""
    with pytest.raises(HTTPException) as excinfo:
        await update_all_port_stock_levels(
            current_admin=SimpleNamespace(username="admin"),
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to update port stock levels"
    assert "secret-admin-comp-query-should-not-leak" not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_ai_models_unexpected_is_opaque_500():
    """LEG-3638 — AI models catch must not echo raw Exception text."""
    secret = "secret-ai-models-should-not-leak"

    with patch.object(ac.logger, "info", side_effect=RuntimeError(secret)):
        with pytest.raises(HTTPException) as excinfo:
            await get_ai_models(
                current_admin=SimpleNamespace(username="admin"),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to get AI models"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_ai_player_profiles_unexpected_is_opaque_500():
    """LEG-3638 — AI player profiles catch must not echo raw Exception text."""
    with pytest.raises(HTTPException) as excinfo:
        await get_ai_player_profiles(
            current_admin=SimpleNamespace(username="admin"),
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to get AI player profiles"
    assert "secret-admin-comp-query-should-not-leak" not in str(exc.detail)


def test_admin_comprehensive_economy_cluster_http500_opaque():
    """LEG-3638 — static pin: economy cluster 500 details stay opaque."""
    src = Path(ac.__file__).read_text(encoding="utf-8")
    for stable in (
        'detail="Failed to create analytics snapshot"',
        'detail="Failed to update port stock levels"',
        'detail="Failed to get AI models"',
        'detail="Failed to get AI prediction accuracy"',
        'detail="Failed to get AI player profiles"',
        'detail="Failed to get AI system metrics"',
        'detail="Failed to get AI predictions"',
        'detail="Failed to get AI route optimization data"',
        'detail="Failed to get AI behavior analytics"',
    ):
        assert stable in src
    assert "Failed to create analytics snapshot: {str(e)}" not in src
    assert "Failed to update port stock levels: {str(e)}" not in src
    assert "Failed to get AI models: {str(e)}" not in src
    assert "Failed to get AI prediction accuracy: {str(e)}" not in src
    assert "Failed to get AI player profiles: {str(e)}" not in src
    assert "Failed to get AI system metrics: {str(e)}" not in src
    assert "Failed to get AI route optimization data: {str(e)}" not in src
    assert "Failed to get AI behavior analytics: {str(e)}" not in src


@pytest.mark.asyncio
async def test_update_player_unexpected_is_opaque_500():
    """LEG-3646 — update_player catch must not echo raw Exception text."""
    with pytest.raises(HTTPException) as excinfo:
        await update_player(
            player_id=_PLAYER_ID,
            update_data=PlayerUpdateRequest(credits=100),
            current_admin=SimpleNamespace(username="admin"),
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to update player"
    assert "secret-admin-comp-query-should-not-leak" not in str(exc.detail)


@pytest.mark.asyncio
async def test_create_ship_unexpected_is_opaque_500():
    """LEG-3646 — create_ship catch must not echo raw Exception text."""
    ship_data = ShipCreateRequest(
        name="Test Ship",
        ship_type="freighter",
        owner_id=_PLAYER_ID,
        current_sector_id=1,
    )

    with patch.object(ac, "admin_action_attempt", _noop_admin_action_attempt):
        with pytest.raises(HTTPException) as excinfo:
            await create_ship(
                ship_data=ship_data,
                current_admin=SimpleNamespace(username="admin"),
                db=_BoomDB(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to create ship"
    assert "secret-admin-comp-query-should-not-leak" not in str(exc.detail)


@pytest.mark.asyncio
async def test_update_ship_unexpected_is_opaque_500():
    """LEG-3646 — update_ship catch must not echo raw Exception text."""
    with patch.object(ac, "admin_action_attempt", _noop_admin_action_attempt):
        with pytest.raises(HTTPException) as excinfo:
            await update_ship(
                ship_id=_SHIP_ID,
                ship_data=ShipUpdateRequest(name="Renamed"),
                current_admin=SimpleNamespace(username="admin"),
                db=_BoomDB(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to update ship"
    assert "secret-admin-comp-query-should-not-leak" not in str(exc.detail)


@pytest.mark.asyncio
async def test_delete_ship_unexpected_is_opaque_500():
    """LEG-3646 — delete_ship catch must not echo raw Exception text."""
    with patch.object(ac, "admin_action_attempt", _noop_admin_action_attempt):
        with pytest.raises(HTTPException) as excinfo:
            await delete_ship(
                ship_id=_SHIP_ID,
                current_admin=SimpleNamespace(username="admin"),
                db=_BoomDB(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to delete ship"
    assert "secret-admin-comp-query-should-not-leak" not in str(exc.detail)


@pytest.mark.asyncio
async def test_teleport_ship_unexpected_is_opaque_500():
    """LEG-3646 — teleport_ship catch must not echo raw Exception text."""
    with patch.object(ac, "admin_action_attempt", _noop_admin_action_attempt):
        with pytest.raises(HTTPException) as excinfo:
            await teleport_ship(
                ship_id=_SHIP_ID,
                target_sector_id=42,
                current_admin=SimpleNamespace(username="admin"),
                db=_BoomDB(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to teleport ship"
    assert "secret-admin-comp-query-should-not-leak" not in str(exc.detail)


def test_admin_comprehensive_ship_player_mutation_cluster_http500_opaque():
    """LEG-3646 — static pin: ship/player mutation cluster 500 details stay opaque."""
    src = Path(ac.__file__).read_text(encoding="utf-8")
    for stable in (
        'detail="Failed to update player"',
        'detail="Failed to create ship"',
        'detail="Failed to update ship"',
        'detail="Failed to delete ship"',
        'detail="Failed to teleport ship"',
    ):
        assert stable in src
    assert "Failed to update player: {str(e)}" not in src
    assert "Failed to create ship: {str(e)}" not in src
    assert "Failed to update ship: {str(e)}" not in src
    assert "Failed to delete ship: {str(e)}" not in src
    assert "Failed to teleport ship: {str(e)}" not in src
