"""Salvage service — a player pulls cargo out of a CargoWreck in their sector.

Canon:
  - DATA_MODELS/cargo-wrecks.md — the CargoWreck schema + lifecycle (cargo is a
    ``{commodity: int}`` JSONB map that DECREMENTS as it is salvaged; the row is
    DELETED the instant the map becomes empty ``{}`` — empty is the only
    terminal state, there is no decay timer).
  - ADR-0007 (grace / Suspect) + ADR-0055 S-F2 (killing-blow attribution):
    for 1 hour from ``created_at`` only three parties may salvage exemptly —
    the ``original_owner_id``, a CURRENT team-mate of ``original_team_id``, and
    the ``killing_blow_pilot_id``. An OUTSIDE-team salvager may STILL salvage
    during that window, but doing so flags them Suspect (sets the existing
    ``Player.is_suspect`` + ``Player.suspect_declared_at``). After the hour
    elapses anyone salvages freely with no Suspect flag.

Cargo space mirrors the canonical ship-hold shape used by the trading route:
``ship.cargo == {'used': int, 'capacity': int, 'contents': {commodity: int}}``.
We transfer as much wreck cargo as fits the ship's FREE space (capacity - used),
decrement the wreck by exactly what was taken, and JSONB-flag both mutated rows.
"""

from datetime import datetime, timezone, timedelta
from typing import Dict
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.player import Player
from src.models.sector import Sector
from src.models.cargo_wreck import CargoWreck
from src.models.ship import effective_cargo_capacity

# ADR-0007: the salvage grace / Suspect window is 1 hour from wreck birth.
GRACE_WINDOW = timedelta(hours=1)

# Canonical empty-hold fallback (matches the trading route's cargo contract).
_DEFAULT_CARGO = {"used": 0, "capacity": 50, "contents": {}}


def _player_sector_uuid(db: Session, player: Player):
    """Resolve the player's CURRENT sector UUID (cargo_wrecks.sector_id is a
    sector UUID; player.current_sector_id is the int sector number). Scoped to
    the player's region exactly like the sector GET routes."""
    q = db.query(Sector).filter(Sector.sector_id == player.current_sector_id)
    if player.current_region_id:
        q = q.filter(Sector.region_id == player.current_region_id)
    else:
        q = q.filter(Sector.region_id == None)  # noqa: E711 (SQLAlchemy IS NULL)
    sector = q.first()
    return sector.id if sector else None


def salvage_wreck(db: Session, player: Player, wreck_id) -> Dict:
    """Salvage as much of a CargoWreck as fits the player's free hold.

    Returns {'salvaged': {commodity: qty}, 'suspect_flagged': bool,
             'wreck_cleared': bool}.
    """
    # --- locate the wreck ---
    if isinstance(wreck_id, str):
        try:
            wreck_id = UUID(wreck_id)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Invalid wreck id")

    # Lock the wreck row FOR UPDATE before the read-modify-write: two concurrent
    # salvagers on the same wreck would otherwise both read the pre-decrement
    # cargo and both transfer it (cargo duplication). The lock serializes them —
    # the second blocks until the first commits, then sees the decremented (or
    # deleted) wreck (reviewer HIGH gate-fix).
    wreck = (
        db.query(CargoWreck)
        .filter(CargoWreck.id == wreck_id)
        .with_for_update()
        .first()
    )
    if not wreck:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Wreck not found")

    # --- player must be in the wreck's sector ---
    player_sector_uuid = _player_sector_uuid(db, player)
    if player_sector_uuid is None or wreck.sector_id != player_sector_uuid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="You are not in this wreck's sector")

    # --- the acting ship + its free hold space ---
    ship = player.current_ship
    if ship is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="No active ship to salvage into")
    cargo = ship.cargo or dict(_DEFAULT_CARGO)
    used = cargo.get("used", 0)
    # The pickup ceiling MUST honor the Cargo-Hold ship-mod bonus, so read the
    # effective (post-bonus) capacity, not the raw base (ship.py:166).
    capacity = effective_cargo_capacity(ship)
    free = capacity - used
    if free <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="No free cargo space")

    # --- grace / exempt / Suspect (ADR-0007 + ADR-0055 S-F2) ---
    now = datetime.now(timezone.utc)
    created = wreck.created_at
    # created_at is timezone-aware (DateTime(timezone=True)); guard a naive row.
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    in_grace = created is not None and (now - created) < GRACE_WINDOW

    exempt = (
        (wreck.original_owner_id is not None and wreck.original_owner_id == player.id)
        or (wreck.killing_blow_pilot_id is not None and wreck.killing_blow_pilot_id == player.id)
        or (wreck.original_team_id is not None
            and player.team_id is not None
            and player.team_id == wreck.original_team_id)
    )

    suspect_flagged = False
    if in_grace and not exempt:
        # Outside-team salvage during grace is ALLOWED but flags the salvager
        # Suspect (existing Player fields, models/player.py:86-88).
        if not player.is_suspect:
            player.is_suspect = True
            player.suspect_declared_at = now
            suspect_flagged = True
        else:
            # Already suspect — re-stamp so the lifecycle clock reflects the
            # latest offense without "downgrading" an existing flag.
            player.suspect_declared_at = now

    # --- transfer as much as fits, decrement the wreck ---
    wreck_cargo = dict(wreck.cargo or {})
    contents = dict(cargo.get("contents", {}))
    salvaged: Dict[str, int] = {}

    for commodity, available in list(wreck_cargo.items()):
        if free <= 0:
            break
        available = int(available or 0)
        if available <= 0:
            # Strip a junk/zero entry so it can't keep the row alive forever.
            del wreck_cargo[commodity]
            continue
        take = min(available, free)
        contents[commodity] = contents.get(commodity, 0) + take
        salvaged[commodity] = salvaged.get(commodity, 0) + take
        remaining = available - take
        if remaining > 0:
            wreck_cargo[commodity] = remaining
        else:
            del wreck_cargo[commodity]
        free -= take

    # Persist the ship hold (canonical {used, capacity, contents} shape).
    cargo["contents"] = contents
    cargo["used"] = used + sum(salvaged.values())
    cargo["capacity"] = capacity
    ship.cargo = cargo
    flag_modified(ship, "cargo")

    # --- wreck row: DELETE when emptied, else write back the decremented map ---
    wreck_cleared = False
    if not wreck_cargo:
        db.delete(wreck)
        wreck_cleared = True
    else:
        wreck.cargo = wreck_cargo
        flag_modified(wreck, "cargo")

    db.commit()

    return {
        "salvaged": salvaged,
        "suspect_flagged": suspect_flagged,
        "wreck_cleared": wreck_cleared,
    }
