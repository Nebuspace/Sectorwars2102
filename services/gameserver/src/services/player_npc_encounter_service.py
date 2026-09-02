"""Player–NPC co-presence encounter recording (LEG-3961).

Upserts encounter rows when a player arrives in a sector shared with an
on-duty named NPC. Read-only listing for the player dossier.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from src.models.npc_character import NPCCharacter, NPCStatus
from src.models.player_npc_encounter import PlayerNpcEncounter

logger = logging.getLogger(__name__)

_ON_DUTY_STATUSES = (NPCStatus.ON_DUTY,)


def _format_npc_name(npc: NPCCharacter) -> str:
    if npc.title:
        return f"{npc.title} {npc.name}"
    return npc.name


def record_npc_copresence_for_sector(
    db: Session,
    player_id: uuid.UUID,
    sector_id: int,
) -> int:
    """Upsert encounter rows for every on-duty NPC in ``sector_id``.

    Returns the number of rows touched. Commits on success; rolls back and
    re-raises on failure (caller wraps in non-fatal guard).
    """
    npcs = (
        db.query(NPCCharacter)
        .filter(
            NPCCharacter.current_sector_id == sector_id,
            NPCCharacter.status.in_(_ON_DUTY_STATUSES),
        )
        .all()
    )
    if not npcs:
        return 0

    now = datetime.now(timezone.utc)
    touched = 0
    for npc in npcs:
        row = (
            db.query(PlayerNpcEncounter)
            .filter(
                PlayerNpcEncounter.player_id == player_id,
                PlayerNpcEncounter.npc_character_id == npc.id,
            )
            .first()
        )
        if row is None:
            db.add(
                PlayerNpcEncounter(
                    player_id=player_id,
                    npc_character_id=npc.id,
                    count=1,
                    last_at=now,
                    last_sector_id=sector_id,
                )
            )
        else:
            row.count = int(row.count or 0) + 1
            row.last_at = now
            row.last_sector_id = sector_id
        touched += 1

    db.commit()
    return touched


def list_player_npc_encounters(
    db: Session,
    player_id: uuid.UUID,
) -> List[Dict[str, Any]]:
    """Return encounter summaries for the authenticated player's dossier."""
    rows = (
        db.query(PlayerNpcEncounter, NPCCharacter)
        .join(NPCCharacter, NPCCharacter.id == PlayerNpcEncounter.npc_character_id)
        .filter(PlayerNpcEncounter.player_id == player_id)
        .order_by(PlayerNpcEncounter.last_at.desc())
        .all()
    )
    return [
        {
            "npc_character_id": str(encounter.npc_character_id),
            "npc_name": _format_npc_name(npc),
            "count": encounter.count,
            "last_at": encounter.last_at.isoformat() if encounter.last_at else None,
            "last_sector_id": encounter.last_sector_id,
        }
        for encounter, npc in rows
    ]
