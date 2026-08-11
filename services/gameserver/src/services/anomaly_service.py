"""Sector-type ANOMALY investigation (Audit-cycle-27 #1).

Canon: FEATURES/galaxy/generation.md — ANOMALY sectors are spatial oddities
with investigation-driven loot. Distinct from SpecialFormation investigate
(special_formation_service), but reuses the formation common-tier credit
magnitude (250) per hub ruling 2026-08-09 (Audit-27 #1 (c)).

State lives in ``Sector.nav_hazards["anomaly_investigation"]`` (additive JSONB
key — no schema change beyond the sector_type enum value).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.sector import Sector, SectorType

# Hub ruling Audit-27 #1 (c): reuse formation common-tier reward.
ANOMALY_INVESTIGATE_REWARD_CREDITS = 250

_INVESTIGATION_PROP_KEY = "anomaly_investigation"


class AnomalyNotFoundError(Exception):
    """Sector missing, wrong type, or player not present."""


class AnomalyAlreadyInvestigatedError(Exception):
    """One-time investigation reward already claimed."""


def is_anomaly_investigated(sector: Sector) -> bool:
    hazards = sector.nav_hazards or {}
    inv = hazards.get(_INVESTIGATION_PROP_KEY)
    return bool(inv and inv.get("investigated"))


def investigate_anomaly(
    db: Session, player, sector_id: int
) -> Dict[str, Any]:
    """Investigate a SectorType.ANOMALY sector the player currently occupies.

    Preconditions (raised for the route to map to HTTP):
      * sector exists AND ``type == ANOMALY`` AND player is in that sector
        — else ``AnomalyNotFoundError`` (404). Wrong-type / absent / not-here
        collapse to the same response so clients cannot probe for anomalies.
      * not already investigated — else ``AnomalyAlreadyInvestigatedError`` (409).

    On success: marks investigated in nav_hazards JSONB, grants 250 credits,
    commits. Returns formation-investigate-shaped payload for a thin client.
    """
    sector: Optional[Sector] = (
        db.query(Sector)
        .filter(Sector.sector_id == sector_id)
        .populate_existing()
        .with_for_update()
        .first()
    )

    if (
        sector is None
        or sector.type != SectorType.ANOMALY
        or getattr(player, "current_sector_id", None) != sector.sector_id
    ):
        raise AnomalyNotFoundError(
            "Anomaly sector not found or not present."
        )

    if is_anomaly_investigated(sector):
        raise AnomalyAlreadyInvestigatedError(
            "Anomaly has already been investigated."
        )

    reward_credits = ANOMALY_INVESTIGATE_REWARD_CREDITS
    player.credits = (player.credits or 0) + reward_credits

    hazards = dict(sector.nav_hazards or {})
    hazards[_INVESTIGATION_PROP_KEY] = {
        "investigated": True,
        "investigated_by": str(getattr(player, "id", "")),
        "investigated_at": datetime.now(timezone.utc).isoformat(),
        "reward_credits": reward_credits,
    }
    sector.nav_hazards = hazards
    flag_modified(sector, "nav_hazards")

    db.commit()

    return {
        "sector": {
            "sector_id": sector.sector_id,
            "name": sector.name,
            "type": SectorType.ANOMALY.value,
            "is_investigated": True,
        },
        "reward": {"credits": reward_credits},
        "credits_remaining": int(player.credits or 0),
        "reward_is_no_canon": False,
    }
