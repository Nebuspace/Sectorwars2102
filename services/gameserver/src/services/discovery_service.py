"""Planet + sector-feature discovery (ADR-0073).

First-discoverer attribution, kept separate for planets vs per-sector features
(and vs the sector's own first-discoverer on Sector.discovered_by_id), so future
hidden per-sector content can be discovered independently. The discoverer of a
planet is the only player who may rename it (claimed or not).

All marks are idempotent and first-wins: once ``discovered_by`` is set it never
changes. Flush-only — the caller owns the commit.
"""

from datetime import datetime, UTC

from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.models.planet import Planet
from src.models.sector_celestial import SectorFeatureDiscovery


def mark_planet_discovered(db: Session, planet: Planet, player_id) -> bool:
    """Record the first discoverer of a planet. Idempotent (first wins).
    Returns True only when newly set."""
    if planet.discovered_by is not None:
        return False
    planet.discovered_by = player_id
    planet.discovered_at = datetime.now(UTC)
    db.flush()
    return True


def mark_feature_discovered(db: Session, sector_uuid, feature_type: str, player_id) -> bool:
    """Record the first discoverer of a per-sector feature (belt/debris/nebula/…).
    Race-safe + idempotent via the (sector_uuid, feature_type) unique key — a
    second discoverer is a no-op. Returns True when newly inserted."""
    stmt = (
        pg_insert(SectorFeatureDiscovery.__table__)
        .values(
            sector_uuid=sector_uuid,
            feature_type=feature_type,
            discovered_by=player_id,
            discovered_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(index_elements=["sector_uuid", "feature_type"])
    )
    result = db.execute(stmt)
    db.flush()
    return bool(result.rowcount)
