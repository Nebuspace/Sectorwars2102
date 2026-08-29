"""
Shared "friendly port" reputation gate.

Extracted from src.api.routes.ship_upgrades (WO-FLEET-RESUPPLY-FRIENDLY-STATION)
so both a routes-layer consumer (ship_upgrades.py, insurance purchase) and a
services-layer consumer (fleet_service.py, fleet resupply) can share the exact
same check without a backwards services->routes import. This module owns the
canonical resolution logic; ship_upgrades.py re-exports from here rather than
keeping its own copy.

Convention (insurance-factionless-port-gate, ratified 2026-08-07 and reused
unmodified here per fleet-resupply-friendly-station): a station is "friendly"
if it has no resolvable controlling faction (always passes — unaffiliated
ports have no faction to be unfriendly with) OR the player has at least
NEUTRAL standing with the station's controlling faction.
"""

from typing import Optional

from sqlalchemy.orm import Session

from src.models.faction import Faction
from src.models.reputation import ReputationLevel
from src.models.station import Station

# ReputationLevel is declared low->high (PUBLIC_ENEMY .. EXALTED), so a
# member's index in the enum's declaration order IS its monotonic rank.
_REPUTATION_RANK = {level: rank for rank, level in enumerate(ReputationLevel)}


def _player_reputation_level_for_faction(db: Session, player_id, faction: Optional[Faction]) -> ReputationLevel:
    """Effective ReputationLevel with `faction` (or NEUTRAL if `faction` is
    None). Uses ``resolve_effective_faction_standing_value`` so teamed players
    honor team AVERAGE standing (factions-and-teams.md); solo path maps the
    personal value through FactionService level thresholds (0 → NEUTRAL)."""
    if faction is None:
        return ReputationLevel.NEUTRAL
    from src.services.faction_service import (
        FactionService,
        resolve_effective_faction_standing_value,
    )

    value, _source = resolve_effective_faction_standing_value(
        db, player_id, faction.id
    )
    return FactionService(db)._calculate_reputation_level(value)


def _station_controlling_faction(db: Session, station: Station) -> Optional[Faction]:
    """Resolve a station's controlling Faction row.

    Station.faction_affiliation carries the faction DISPLAY NAME, matched
    against Faction.name — the established resolution pattern shared by
    docking_service._player_faction_rep_for_station, construction_service, and
    emergent_reputation_service.trade_volume_action_for_faction_name. None for
    an unaffiliated station or a name that doesn't resolve to a seeded Faction
    row.
    """
    faction_name = getattr(station, "faction_affiliation", None)
    if not faction_name:
        return None
    return db.query(Faction).filter(Faction.name == faction_name).first()


def check_friendly_port(db: Session, player_id, station: Station):
    """Return (ok: bool, reason: Optional[str]) for the "friendly port" gate
    (ship-insurance.md:48 "Buying insurance ... requires at least NEUTRAL
    reputation with the controlling faction"; fleet-tactics.md:63 requires the
    same standing for fleet resupply).

    NO-CANON->RULED: a station with no controlling faction (no
    faction_affiliation, or a name that doesn't resolve to a seeded Faction
    row) has no faction to be unfriendly with, so it always passes. DECISION
    `insurance-factionless-port-gate` (2026-08-07): ratify always-friendly-by-
    design for unaffiliated ports — this pass is intentional, not a gap.
    """
    faction = _station_controlling_faction(db, station)
    if faction is None:
        return True, None

    player_level = _player_reputation_level_for_faction(db, player_id, faction)
    if _REPUTATION_RANK[player_level] < _REPUTATION_RANK[ReputationLevel.NEUTRAL]:
        return False, (
            f"ERR_UNFRIENDLY_PORT: insurance requires at least NEUTRAL standing "
            f"with {faction.name} (you are {player_level.value})"
        )
    return True, None
