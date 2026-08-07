"""ADR-0054 X-D3 -- GC-lapse 7-day liquidation window in-window actions.

Owns the ``POST /api/v1/players/me/gc-emergency-relocation`` mechanic: a
one-time, free teleport (mirrors slipdrive_service's escape-jump player-state
sync -- sector/region/dock/land flags, ship sector, presence broadcast) to one
of the lapsed player's foreign-region holdings, consumed once per lapse
cycle. Renewed the moment the player re-subscribes (paypal_service clears
``gc_relocation_used_at`` alongside ``gc_lapsed_at``).

Everything else the ADR names for the 7-day window (physical safe-withdraw at
the destination, NPC station buyback, voluntary surrender) rides EXISTING
routes unmodified -- this module only enumerates the foreign-region holding
list the relocation endpoint needs and performs the teleport itself.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import UUID

from sqlalchemy.orm import Session

from src.models.planet import Planet
from src.models.player import Player
from src.models.station import Station

logger = logging.getLogger(__name__)


class GCLapseError(ValueError):
    """Raised on a precondition failure; routes map these to 4xx."""


def list_foreign_holdings(db: Session, player: Player) -> List[Dict[str, Any]]:
    """The player's owned planets + stations in a region OTHER than their
    ``home_region_id`` (a NULL home_region_id means every owned asset counts
    as foreign, matching the ADR's "regions outside your home region")."""
    holdings: List[Dict[str, Any]] = []

    planet_q = db.query(Planet).filter(Planet.owner_id == player.id)
    for planet in planet_q:
        if planet.region_id != player.home_region_id:
            holdings.append({
                "asset_type": "planet",
                "asset_id": str(planet.id),
                "name": planet.name,
                "region_id": str(planet.region_id) if planet.region_id else None,
                "sector_id": planet.sector_id,
            })

    station_q = db.query(Station).filter(Station.owner_id == player.id)
    for station in station_q:
        if station.region_id != player.home_region_id:
            holdings.append({
                "asset_type": "station",
                "asset_id": str(station.id),
                "name": station.name,
                "region_id": str(station.region_id) if station.region_id else None,
                "sector_id": station.sector_id,
            })

    return holdings


def _lock_player(db: Session, player_id: UUID) -> Player:
    """Mirrors slipdrive_service._lock_player -- row-lock the player before
    any GC-lapse mutation."""
    player = (
        db.query(Player)
        .filter(Player.id == player_id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if not player:
        raise GCLapseError("player_not_found")
    return player


def emergency_relocate(
    db: Session,
    player_id: UUID,
    asset_type: str,
    asset_id: UUID,
) -> Dict[str, Any]:
    """Consume the one-time GC-bypass emergency relocation (ADR-0054 X-D3).

    Preconditions:
      * the player must currently be in a GC-lapse window (``gc_lapsed_at``
        set -- the window this grant exists for),
      * the grant must not already be consumed this cycle
        (``gc_relocation_used_at`` NULL),
      * the target asset must be owned by the player and in a foreign region
        (a non-holding or home-region destination is not what this bypass is
        for -- the player can fly there normally).

    Locks the player row (mirrors slipdrive_service.complete_charge), mutates
    player + (if present) player.current_ship sector/region and presence,
    mirroring slipdrive_service.complete_charge's teleport-arrival sync.
    Marks ``gc_relocation_used_at = now()``. Does NOT commit -- rides the
    route's transaction."""
    player = _lock_player(db, player_id)
    if player.gc_lapsed_at is None:
        raise GCLapseError("not_lapsed")
    if player.gc_relocation_used_at is not None:
        raise GCLapseError("already_used")

    if asset_type == "planet":
        asset = db.query(Planet).filter(Planet.id == asset_id).first()
    elif asset_type == "station":
        asset = db.query(Station).filter(Station.id == asset_id).first()
    else:
        raise GCLapseError("invalid_asset_type")

    if asset is None or asset.owner_id != player.id:
        raise GCLapseError("not_owner")
    if asset.region_id == player.home_region_id:
        raise GCLapseError("not_foreign")

    old_sector_id = player.current_sector_id
    destination_sector_id = asset.sector_id
    destination_region_id = asset.region_id

    if destination_sector_id != old_sector_id:
        player.current_sector_id = destination_sector_id
        player.current_region_id = destination_region_id
        player.is_docked = False
        player.is_landed = False
        player.current_port_id = None
        player.current_planet_id = None
        from src.services.docking_service import release as _release_docking_slip
        _release_docking_slip(db, None, player)

        ship = player.current_ship
        if ship is not None:
            ship.sector_id = destination_sector_id

        from src.services.movement_service import MovementService
        MovementService(db)._update_player_presence(player, old_sector_id, destination_sector_id)

    player.gc_relocation_used_at = datetime.now(timezone.utc)

    db.flush()

    logger.info(
        "GC-emergency-relocation: player %s teleported to %s %s (sector %s)",
        player.id, asset_type, asset_id, destination_sector_id,
    )

    return {
        "outcome": "gc_emergency_relocation",
        "asset_type": asset_type,
        "asset_id": str(asset_id),
        "destination_sector_id": destination_sector_id,
        "destination_region_id": str(destination_region_id) if destination_region_id else None,
    }
