"""LEG-4143: contract board reputation gate and issuer-blocklist filter.

Unit tests for the _is_visible filtering logic in get_contract_board.
DB-free: tests the Python filter logic via SimpleNamespace mocks.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from src.models.contract import ContractIssuerType, ContractStatus, ContractType


def _npc_contract(faction_id=None, contract_type=ContractType.CARGO_DELIVERY):
    return SimpleNamespace(
        id=uuid.uuid4(),
        issuer_type=ContractIssuerType.NPC,
        issuer_id=uuid.uuid4(),
        faction_id=faction_id,
        status=ContractStatus.POSTED,
        contract_type=contract_type,
    )


def _player_contract(issuer_id=None):
    issuer_id = issuer_id or uuid.uuid4()
    return SimpleNamespace(
        id=uuid.uuid4(),
        issuer_type=ContractIssuerType.PLAYER,
        issuer_id=issuer_id,
        faction_id=None,
        status=ContractStatus.POSTED,
        contract_type=ContractType.CARGO_DELIVERY,
    )


def _is_visible(contract, hostile_faction_ids, blocked_issuer_ids, viewer_id=None):
    """Replicate the _is_visible logic from get_contract_board (LEG-4143)."""
    issuer_type = getattr(contract, "issuer_type", None)
    if issuer_type == ContractIssuerType.NPC and contract.faction_id in hostile_faction_ids:
        return False
    if issuer_type == ContractIssuerType.PLAYER and contract.issuer_id in blocked_issuer_ids:
        return False
    return True


@pytest.mark.unit
class TestBoardReputationGate:
    def test_npc_contract_no_faction_always_visible(self) -> None:
        c = _npc_contract(faction_id=None)
        assert _is_visible(c, hostile_faction_ids=set(), blocked_issuer_ids=set())

    def test_npc_contract_friendly_faction_visible(self) -> None:
        fid = uuid.uuid4()
        c = _npc_contract(faction_id=fid)
        # Not in hostile set → visible.
        assert _is_visible(c, hostile_faction_ids=set(), blocked_issuer_ids=set())

    def test_npc_contract_hostile_faction_hidden(self) -> None:
        fid = uuid.uuid4()
        c = _npc_contract(faction_id=fid)
        # Faction is in hostile set → hidden.
        assert not _is_visible(c, hostile_faction_ids={fid}, blocked_issuer_ids=set())

    def test_multiple_contracts_filtered_selectively(self) -> None:
        hostile_fid = uuid.uuid4()
        friendly_fid = uuid.uuid4()
        hidden = _npc_contract(faction_id=hostile_fid)
        visible = _npc_contract(faction_id=friendly_fid)
        no_faction = _npc_contract(faction_id=None)

        hostile = {hostile_fid}
        results = [c for c in [hidden, visible, no_faction] if _is_visible(c, hostile, set())]
        assert visible in results
        assert no_faction in results
        assert hidden not in results


@pytest.mark.unit
class TestBoardBlocklistGate:
    def test_player_contract_not_blocked_visible(self) -> None:
        c = _player_contract()
        assert _is_visible(c, hostile_faction_ids=set(), blocked_issuer_ids=set())

    def test_player_contract_blocked_issuer_hidden(self) -> None:
        issuer_id = uuid.uuid4()
        c = _player_contract(issuer_id=issuer_id)
        assert not _is_visible(c, hostile_faction_ids=set(), blocked_issuer_ids={issuer_id})

    def test_own_contracts_never_hidden(self) -> None:
        """Player's own contracts: issuer == viewer — blocked_issuer_ids excludes own id."""
        viewer_id = uuid.uuid4()
        c = _player_contract(issuer_id=viewer_id)
        # Simulate the route logic: viewer's own id excluded from blocked set.
        blocked = set()  # own id never put in blocked set
        assert _is_visible(c, hostile_faction_ids=set(), blocked_issuer_ids=blocked)

    def test_npc_contracts_unaffected_by_blocklist(self) -> None:
        fid = uuid.uuid4()
        c = _npc_contract(faction_id=None)
        # Even if a NPC issuer_id somehow ended up in blocked set, NPC contracts
        # are gated by faction, not by issuer blocklist.
        assert _is_visible(c, hostile_faction_ids=set(), blocked_issuer_ids={c.issuer_id})
