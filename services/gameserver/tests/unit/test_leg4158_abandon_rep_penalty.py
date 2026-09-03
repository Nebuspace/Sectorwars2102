"""LEG-4158: abandon() applies reputation_penalty via apply_faction_rep_delta.

contracts.md §Reputation effects: abandoning an NPC contract with a
reputation_penalty and faction_id must apply a negative delta.
"""
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, call, patch

import pytest

from src.models.contract import Contract, ContractIssuerType, ContractStatus, ContractType


class TestAbandonRepPenalty:
    """abandon() applies reputation_penalty -> apply_faction_rep_delta."""

    def _make_db(self) -> MagicMock:
        db = MagicMock()
        db.flush = MagicMock()
        return db

    def _make_player(self, player_id: uuid.UUID) -> MagicMock:
        player = MagicMock()
        player.id = player_id
        player.credits = 5000
        player.team_id = None
        return player

    def _make_contract(
        self,
        player_id: uuid.UUID,
        reputation_penalty=None,
        faction_id=None,
    ) -> MagicMock:
        contract = MagicMock(spec=Contract)
        contract.id = uuid.uuid4()
        contract.acceptor_player_id = player_id
        contract.status = ContractStatus.ACCEPTED
        contract.issuer_type = ContractIssuerType.NPC
        contract.issuer_id = uuid.uuid4()
        contract.penalty = Decimal("100")
        contract.payment = Decimal("1000")
        contract.quantity = 100
        contract.escrow_amount = Decimal("0")
        contract.escrow_state = None
        contract.insurance_coverage_tier = None
        contract.contract_type = ContractType.CARGO_DELIVERY
        contract.reputation_penalty = reputation_penalty
        contract.faction_id = faction_id
        return contract

    def test_abandon_applies_rep_penalty_when_set(self):
        """abandon() with reputation_penalty=20 and valid faction_id calls apply_faction_rep_delta(-20)."""
        faction_id = uuid.uuid4()
        player_id = uuid.uuid4()
        contract = self._make_contract(player_id, reputation_penalty=20, faction_id=faction_id)
        db = self._make_db()
        player = self._make_player(player_id)

        with patch("src.services.contract_service._load_contract", return_value=contract), \
             patch("src.services.contract_service._load_player", return_value=player), \
             patch("src.services.contract_service._guarded_transition"), \
             patch("src.services.contract_service._refresh_contract_insurance_snapshot"), \
             patch("src.services.contract_service._to_credits_int", return_value=100), \
             patch("src.services.contract_service._round_credits", return_value=Decimal("100")), \
             patch("src.services.contract_service._as_decimal", return_value=Decimal("100")), \
             patch("src.services.contract_service.apply_faction_rep_delta") as mock_rep:
            from src.services.contract_service import abandon
            abandon(db, contract.id, player_id)
            mock_rep.assert_called_once_with(
                db, player_id, faction_id, -20, reason="npc_contract_abandon"
            )

    def test_abandon_no_rep_change_when_penalty_is_none(self):
        """abandon() with reputation_penalty=None applies no rep change."""
        player_id = uuid.uuid4()
        contract = self._make_contract(player_id, reputation_penalty=None, faction_id=uuid.uuid4())
        db = self._make_db()
        player = self._make_player(player_id)

        with patch("src.services.contract_service._load_contract", return_value=contract), \
             patch("src.services.contract_service._load_player", return_value=player), \
             patch("src.services.contract_service._guarded_transition"), \
             patch("src.services.contract_service._refresh_contract_insurance_snapshot"), \
             patch("src.services.contract_service._to_credits_int", return_value=0), \
             patch("src.services.contract_service._round_credits", return_value=Decimal("0")), \
             patch("src.services.contract_service._as_decimal", return_value=Decimal("0")), \
             patch("src.services.contract_service.apply_faction_rep_delta") as mock_rep:
            from src.services.contract_service import abandon
            abandon(db, contract.id, player_id)
            mock_rep.assert_not_called()

    def test_abandon_no_rep_change_when_no_faction_id(self):
        """abandon() with reputation_penalty set but faction_id=None applies no rep change."""
        player_id = uuid.uuid4()
        contract = self._make_contract(player_id, reputation_penalty=20, faction_id=None)
        db = self._make_db()
        player = self._make_player(player_id)

        with patch("src.services.contract_service._load_contract", return_value=contract), \
             patch("src.services.contract_service._load_player", return_value=player), \
             patch("src.services.contract_service._guarded_transition"), \
             patch("src.services.contract_service._refresh_contract_insurance_snapshot"), \
             patch("src.services.contract_service._to_credits_int", return_value=0), \
             patch("src.services.contract_service._round_credits", return_value=Decimal("0")), \
             patch("src.services.contract_service._as_decimal", return_value=Decimal("0")), \
             patch("src.services.contract_service.apply_faction_rep_delta") as mock_rep:
            from src.services.contract_service import abandon
            abandon(db, contract.id, player_id)
            mock_rep.assert_not_called()

    def test_abandon_rep_penalty_exception_does_not_abort(self):
        """If apply_faction_rep_delta raises, abandon() still succeeds (best-effort)."""
        faction_id = uuid.uuid4()
        player_id = uuid.uuid4()
        contract = self._make_contract(player_id, reputation_penalty=20, faction_id=faction_id)
        db = self._make_db()
        player = self._make_player(player_id)

        with patch("src.services.contract_service._load_contract", return_value=contract), \
             patch("src.services.contract_service._load_player", return_value=player), \
             patch("src.services.contract_service._guarded_transition"), \
             patch("src.services.contract_service._refresh_contract_insurance_snapshot"), \
             patch("src.services.contract_service._to_credits_int", return_value=100), \
             patch("src.services.contract_service._round_credits", return_value=Decimal("100")), \
             patch("src.services.contract_service._as_decimal", return_value=Decimal("100")), \
             patch("src.services.contract_service.apply_faction_rep_delta", side_effect=RuntimeError("db error")) as mock_rep:
            from src.services.contract_service import abandon
            # abandon() should NOT raise even when rep delta fails
            result = abandon(db, contract.id, player_id)
            # The function returns normally (exception swallowed by best-effort try/except)
            assert "penalty_charged" in result
            # apply_faction_rep_delta WAS attempted (exception proves it was called)
            mock_rep.assert_called_once()
