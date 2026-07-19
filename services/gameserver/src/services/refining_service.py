"""
Shard → Crystal refining service — the ONLY player-driven path to Quantum
Crystals, PLUS the Class-5+ Shard-to-Lumen-Crystal refine (WO-GWQ-LUMEN-FAUCET).

Canon reference: sw2102-docs quantum-resources.md (Shard→Crystal refining
5:1, Shard→Lumen refining 100:1 at :233-237) + ADR-0009 (refining venue rule:
a Class-3+ station or SpaceDock) + ADR-0037 (Lumen supply economy). Quantum
Crystals are otherwise creatable only via combat loot or an admin grant; they
are consumed by the warp-gate beacon deploy (warp_gate_service) and Genesis
advanced assembly (genesis_service). This service closes that production gap
— and, for Lumen, gives the Phase-3 warp-gate requirement its first faucet
alongside quantum_service.harvest_nebula's RNG drop.

NOT the same mechanic as ``quantum_service.refine_charge`` — that is a 1:1
Shard → Quantum *Charge* (a Warp-Jumper jump-drive consumable on
ships.quantum_charges). ``refine()`` below is a 5 Shards + 10,000 cr → 1
*Crystal* conversion on the player wallet, with no ship requirement.

ATOMICITY: every entry point locks the player row FOR UPDATE (mirroring
quantum_service / movement_service), re-checks every gate against the locked
state, and FLUSHES ONLY — the route owns db.commit() / db.rollback(). A
route that returns without committing silently rolls back the spent
shards/credits, so the route MUST commit on success.

KERNEL SCOPE: the documented 24h refine *queue* for the 5:1 Quantum Crystal
path is DEFERRED — ``refine()`` ships the instant conversion. See
REFINE_QUEUE_HOURS note in the route module / the worker report for the
proposed queued follow-up. The Lumen Crystal path below is DIFFERENT: canon
(quantum-resources.md:233) states a real 12-hour refining timer for Lumen
("half the Quantum Crystal refine for the higher-tier Lumen output" — i.e.
the 24h queue Quantum Crystal refining left deferred), so
``start_lumen_refine``/``collect_lumen_refine`` build that timer for real
rather than shipping an instant kernel. NOTE: refining instant-vs-timer
scarcity was one of the 3 ESCALATE design calls in the
iteration-2026-06-28-digest.md; a pending Max ruling may supersede this
12h-timer build.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from src.core.game_time import scaled_deadline
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

# 100 Quantum Shards + 10,000 cr -> 1 Lumen Crystal, 12h real-time timer,
# gated at a HIGHER station class than the Quantum Crystal path above
# (quantum-resources.md:233-237; ADR-0037). "the same exotic_technology-class
# venue list as Quantum Crystal refining" is read as the same venue-rule
# SHAPE (station-class-or-SpaceDock), just at the raised Class-5 floor —
# SpaceDocks are full-service infrastructure that already carry a refining
# amenity (DATA_MODELS/stations.md), so they qualify here too.
LUMEN_SHARD_COST = 100
# NO-CANON: canon states "~10k" — fixed at exactly 10,000 cr, flagged to
# DECISIONS (mirrors the WO's own NO-CANON note).
LUMEN_CREDIT_COST = 10_000
LUMEN_YIELD = 1
LUMEN_REFINE_MIN_STATION_CLASS = 5
LUMEN_REFINE_HOURS = 12.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


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


# --- Lumen Crystal refining (WO-GWQ-LUMEN-FAUCET) ---
#
# Unlike refine() above, this is a two-step start/collect job: start() debits
# the shards/credits and arms a 12h wall-clock deadline; collect() claims the
# Lumen Crystal once the deadline has passed. Only one job may be in flight
# per player (players.lumen_refine_ready_at is a single nullable slot) —
# start() is rejected while a prior job hasn't been collected yet.


def start_lumen_refine(db: Session, player_id: uuid.UUID) -> Dict[str, Any]:
    """Begin refining 100 Quantum Shards + 10,000 cr into 1 Lumen Crystal.

    Venue rule (quantum-resources.md:233-237): the player must be docked at a
    Class-5+ station or a SpaceDock — a higher floor than the Quantum Crystal
    path's Class-3+, reflecting the higher-tier output. Gates (all re-checked
    against the FOR-UPDATE-locked player row): docked at a qualifying
    station, no Lumen refine job already in flight, ``quantum_shards >= 100``,
    and ``credits >= 10000``. On success, debits 100 shards + 10,000 cr and
    arms ``player.lumen_refine_ready_at`` at scaled_deadline(12h) — the Lumen
    Crystal itself is not credited until ``collect_lumen_refine`` after that
    deadline passes.

    FLUSH-ONLY: the route owns db.commit() (success) / db.rollback()
    (failure). Raises RefiningError with a stable detail string for any
    rejected attempt (the route maps it to a 400).
    """
    player = _lock_player(db, player_id)

    # GATE — docked at a qualifying station (Class-5+ or SpaceDock).
    if not player.is_docked or not player.current_port_id:
        raise RefiningError(
            "You must be docked at a Class-5+ station or SpaceDock to start "
            "a Lumen Crystal refine"
        )
    station = db.query(Station).filter(Station.id == player.current_port_id).first()
    if not station:
        raise RefiningError("Docked station not found")
    if not (station.is_spacedock or station.station_class.value >= LUMEN_REFINE_MIN_STATION_CLASS):
        raise RefiningError(
            f"Lumen Crystal refining requires a Class-{LUMEN_REFINE_MIN_STATION_CLASS}+ "
            f"station or SpaceDock; {station.name} is Class {station.station_class.value}"
        )

    # GATE — no job already in flight (single-slot; collect before starting
    # another, even if the prior job is already past its deadline).
    if player.lumen_refine_ready_at is not None:
        raise RefiningError(
            "A Lumen Crystal refine job is already in progress; collect it "
            "before starting another"
        )

    # GATE — sufficient shards (re-read off the locked row).
    shards = player.quantum_shards or 0
    if shards < LUMEN_SHARD_COST:
        raise RefiningError(
            f"Refining a Lumen Crystal costs {LUMEN_SHARD_COST} Quantum Shards; "
            f"you have {shards}"
        )

    # GATE — sufficient credits.
    credits = player.credits or 0
    if credits < LUMEN_CREDIT_COST:
        raise RefiningError(
            f"Refining a Lumen Crystal costs {LUMEN_CREDIT_COST:,} credits; "
            f"you have {credits:,}"
        )

    # ATOMIC debit + arm the timer on the locked row.
    player.quantum_shards = shards - LUMEN_SHARD_COST
    player.credits = credits - LUMEN_CREDIT_COST
    player.lumen_refine_ready_at = scaled_deadline(LUMEN_REFINE_HOURS)

    logger.info(
        "Player %s started a Lumen Crystal refine at station %s, ready_at=%s "
        "(shards=%d, credits=%d)",
        player.id, station.id, player.lumen_refine_ready_at,
        player.quantum_shards, player.credits,
    )

    return {
        "lumen_refine_ready_at": _aware(player.lumen_refine_ready_at).isoformat(),
        "quantum_shards": player.quantum_shards,
        "credits": player.credits,
        "shards_spent": LUMEN_SHARD_COST,
        "credits_spent": LUMEN_CREDIT_COST,
    }


def collect_lumen_refine(db: Session, player_id: uuid.UUID) -> Dict[str, Any]:
    """Claim the 1 Lumen Crystal from a completed refine job.

    Not station-gated — the debit and venue check already happened at
    start(); collecting is a wallet-side claim the player can make from
    anywhere (NO-CANON: the job does not occupy the ship/station slot,
    flagged to DECISIONS). Raises RefiningError if no job is in flight, or if
    the 12h deadline hasn't passed yet.

    FLUSH-ONLY: the route owns db.commit() / db.rollback().
    """
    player = _lock_player(db, player_id)

    ready_at = _aware(player.lumen_refine_ready_at)
    if ready_at is None:
        raise RefiningError("No Lumen Crystal refine job is in progress")
    if _now() < ready_at:
        raise RefiningError(
            f"The Lumen Crystal refine is not ready yet; ready at {ready_at.isoformat()}"
        )

    player.lumen_crystals = (player.lumen_crystals or 0) + LUMEN_YIELD
    player.lumen_refine_ready_at = None

    logger.info(
        "Player %s collected a Lumen Crystal refine (lumen_crystals=%d)",
        player.id, player.lumen_crystals,
    )

    return {
        "lumen_crystals": player.lumen_crystals,
        "lumen_yield": LUMEN_YIELD,
    }


def get_lumen_refine_status(player: Player) -> Dict[str, Any]:
    """Read-only status for the docked refining card — never mutates state
    and needs no row lock. ``collectible`` is true once the 12h deadline has
    passed; the route's /collect endpoint is the one that actually claims it.
    """
    ready_at = _aware(player.lumen_refine_ready_at)
    if ready_at is None:
        return {"pending": False, "ready_at": None, "collectible": False}
    return {
        "pending": True,
        "ready_at": ready_at.isoformat(),
        "collectible": _now() >= ready_at,
    }
