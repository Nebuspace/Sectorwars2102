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
from typing import List

from sqlalchemy.orm import Session
from sqlalchemy import or_

from src.models.sector import Sector
from src.models.special_formation import SpecialFormation

logger = logging.getLogger(__name__)


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
