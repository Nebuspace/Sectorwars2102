"""
Shard → Crystal refining service — the ONLY player-driven path to Quantum
Crystals.

Canon reference: sw2102-docs quantum-resources.md (Shard→Crystal refining
5:1) + ADR-0009 (refining venue rule: a Class-3+ station or SpaceDock).
Quantum Crystals are otherwise creatable only via combat loot or an admin
grant; they are consumed by the warp-gate beacon deploy (warp_gate_service)
and Genesis advanced assembly (genesis_service). This service closes that
production gap.

NOT the same mechanic as ``quantum_service.refine_charge`` — that is a 1:1
Shard → Quantum *Charge* (a Warp-Jumper jump-drive consumable on
ships.quantum_charges). This is a 5 Shards + 10,000 cr → 1 *Crystal*
conversion on the player wallet, with no ship requirement.

ATOMICITY: the entry point locks the player row FOR UPDATE (mirroring
quantum_service / movement_service), re-checks every gate against the locked
state, and FLUSHES ONLY — the route owns db.commit() / db.rollback(). A
route that returns without committing silently rolls back the spent
shards/credits, so the route MUST commit on success.

KERNEL SCOPE: the documented 24h refine *queue* is DEFERRED — this ships the
instant 5:1 conversion. See REFINE_QUEUE_HOURS note in the route module / the
worker report for the proposed queued follow-up.
"""
import logging
import uuid
from typing import Any, Dict

from sqlalchemy.orm import Session

from src.models.player import Player
from src.models.station import Station

logger = logging.getLogger(__name__)


class RefiningError(Exception):
    """Raised for player-facing refining failures; .args[0] is the
    human-readable detail string the route layer surfaces as a 400."""


# --- Canonical constants (quantum-resources.md Shard→Crystal refining;
# ADR-0009 venue rule) ---

# 5 Quantum Shards → 1 Quantum Crystal.
CRYSTAL_SHARD_COST = 5
CRYSTAL_CREDIT_COST = 10_000
CRYSTAL_YIELD = 1

# A Class-3+ station OR a SpaceDock (mirrors quantum_service.REFINE_MIN_STATION_CLASS
# and the refine_charge venue gate so the two refining venues stay identical).
REFINE_MIN_STATION_CLASS = 3


def _lock_player(db: Session, player_id: uuid.UUID) -> Player:
    # populate_existing() forces a refresh from the locked row. with_for_update()
    # alone returns the identity-mapped instance already loaded by
    # get_current_player with PRE-LOCK state, so two concurrent refine calls could
    # both read the same shards/credits and double-spend (mirrors
    # quantum_service._lock_player / movement_service).
    player = (
        db.query(Player)
        .filter(Player.id == player_id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if not player:
        raise RefiningError("Player not found")
    return player


def refine(db: Session, player_id: uuid.UUID) -> Dict[str, Any]:
    """Refine 5 Quantum Shards + 10,000 cr into 1 Quantum Crystal.

    Venue rule (ADR-0009): the player must be docked at a Class-3+ station or
    a SpaceDock. Gates (all re-checked against the FOR-UPDATE-locked player
    row): docked at a qualifying station, ``quantum_shards >= 5``, and
    ``credits >= 10000``. On success, debits 5 shards + 10,000 cr and credits
    1 crystal — atomically (single locked transaction).

    FLUSH-ONLY: this method mutates the locked rows but does NOT commit; the
    route owns db.commit() (success) / db.rollback() (failure). Returns the new
    wallet balances; raises RefiningError with a stable detail string for any
    rejected attempt (the route maps it to a 400).
    """
    player = _lock_player(db, player_id)

    # GATE — docked at a qualifying station (Class-3+ or SpaceDock).
    if not player.is_docked or not player.current_port_id:
        raise RefiningError(
            "You must be docked at a Class-3+ station or SpaceDock to refine a "
            "Quantum Crystal"
        )
    station = db.query(Station).filter(Station.id == player.current_port_id).first()
    if not station:
        raise RefiningError("Docked station not found")
    if not (station.is_spacedock or station.station_class.value >= REFINE_MIN_STATION_CLASS):
        raise RefiningError(
            f"Quantum Crystal refining requires a Class-{REFINE_MIN_STATION_CLASS}+ "
            f"station or SpaceDock; {station.name} is Class {station.station_class.value}"
        )

    # GATE — sufficient shards (re-read off the locked row).
    shards = player.quantum_shards or 0
    if shards < CRYSTAL_SHARD_COST:
        raise RefiningError(
            f"Refining a Quantum Crystal costs {CRYSTAL_SHARD_COST} Quantum Shards; "
            f"you have {shards}"
        )

    # GATE — sufficient credits.
    credits = player.credits or 0
    if credits < CRYSTAL_CREDIT_COST:
        raise RefiningError(
            f"Refining a Quantum Crystal costs {CRYSTAL_CREDIT_COST:,} credits; "
            f"you have {credits:,}"
        )

    # ATOMIC conversion on the locked row.
    player.quantum_shards = shards - CRYSTAL_SHARD_COST
    player.credits = credits - CRYSTAL_CREDIT_COST
    player.quantum_crystals = (player.quantum_crystals or 0) + CRYSTAL_YIELD

    logger.info(
        "Player %s refined a Quantum Crystal at station %s "
        "(crystals=%d, shards=%d, credits=%d)",
        player.id, station.id, player.quantum_crystals,
        player.quantum_shards, player.credits,
    )

    return {
        "quantum_crystals": player.quantum_crystals,
        "quantum_shards": player.quantum_shards,
        "credits": player.credits,
        "shards_spent": CRYSTAL_SHARD_COST,
        "credits_spent": CRYSTAL_CREDIT_COST,
    }
