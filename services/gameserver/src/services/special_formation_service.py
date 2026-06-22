"""Special-formation discovery (WO-CA).

Mirrors the planet/feature discovery pattern (discovery_service.py): a player
arriving in — or scanning — a sector that is a formation's anchor, or any of its
interior sectors, flips that formation's ``is_discovered`` False→True and back-
fills its public ``name`` column from ``properties["name"]`` (the bang importer
only ever wrote the name into the JSONB, never the dedicated column — see
bang_import_service.py).

Discovery is first-observe and idempotent: an already-discovered formation is a
no-op. Flush-only — the caller owns the commit (so the flip rides the move's own
single commit, exactly like the ARIA / medal hooks in movement_service).

The reverse "which formations contain this sector" lookup is NOT a SQLAlchemy
relationship — interior membership lives in the ``interior_sector_ids`` ARRAY,
queried via the GIN containment index ``ix_special_formations_interior_sector_ids``
(see SpecialFormation model). So we issue two predicates (anchor match OR interior
containment) in one query.

``discovery_requirement`` (a documented-but-unenforced JSONB precondition) is
intentionally NOT enforced here: no code anywhere reads it yet, and inventing an
unlock rule would be inventing canon. Visiting the sector is the discovery event.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import or_
from sqlalchemy.orm.attributes import flag_modified

from src.models.sector import Sector
from src.models.special_formation import SpecialFormation, SpecialFormationType

logger = logging.getLogger(__name__)


class FormationNotDiscoveredError(Exception):
    """Raised when a player tries to investigate a formation they have not yet
    discovered (the route maps this to 404 — an undiscovered formation does not
    exist from the player's perspective, mirroring identity-withholding)."""


class FormationAlreadyInvestigatedError(Exception):
    """Raised when a formation has already been investigated (the route maps this
    to 409 — investigation is a one-time event; the reward is not repeatable)."""


# --- Investigation reward calibration ----------------------------------------
# [NO-CANON] The investigate reward magnitude is NOT specified in sw2102-docs
# (DATA_MODELS/special-formations.md describes the catalog/topology; no
# investigation reward is documented). The values below are a CONSERVATIVE
# proposed scale, FLAGGED for Max's canon ruling — they are intentionally modest
# (a few percent of a player's 10,000-credit start) and scale by formation
# rarity (rarer topologies pay more). Tune or replace once canon lands.
INVESTIGATE_REWARD_NO_CANON = True

# Rarity tiers (proposed): single-sector terminals are common; multi-sector
# bubbles and the ADR-0070 island formations are rarer; the operator-placed
# GOLD_BUBBLE and the ARCHIPELAGO are the rarest. Each tier maps to a credit
# reward. (Default for any unmapped/new type = the common tier.)
_FORMATION_INVESTIGATE_CREDITS: Dict[SpecialFormationType, int] = {
    # Common — single-sector or simple terminal topologies.
    SpecialFormationType.DEAD_END: 250,
    SpecialFormationType.WARP_SINK: 250,
    SpecialFormationType.ESCAPE_HATCH: 250,
    SpecialFormationType.BLISTER: 250,
    SpecialFormationType.LOST_SECTOR: 250,
    # Uncommon — multi-sector enclaves and bypass topologies.
    SpecialFormationType.BUBBLE: 500,
    SpecialFormationType.DEAD_END_BUBBLE: 500,
    SpecialFormationType.TUNNEL: 500,
    SpecialFormationType.BACKDOOR: 500,
    SpecialFormationType.LOST_CLUSTER: 500,
    # Rare — operator-placed / large / aggregate island formations.
    SpecialFormationType.GOLD_BUBBLE: 1000,
    SpecialFormationType.ARCHIPELAGO: 1000,
}
_FORMATION_INVESTIGATE_CREDITS_DEFAULT = 250


def _investigate_reward_credits(formation: SpecialFormation) -> int:
    """[NO-CANON] Conservative, rarity-scaled credit reward for investigating a
    formation. Unmapped/new types fall back to the common tier."""
    return _FORMATION_INVESTIGATE_CREDITS.get(
        formation.type, _FORMATION_INVESTIGATE_CREDITS_DEFAULT
    )


def find_formations_for_sector(db: Session, sector: Sector) -> List[SpecialFormation]:
    """Return every SpecialFormation that includes ``sector`` — as its anchor OR
    as one of its interior sectors. Both predicates key on the Sector UUID
    (``sector.id``): anchor via the FK, interior via GIN array containment."""
    return (
        db.query(SpecialFormation)
        .filter(
            or_(
                SpecialFormation.anchor_sector_id == sector.id,
                SpecialFormation.interior_sector_ids.contains([sector.id]),
            )
        )
        .all()
    )


def flip_formation_discovery(db: Session, player, sector: Sector) -> int:
    """Discover any undiscovered formation that includes ``sector``.

    For each matching, not-yet-discovered formation: set ``is_discovered=True``
    and, if the dedicated ``name`` column is still NULL, back-fill it from
    ``properties["name"]`` (the bang importer's only home for the name). Idempotent
    — already-discovered formations are skipped. Flush-only; caller commits.

    Returns the count of formations newly discovered (0 on a no-op visit).
    """
    flipped = 0
    for formation in find_formations_for_sector(db, sector):
        if formation.is_discovered:
            continue
        formation.is_discovered = True
        # Back-fill the public name column from the JSONB the importer populated.
        if not formation.name:
            props = formation.properties or {}
            jsonb_name = props.get("name")
            if jsonb_name:
                formation.name = jsonb_name
        flipped += 1
        logger.info(
            "Player %s discovered formation %s (%s) in sector %s",
            getattr(player, "id", None),
            formation.id,
            formation.type.name if formation.type else "?",
            sector.sector_id,
        )
    if flipped:
        db.flush()
    return flipped


# Key under which investigation state is recorded in the formation's
# ``properties`` JSONB. There is no dedicated ``is_investigated`` column, and the
# WO is additive-only / no-migration — so investigation state rides the existing
# JSONB (additive key), exactly as ``name`` lived in ``properties["name"]`` before
# ADR-0044 promoted it to a first-class column. Shape under this key:
#   {"investigated": True, "investigated_by": "<player-uuid>",
#    "investigated_at": "<iso8601>", "reward_credits": int}
_INVESTIGATION_PROP_KEY = "investigation"


def is_formation_investigated(formation: SpecialFormation) -> bool:
    """True if ``formation`` has already been investigated (state stored in the
    ``properties`` JSONB under ``_INVESTIGATION_PROP_KEY``)."""
    props = formation.properties or {}
    inv = props.get(_INVESTIGATION_PROP_KEY)
    return bool(inv and inv.get("investigated"))


def investigate_formation(
    db: Session, player, formation_id
) -> Dict[str, Any]:
    """Investigate a DISCOVERED special-formation, granting a one-time reward.

    Preconditions (raised as exceptions the route maps to HTTP status):
      * the formation must exist AND be discovered (``is_discovered``) — else
        ``FormationNotDiscoveredError`` (404). Discovery is a global row flag set
        by visiting/scanning the formation's sector (see flip_formation_discovery);
        an undiscovered formation is withheld from the player entirely, so
        investigating one is indistinguishable from "not found".
      * the formation must not already be investigated — else
        ``FormationAlreadyInvestigatedError`` (409). Investigation is one-time;
        the reward is not repeatable.

    On success: marks the formation investigated (records who/when/reward in the
    ``properties`` JSONB — additive, no schema change), grants the [NO-CANON]
    rarity-scaled credit reward to the player, and returns a payload of the
    formation details + the investigation reward. Commits (mirrors the discovery
    serializer's commit-on-write).

    Returns a dict payload:
      {
        "formation": {id, type, name, is_discovered, is_investigated, region_id,
                      anchor_sector_id},
        "reward": {"credits": int},
        "credits_remaining": int,
        "reward_is_no_canon": True,   # FLAG: reward magnitude is unspecified canon
      }
    """
    # Lock the row for the check-then-set (WO-AI review HIGH: TOCTOU) — concurrent
    # investigates serialize so the second sees the first's committed
    # investigated=True and correctly raises FormationAlreadyInvestigatedError.
    formation: Optional[SpecialFormation] = (
        db.query(SpecialFormation)
        .filter(SpecialFormation.id == formation_id)
        .populate_existing()
        .with_for_update()
        .first()
    )

    # 404 — not found OR not yet discovered (identity is withheld pre-discovery,
    # so both collapse to the same "you don't know this exists" response).
    if formation is None or not formation.is_discovered:
        raise FormationNotDiscoveredError(
            "Formation not found or not yet discovered."
        )

    # 409 — already investigated; the reward is one-time.
    if is_formation_investigated(formation):
        raise FormationAlreadyInvestigatedError(
            "Formation has already been investigated."
        )

    # Grant the [NO-CANON] rarity-scaled credit reward.
    reward_credits = _investigate_reward_credits(formation)
    player.credits = (player.credits or 0) + reward_credits

    # Record investigation state in the JSONB (additive key — no migration).
    props = dict(formation.properties or {})
    props[_INVESTIGATION_PROP_KEY] = {
        "investigated": True,
        "investigated_by": str(getattr(player, "id", "")),
        "investigated_at": datetime.now(timezone.utc).isoformat(),
        "reward_credits": reward_credits,
    }
    formation.properties = props
    # JSONB in-place reassignment can miss the dirty-tracking; flag explicitly so
    # the change is flushed (mirrors how mutable-JSONB writes are persisted).
    flag_modified(formation, "properties")

    db.commit()
    db.refresh(formation)

    logger.info(
        "Player %s investigated formation %s (%s) — reward %s credits",
        getattr(player, "id", None),
        formation.id,
        formation.type.name if formation.type else "?",
        reward_credits,
    )

    return {
        "formation": {
            "id": str(formation.id),
            "type": formation.type.value if hasattr(formation.type, "value") else str(formation.type),
            "name": formation.name,
            "is_discovered": bool(formation.is_discovered),
            "is_investigated": True,
            "region_id": str(formation.region_id) if formation.region_id else None,
            "anchor_sector_id": str(formation.anchor_sector_id) if formation.anchor_sector_id else None,
        },
        "reward": {"credits": reward_credits},
        "credits_remaining": int(player.credits),
        # FLAG: this reward magnitude is [NO-CANON] — proposed conservative value,
        # pending Max's canon ruling. See INVESTIGATE_REWARD_NO_CANON above.
        "reward_is_no_canon": INVESTIGATE_REWARD_NO_CANON,
    }
