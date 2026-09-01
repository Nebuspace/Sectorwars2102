"""Contract dispute Tier-2 reputation kernel (LEG-3625).

Maps each ``ContractDisputeResolution`` outcome to faction-reputation deltas
via the proven sync, flush-only ``apply_faction_rep_delta`` rail
(``contract_service.complete`` hazardous_transport / NPC reward precedent).

Wiring into ``contract_dispute.resolve_dispute`` is OUT OF SCOPE here — see
LEG-2071.

OUT OF SCOPE (explicit):
  - Cooldown / ban persistence (24h / 72h issuer cooldowns, repeat-filer flags)
  - Trader-reputation stat / profile badge
  - Player-to-player mutual reputation bump on complete
  - Inventing magnitudes not frozen on the contract row at posting time
  - Tier-1 automated reputation effects
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Optional, Tuple

from sqlalchemy.orm import Session

from src.models.contract import (
    ContractDisputeResolution,
    ContractIssuerType,
)
from src.models.faction import Faction, FactionType
from src.services.contract_dispute import _is_reputation_penalty_paused
from src.services.faction_service import apply_faction_rep_delta

logger = logging.getLogger(__name__)

# [NO-CANON] Parity pin: ``resolve_dispute`` uses ``delivered = 0`` for
# PARTIAL_PAYOUT until a per-contract delivery signal exists
# (contract_dispute.py docstring). This kernel mirrors that conservative floor.
_PARTIAL_DELIVERED_PIN = 0


def _resolve_contract_faction(
    db: Session, contract: Any,
) -> Optional[Tuple[FactionType, Optional[str]]]:
    """Resolve issuing faction for rep deltas — mirrors ``contract_service.complete``."""
    faction = getattr(contract, "faction", None)
    if faction is None:
        faction_id = getattr(contract, "faction_id", None)
        if faction_id is not None:
            faction = db.query(Faction).filter(Faction.id == faction_id).first()
    if faction is None or getattr(faction, "faction_type", None) is None:
        return None
    return faction.faction_type, getattr(faction, "name", None)


def _apply_acceptor_faction_delta(
    db: Session,
    *,
    player_id: uuid.UUID,
    contract: Any,
    delta: int,
    reason: str,
    faction: Optional[Tuple[FactionType, Optional[str]]] = None,
) -> None:
    if delta == 0:
        return
    resolved = faction if faction is not None else _resolve_contract_faction(db, contract)
    if resolved is None:
        logger.info(
            "apply_dispute_outcome_reputation: no issuing faction on contract %s — "
            "skipping delta %+d for player %s (reason: %s).",
            getattr(contract, "id", None), delta, player_id, reason,
        )
        return
    faction_type, faction_name = resolved
    apply_faction_rep_delta(
        db,
        player_id,
        faction_type,
        delta,
        reason,
        faction_name=faction_name,
    )


def _expected_quantity(contract: Any) -> int:
    qty = getattr(contract, "quantity", None)
    return int(qty) if qty else 0


def _proportional_reward(contract: Any) -> int:
    reward = getattr(contract, "reputation_reward", None)
    if not reward:
        return 0
    expected = _expected_quantity(contract)
    if expected <= 0:
        return 0
    return int(int(reward) * _PARTIAL_DELIVERED_PIN / expected)


def _apply_full_payout(
    db: Session, contract: Any, *, player_id: uuid.UUID,
    faction: Optional[Tuple[FactionType, Optional[str]]],
) -> None:
    reward = getattr(contract, "reputation_reward", None)
    if reward:
        _apply_acceptor_faction_delta(
            db,
            player_id=player_id,
            contract=contract,
            delta=int(reward),
            reason="dispute_full_payout_reputation_reward",
            faction=faction,
        )


def _apply_partial_payout(
    db: Session, contract: Any, *, player_id: uuid.UUID,
    faction: Optional[Tuple[FactionType, Optional[str]]],
) -> None:
    proportional = _proportional_reward(contract)
    if proportional:
        _apply_acceptor_faction_delta(
            db,
            player_id=player_id,
            contract=contract,
            delta=proportional,
            reason="dispute_partial_payout_reputation_reward",
            faction=faction,
        )


def _apply_refund(
    db: Session, contract: Any, *, player_id: uuid.UUID,
    faction: Optional[Tuple[FactionType, Optional[str]]],
) -> None:
    del player_id
    penalty = getattr(contract, "reputation_penalty", None)
    if not penalty:
        return
    issuer_type = getattr(contract, "issuer_type", None)
    if issuer_type == ContractIssuerType.PLAYER:
        issuer_id = getattr(contract, "issuer_id", None)
        if issuer_id is not None:
            _apply_acceptor_faction_delta(
                db,
                player_id=issuer_id,
                contract=contract,
                delta=int(penalty),
                reason="dispute_refund_issuer_reputation_penalty",
                faction=faction,
            )
        return
    logger.info(
        "apply_dispute_outcome_reputation: REFUND on NPC-issued contract %s — "
        "issuer penalty (%s) has no player faction target; skipping.",
        getattr(contract, "id", None), penalty,
    )


def _apply_penalty(
    db: Session, contract: Any, *, player_id: uuid.UUID,
    faction: Optional[Tuple[FactionType, Optional[str]]],
) -> None:
    penalty = getattr(contract, "reputation_penalty", None)
    if not penalty or _is_reputation_penalty_paused(contract):
        return
    _apply_acceptor_faction_delta(
        db,
        player_id=player_id,
        contract=contract,
        delta=int(penalty) * 2,
        reason="dispute_penalty_doubled_reputation_penalty",
        faction=faction,
    )


def _apply_split(
    db: Session, contract: Any, *, player_id: uuid.UUID,
    faction: Optional[Tuple[FactionType, Optional[str]]],
) -> None:
    reward = getattr(contract, "reputation_reward", None)
    if reward:
        half_reward = int(int(reward) / 2)
        if half_reward:
            _apply_acceptor_faction_delta(
                db,
                player_id=player_id,
                contract=contract,
                delta=half_reward,
                reason="dispute_split_half_reputation_reward",
                faction=faction,
            )
    penalty = getattr(contract, "reputation_penalty", None)
    if penalty:
        half_penalty = int(int(penalty) / 2)
        if half_penalty:
            _apply_acceptor_faction_delta(
                db,
                player_id=player_id,
                contract=contract,
                delta=half_penalty,
                reason="dispute_split_half_reputation_penalty",
                faction=faction,
            )


_OUTCOME_HANDLERS = {
    ContractDisputeResolution.FULL_PAYOUT: _apply_full_payout,
    ContractDisputeResolution.PARTIAL_PAYOUT: _apply_partial_payout,
    ContractDisputeResolution.REFUND: _apply_refund,
    ContractDisputeResolution.PENALTY: _apply_penalty,
    ContractDisputeResolution.SPLIT: _apply_split,
}


def apply_dispute_outcome_reputation(
    db: Session,
    contract: Any,
    outcome: ContractDisputeResolution,
    *,
    player_id: uuid.UUID,
    now: datetime,
) -> None:
    """Apply Tier-2 dispute outcome reputation effects (contracts.md Reputation column).

    ``player_id`` is the contract acceptor. Magnitudes come only from frozen row
    columns ``reputation_reward`` / ``reputation_penalty``. FLUSH-ONLY — caller
    owns the transaction.

    ``_is_reputation_penalty_paused`` is consulted for PENALTY (doubled penalty).
    When called post-``resolve_dispute`` guarded transition the contract has
    already left DISPUTED, so the pause gate is typically False; callers wiring
    this inside resolve before status flip should pass DISPUTED status or call
    before status flip — LEG-2071 owns that ordering choice.
    """
    del now  # reserved for future cooldown wiring; unused in kernel-only scope

    handler = _OUTCOME_HANDLERS.get(outcome)
    if handler is None:
        logger.warning(
            "apply_dispute_outcome_reputation: unhandled outcome %s on contract %s",
            outcome, getattr(contract, "id", None),
        )
        return
    faction = _resolve_contract_faction(db, contract)
    handler(db, contract, player_id=player_id, faction=faction)
