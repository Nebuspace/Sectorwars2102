"""Unit tests for contract_reputation_service (LEG-3625)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, List

import pytest

from src.models.contract import (
    ContractDisputeResolution,
    ContractIssuerType,
    ContractStatus,
    ContractType,
)
from src.models.faction import FactionType
from src.services import contract_reputation_service as crs

UTC = timezone.utc
_NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _faction(**overrides: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(),
        faction_type=FactionType.MINING,
        name="Astral Mining Consortium",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _contract(**overrides: Any) -> SimpleNamespace:
    if "faction" in overrides:
        faction = overrides.pop("faction")
    else:
        faction = _faction()
    faction_id = overrides.pop("faction_id", getattr(faction, "id", None) if faction else None)
    base = dict(
        id=uuid.uuid4(),
        issuer_type=ContractIssuerType.NPC,
        issuer_id=uuid.uuid4(),
        acceptor_player_id=uuid.uuid4(),
        contract_type=ContractType.CARGO_DELIVERY,
        status=ContractStatus.DISPUTED,
        quantity=100,
        payment=Decimal("2000.00"),
        faction_id=faction_id,
        faction=faction,
        reputation_reward=20,
        reputation_penalty=-10,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.unit
class TestApplyDisputeOutcomeReputation:
    def test_full_payout_applies_retroactive_reward(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: List[Any] = []
        monkeypatch.setattr(
            crs, "apply_faction_rep_delta",
            lambda db, pid, ft, delta, reason, faction_name=None: calls.append(
                (pid, ft, delta, reason, faction_name)
            ),
        )
        acceptor_id = uuid.uuid4()
        c = _contract(reputation_reward=25)
        crs.apply_dispute_outcome_reputation(
            None, c, ContractDisputeResolution.FULL_PAYOUT, player_id=acceptor_id, now=_NOW,
        )
        assert len(calls) == 1
        assert calls[0][0] == acceptor_id
        assert calls[0][1] == FactionType.MINING
        assert calls[0][2] == 25
        assert calls[0][3] == "dispute_full_payout_reputation_reward"

    def test_partial_payout_delivered_zero_pin_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: List[Any] = []
        monkeypatch.setattr(
            crs, "apply_faction_rep_delta",
            lambda *a, **kw: calls.append((a, kw)),
        )
        c = _contract(reputation_reward=40, quantity=50)
        crs.apply_dispute_outcome_reputation(
            None, c, ContractDisputeResolution.PARTIAL_PAYOUT, player_id=uuid.uuid4(), now=_NOW,
        )
        assert calls == []

    def test_refund_player_issuer_applies_penalty_to_issuer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: List[Any] = []
        monkeypatch.setattr(
            crs, "apply_faction_rep_delta",
            lambda db, pid, ft, delta, reason, faction_name=None: calls.append(
                (pid, ft, delta, reason)
            ),
        )
        issuer_id = uuid.uuid4()
        acceptor_id = uuid.uuid4()
        c = _contract(
            issuer_type=ContractIssuerType.PLAYER,
            issuer_id=issuer_id,
            reputation_penalty=-15,
        )
        crs.apply_dispute_outcome_reputation(
            None, c, ContractDisputeResolution.REFUND, player_id=acceptor_id, now=_NOW,
        )
        assert len(calls) == 1
        assert calls[0][0] == issuer_id
        assert calls[0][2] == -15
        assert calls[0][3] == "dispute_refund_issuer_reputation_penalty"

    def test_refund_npc_issuer_is_documented_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: List[Any] = []
        monkeypatch.setattr(
            crs, "apply_faction_rep_delta",
            lambda *a, **kw: calls.append((a, kw)),
        )
        c = _contract(
            issuer_type=ContractIssuerType.NPC,
            reputation_penalty=-12,
        )
        crs.apply_dispute_outcome_reputation(
            None, c, ContractDisputeResolution.REFUND, player_id=uuid.uuid4(), now=_NOW,
        )
        assert calls == []

    def test_penalty_doubles_when_not_paused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: List[Any] = []
        monkeypatch.setattr(
            crs, "apply_faction_rep_delta",
            lambda db, pid, ft, delta, reason, faction_name=None: calls.append(
                (pid, ft, delta, reason)
            ),
        )
        acceptor_id = uuid.uuid4()
        c = _contract(status=ContractStatus.CANCELLED, reputation_penalty=-8)
        crs.apply_dispute_outcome_reputation(
            None, c, ContractDisputeResolution.PENALTY, player_id=acceptor_id, now=_NOW,
        )
        assert len(calls) == 1
        assert calls[0][0] == acceptor_id
        assert calls[0][2] == -16
        assert calls[0][3] == "dispute_penalty_doubled_reputation_penalty"

    def test_penalty_skipped_when_pause_gate_active(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: List[Any] = []
        monkeypatch.setattr(
            crs, "apply_faction_rep_delta",
            lambda *a, **kw: calls.append((a, kw)),
        )
        c = _contract(status=ContractStatus.DISPUTED, reputation_penalty=-8)
        crs.apply_dispute_outcome_reputation(
            None, c, ContractDisputeResolution.PENALTY, player_id=uuid.uuid4(), now=_NOW,
        )
        assert calls == []

    def test_split_applies_half_reward_and_half_penalty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: List[Any] = []
        monkeypatch.setattr(
            crs, "apply_faction_rep_delta",
            lambda db, pid, ft, delta, reason, faction_name=None: calls.append(
                (delta, reason)
            ),
        )
        c = _contract(reputation_reward=21, reputation_penalty=-11)
        crs.apply_dispute_outcome_reputation(
            None, c, ContractDisputeResolution.SPLIT, player_id=uuid.uuid4(), now=_NOW,
        )
        assert len(calls) == 2
        assert (10, "dispute_split_half_reputation_reward") in calls
        assert (-5, "dispute_split_half_reputation_penalty") in calls

    def test_no_faction_skips_without_crashing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: List[Any] = []
        monkeypatch.setattr(
            crs, "apply_faction_rep_delta",
            lambda *a, **kw: calls.append((a, kw)),
        )
        c = _contract(faction=None, faction_id=None, reputation_reward=10)
        crs.apply_dispute_outcome_reputation(
            None, c, ContractDisputeResolution.FULL_PAYOUT, player_id=uuid.uuid4(), now=_NOW,
        )
        assert calls == []
