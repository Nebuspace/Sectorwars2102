"""LEG-3829 — regional_governance.py policy-create must not leak on 500s.

Mirrors LEG-3805 messages / LEG-3806 nexus opaque densify family.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import regional_governance as gov_mod
from src.api.routes.regional_governance import (
    PolicyCreate,
    create_policy_proposal_for_member,
)
from src.models.player import Player
from src.models.region import Region
from src.models.user import User
from src.services.regional_governance_service import RegionalGovernanceService


def _fake_region(owner_id=None) -> Region:
    return Region(
        id=uuid.uuid4(),
        name=f"region-{uuid.uuid4().hex[:8]}",
        display_name="Test Region",
        owner_id=owner_id or uuid.uuid4(),
        voting_threshold=Decimal("0.51"),
    )


def _fake_player() -> Player:
    return Player(id=uuid.uuid4(), user_id=uuid.uuid4())


def _fake_db_for_reads(region, player) -> MagicMock:
    fake_db = MagicMock()
    fake_db.scalar = AsyncMock(return_value=region)
    fake_db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=player))
    )
    return fake_db


class _FailingCommitDB:
    """Minimal AsyncSession stand-in: commit raises so the real service
    swallows the error and returns None."""

    def __init__(self, region: Region, player: Player, secret: str):
        self._region = region
        self._player = player
        self._secret = secret
        self.policies: list = []

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self.policies.append(obj)

    async def commit(self):
        raise RuntimeError(self._secret)

    async def rollback(self):
        return None

    async def refresh(self, obj):
        return None

    async def scalar(self, stmt):
        return self._region

    async def execute(self, stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = self._player
        result.scalars.return_value.all.return_value = list(self.policies)
        return result


@pytest.mark.asyncio
async def test_create_policy_proposal_for_member_service_failure_is_opaque_500():
    region = _fake_region()
    player = _fake_player()
    user = User(id=player.user_id)
    fake_db = _fake_db_for_reads(region, player)
    body = PolicyCreate(policy_type="tax_rate", title="X", proposed_changes={})

    with patch.object(
        RegionalGovernanceService,
        "get_membership_status",
        new=AsyncMock(return_value={"is_member": True, "can_vote": True}),
    ), patch.object(
        RegionalGovernanceService,
        "create_policy_proposal",
        new=AsyncMock(return_value=None),
    ), pytest.raises(HTTPException) as excinfo:
        await create_policy_proposal_for_member(region.id, body, user, fake_db)

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "ERR_POLICY_CREATE_FAILED"


@pytest.mark.asyncio
async def test_create_policy_proposal_for_member_runtime_error_secret_never_leaks():
    secret = "secret-policy-create-should-not-leak"
    region = _fake_region()
    player = _fake_player()
    user = User(id=player.user_id)
    fake_db = _FailingCommitDB(region, player, secret)
    body = PolicyCreate(
        policy_type="tax_rate",
        title="Lower taxes",
        proposed_changes={"tax_rate": 0.10},
    )

    with patch.object(
        RegionalGovernanceService,
        "get_membership_status",
        new=AsyncMock(return_value={"is_member": True, "can_vote": True}),
    ), pytest.raises(HTTPException) as excinfo:
        await create_policy_proposal_for_member(region.id, body, user, fake_db)

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "ERR_POLICY_CREATE_FAILED"
    assert secret not in str(exc.detail)


def test_regional_governance_policy_create_http500_is_opaque():
    """LEG-3829 — static pin: policy-create 500 detail stays opaque."""
    src = Path(gov_mod.__file__).read_text(encoding="utf-8")
    assert 'detail="ERR_POLICY_CREATE_FAILED"' in src
    assert "ERR_POLICY_CREATE_FAILED: {str(e)}" not in src
