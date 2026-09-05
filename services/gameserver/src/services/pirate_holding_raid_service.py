"""Pirate-holding raid initiation (LEG-1105).

Live HTTP caller for the ADR-0060 combat-lock kernel
(``acquire_combat_lock`` / ``can_engage``) via
``POST /pirate-holdings/{id}/raid/initiate``.

Capture is a separate live route (``POST .../raid/capture``, LEG-4153) that
calls ``pirate_ecosystem_service.capture_holding`` directly from
``pirate_holdings.py`` — this module does **not** invoke capture; that
split is intentional (initiate = lock, capture = ownership flip).

Still incomplete relative to full canon raid flow: holding-anchored
garrison / NPC KIA gate and OutlawBase→NPCBarracks conversion are not
wired here (or elsewhere on tip).

Canon: sw2102-docs/SYSTEMS/pirate-holding-raid.md § Concurrent-attacker
arbitration (G-F2). Camps are permissive (no lock); Outpost/Stronghold rows
use the snapshotted team lock via ``acquire_combat_lock``.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict

from sqlalchemy.orm import Session

from src.models.pirate_holding import PirateHolding, PirateHoldingTier
from src.models.player import Player
from src.services import pirate_ecosystem_service as pes


class PirateHoldingRaidError(Exception):
    """Raised on invalid raid-initiation actions; carries an HTTP status hint."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def initiate_raid(db: Session, holding_id: uuid.UUID, player: Player) -> Dict[str, Any]:
    """Acquire (or skip for Camp) the G-F2 combat lock for a pirate holding.

    Flush-only — caller owns commit. Player must already have ``team`` (and
    ``team.members`` when present) loaded if team snapshotting matters."""
    holding = db.query(PirateHolding).filter(PirateHolding.id == holding_id).first()
    if holding is None:
        raise PirateHoldingRaidError(404, "Pirate holding not found")

    if holding.owner_player_id is not None:
        raise PirateHoldingRaidError(400, "Holding is already captured")

    if player.current_sector_id != holding.sector_id:
        raise PirateHoldingRaidError(
            403,
            "Player must be in the holding anchor sector to initiate a raid",
        )

    if holding.tier == PirateHoldingTier.CAMP:
        # Canon: camps have no concurrent-attacker lock.
        return {
            "holding_id": str(holding.id),
            "tier": holding.tier.value,
            "initiated": True,
            "lock_applied": False,
        }

    if not pes.can_engage(holding, player.id):
        raise PirateHoldingRaidError(
            409,
            "Holding is locked by another attacker",
        )

    pes.acquire_combat_lock(db, holding, player)
    db.flush()

    return {
        "holding_id": str(holding.id),
        "tier": holding.tier.value,
        "initiated": True,
        "lock_applied": True,
        "combat_lock_held_by": str(holding.combat_lock_held_by),
        "combat_lock_team_snapshot": [
            str(member_id) for member_id in (holding.combat_lock_team_snapshot or [])
        ],
    }
