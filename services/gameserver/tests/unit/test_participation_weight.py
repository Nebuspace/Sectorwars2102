"""WO-P7-MULTIACCT-PARTICIPATION-WEIGHT + LEG-256 soft-tier / all-paid.

Accept proof:
- one shared implementation (multi_account_service)
- beacon + governance both consume it
- HARD-flagged cluster member loses beacon count AND loses the vote
- SOFT → 0.5; all_paid_subscribers → 1.0; HARD → 0.0 (non-exempt)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import AsyncMock

import pytest

from src.models.multi_account import (
    MultiAccountCluster,
    MultiAccountFlag,
    MultiAccountSeverity,
)
from src.services import multi_account_service as mas
from src.services import message_beacon_service as beacon_svc
from src.services.regional_governance_service import RegionalGovernanceService


# --- minimal sync fake session (mirrors beacon deploy test shape) ---------- #

def _match(row: Any, cond: Any) -> bool:
    col_name = cond.left.key
    row_val = getattr(row, col_name, None)
    op_name = getattr(cond.operator, "__name__", None)
    if op_name == "eq":
        return row_val == cond.right.value
    raise NotImplementedError(f"unsupported operator {cond.operator!r}")


class _FakeQuery:
    def __init__(self, rows: List[Any]) -> None:
        self._rows = list(rows)

    def filter(self, *conds: Any) -> "_FakeQuery":
        rows = self._rows
        for cond in conds:
            rows = [r for r in rows if _match(r, cond)]
        return _FakeQuery(rows)

    def first(self) -> Optional[Any]:
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(
        self,
        flags: Optional[List[Any]] = None,
        clusters: Optional[List[Any]] = None,
    ) -> None:
        self.flags = flags or []
        self.clusters = clusters or []

    def query(self, *entities: Any) -> Any:
        head = entities[0]
        if head is MultiAccountFlag:
            return _FakeQuery(self.flags)
        if head is MultiAccountCluster:
            return _FakeQuery(self.clusters)
        raise AssertionError(f"unexpected query for {entities!r}")


@pytest.mark.unit
class TestSharedParticipationWeight:
    def test_defaults_to_1_when_no_flags(self) -> None:
        assert mas.participation_weight(_FakeSession(), uuid.uuid4()) == 1.0

    def test_hard_flag_weights_zero(self) -> None:
        player_id = uuid.uuid4()
        cluster_id = uuid.uuid4()
        flag = SimpleNamespace(
            player_id=player_id,
            cluster_id=cluster_id,
            severity=MultiAccountSeverity.HARD,
        )
        cluster = SimpleNamespace(id=cluster_id, all_paid_subscribers=False)
        assert (
            mas.participation_weight(
                _FakeSession(flags=[flag], clusters=[cluster]), player_id
            )
            == 0.0
        )

    def test_soft_flag_weights_half(self) -> None:
        """LEG-256: soft-tier discount is 0.5× per multi-account-detection.md."""
        player_id = uuid.uuid4()
        cluster_id = uuid.uuid4()
        flag = SimpleNamespace(
            player_id=player_id,
            cluster_id=cluster_id,
            severity=MultiAccountSeverity.SOFT,
        )
        cluster = SimpleNamespace(id=cluster_id, all_paid_subscribers=False)
        assert (
            mas.participation_weight(
                _FakeSession(flags=[flag], clusters=[cluster]), player_id
            )
            == 0.5
        )

    def test_all_paid_exemption_returns_one_for_hard(self) -> None:
        player_id = uuid.uuid4()
        cluster_id = uuid.uuid4()
        flag = SimpleNamespace(
            player_id=player_id,
            cluster_id=cluster_id,
            severity=MultiAccountSeverity.HARD,
        )
        cluster = SimpleNamespace(id=cluster_id, all_paid_subscribers=True)
        assert (
            mas.participation_weight(
                _FakeSession(flags=[flag], clusters=[cluster]), player_id
            )
            == 1.0
        )

    def test_all_paid_exemption_returns_one_for_soft(self) -> None:
        player_id = uuid.uuid4()
        cluster_id = uuid.uuid4()
        flag = SimpleNamespace(
            player_id=player_id,
            cluster_id=cluster_id,
            severity=MultiAccountSeverity.SOFT,
        )
        cluster = SimpleNamespace(id=cluster_id, all_paid_subscribers=True)
        assert (
            mas.participation_weight(
                _FakeSession(flags=[flag], clusters=[cluster]), player_id
            )
            == 1.0
        )

    def test_hard_precedes_soft_when_both_present(self) -> None:
        player_id = uuid.uuid4()
        cluster_id = uuid.uuid4()
        hard = SimpleNamespace(
            player_id=player_id,
            cluster_id=cluster_id,
            severity=MultiAccountSeverity.HARD,
        )
        soft = SimpleNamespace(
            player_id=player_id,
            cluster_id=cluster_id,
            severity=MultiAccountSeverity.SOFT,
        )
        cluster = SimpleNamespace(id=cluster_id, all_paid_subscribers=False)
        assert (
            mas.participation_weight(
                _FakeSession(flags=[soft, hard], clusters=[cluster]), player_id
            )
            == 0.0
        )

    def test_blocks_vote_tracks_hard_weight(self) -> None:
        player_id = uuid.uuid4()
        cluster_id = uuid.uuid4()
        hard = SimpleNamespace(
            player_id=player_id,
            cluster_id=cluster_id,
            severity=MultiAccountSeverity.HARD,
        )
        soft = SimpleNamespace(
            player_id=player_id,
            cluster_id=cluster_id,
            severity=MultiAccountSeverity.SOFT,
        )
        cluster = SimpleNamespace(id=cluster_id, all_paid_subscribers=False)
        assert (
            mas.blocks_vote(
                _FakeSession(flags=[hard], clusters=[cluster]), player_id
            )
            is True
        )
        assert (
            mas.blocks_vote(
                _FakeSession(flags=[soft], clusters=[cluster]), player_id
            )
            is False
        )
        assert mas.blocks_vote(_FakeSession(), player_id) is False

    def test_eligible_for_contest_soft_still_eligible(self) -> None:
        player_id = uuid.uuid4()
        planet_id = uuid.uuid4()
        cluster_id = uuid.uuid4()
        soft = SimpleNamespace(
            player_id=player_id,
            cluster_id=cluster_id,
            severity=MultiAccountSeverity.SOFT,
        )
        cluster = SimpleNamespace(id=cluster_id, all_paid_subscribers=False)
        assert (
            mas.eligible_for_contest(
                _FakeSession(flags=[soft], clusters=[cluster]),
                player_id,
                planet_id,
            )
            is True
        )

    def test_beacon_module_uses_shared_helper(self) -> None:
        """Beacon call site is the shared function (imported alias)."""
        assert beacon_svc._participation_weight is mas.participation_weight


@pytest.mark.unit
class TestHardFlaggedLosesBeaconAndVote:
    """Accept proof: one HARD-flagged player loses beacon count AND the vote."""

    def test_hard_flagged_loses_beacon_weight(self) -> None:
        player_id = uuid.uuid4()
        cluster_id = uuid.uuid4()
        flag = SimpleNamespace(
            player_id=player_id,
            cluster_id=cluster_id,
            severity=MultiAccountSeverity.HARD,
        )
        cluster = SimpleNamespace(id=cluster_id, all_paid_subscribers=False)
        db = _FakeSession(flags=[flag], clusters=[cluster])
        assert mas.participation_weight(db, player_id) == 0.0
        # Beacon consumer is the same function object.
        assert beacon_svc._participation_weight(db, player_id) == 0.0

    @pytest.mark.asyncio
    async def test_hard_flagged_loses_vote_eligibility(self) -> None:
        player_id = uuid.uuid4()
        mock_db = AsyncMock()
        # Age half: old enough to vote.
        mock_db.scalar = AsyncMock(
            return_value=datetime.now(timezone.utc) - timedelta(days=90)
        )
        # Multi-account half: run_sync(blocks_vote, player_id) → True.
        mock_db.run_sync = AsyncMock(return_value=True)

        eligible = await RegionalGovernanceService._is_account_vote_eligible(
            mock_db, player_id
        )
        assert eligible is False
        mock_db.run_sync.assert_awaited_once()
        assert mock_db.run_sync.await_args.args[0] is mas.blocks_vote
        assert mock_db.run_sync.await_args.args[1] == player_id

    @pytest.mark.asyncio
    async def test_clear_account_keeps_vote_when_age_ok(self) -> None:
        player_id = uuid.uuid4()
        mock_db = AsyncMock()
        mock_db.scalar = AsyncMock(
            return_value=datetime.now(timezone.utc) - timedelta(days=90)
        )
        mock_db.run_sync = AsyncMock(return_value=False)

        eligible = await RegionalGovernanceService._is_account_vote_eligible(
            mock_db, player_id
        )
        assert eligible is True
        mock_db.run_sync.assert_awaited_once_with(mas.blocks_vote, player_id)
