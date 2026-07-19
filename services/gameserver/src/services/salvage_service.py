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
    during that window, but doing so flags them Suspect. The full lifecycle
    (timer extension capped at 4h cumulative, team-snapshot-once, -25
    personal rep per event -- ships.md:287-296, ADR-0061 S-V4,
    WO-CMB-SUSPECT-LIFE-1) is owned by ``src.services.suspect_service``,
    not hand-rolled here. After the hour elapses anyone salvages freely with
    no Suspect flag.
  - ships.md:275-277 (WO-CMB-SALVAGE-LOOP-1): salvage is time-cost gated at
    1 turn per 100 cargo units retrieved (rounded up), charged to every
    salvager regardless of grace/exemption -- the grace window only exempts
    the Suspect flag, never the time cost. Combat-interruption (ships.md:277)
    is NOT built here -- turns are charged up-front for the whole pass.

Cargo space mirrors the canonical ship-hold shape used by the trading route:
``ship.cargo == {'used': int, 'capacity': int, 'contents': {commodity: int}}``.
We transfer as much wreck cargo as fits the ship's FREE space (capacity - used),
decrement the wreck by exactly what was taken, and JSONB-flag both mutated rows.
"""

import math
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.cargo_wreck import CargoWreck
from src.models.player import Player
from src.models.sector import Sector
from src.models.ship import effective_cargo_capacity
from src.services import suspect_service
from src.services.turn_service import regenerate_turns, spend_turns

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


def grace_status(wreck: CargoWreck, player: Player, *, now: Optional[datetime] = None):
    """Returns ``(in_grace, exempt)`` for ``player`` salvaging ``wreck`` right
    now (ADR-0007 + ADR-0055 S-F2) -- the exact same computation
    ``salvage_wreck`` feeds into its Suspect-flag decision below, extracted
    so the wreck-listing endpoint's ``would_flag_suspect`` preview
    (WO-CMB-SALVAGE-LOOP-1 Lane 1, routes/sectors.py) can share ONE
    definition instead of re-deriving the exemption rule and risking drift.

    ``exempt`` mirrors ``original_team_id``'s CURRENT-membership contract
    (cargo_wreck.py) -- team-mate exemption checks live team_id, not a
    frozen roster snapshot."""
    now = now or datetime.now(timezone.utc)
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
    return in_grace, exempt


def salvage_wreck(db: Session, player: Player, wreck_id, quantity: Optional[int] = None) -> Dict:
    """Salvage a CargoWreck, capped by whichever is tightest of: free cargo
    hold, ``quantity`` if the caller requested a specific amount (None =
    take as much as fits, the pre-WO-CMB-SALVAGE-LOOP-1 default), and
    available turns (1 turn / 100 units, rounded up -- ships.md:275-277).

    Turn cost is charged to EVERY salvager, exempt or not (canon: no grace
    exemption from the time cost, only from the Suspect flag). Charged
    up-front for the whole pass -- combat-interruption (ships.md:277) is
    not built here (WO-CMB-SALVAGE-LOOP-1 divergence, tracked separately).

    Returns {'salvaged': {commodity: qty}, 'suspect_flagged': bool,
             'wreck_cleared': bool, 'turns_spent': int}.
    """
    # --- locate the wreck ---
    if isinstance(wreck_id, str):
        try:
            wreck_id = UUID(wreck_id)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Invalid wreck id")

    # Reject a non-positive explicit request before any row lock / mutation.
    if quantity is not None and quantity <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Requested salvage quantity must be positive")

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

    # --- [NO-CANON WO-CMB-SALVAGE-LOOP-1] empty-manifest free-clear ---
    # The model's own invariant (cargo_wreck.py) is that a row is deleted
    # the INSTANT its cargo map empties -- one should never be findable
    # here with {}/all-zero entries. Defensive only (a race or a stray
    # zero-value entry): clear it at zero turn cost rather than running it
    # through the transfer loop below and charging for a no-op salvage.
    if not any(int(v or 0) > 0 for v in (wreck.cargo or {}).values()):
        db.delete(wreck)
        db.commit()
        return {"salvaged": {}, "suspect_flagged": False, "wreck_cleared": True, "turns_spent": 0}

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

    # --- turn affordability (ADR-0004 continuous lazy regen, same pattern
    # every other turn-spend site uses) + compose the tightest cap ---
    regenerate_turns(db, player)
    if player.turns <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="No turns available to salvage")
    turn_cap = player.turns * 100
    budget = free if quantity is None else min(free, quantity)
    budget = min(budget, turn_cap)

    # --- grace / exempt / Suspect (ADR-0007 + ADR-0055 S-F2) ---
    now = datetime.now(timezone.utc)
    in_grace, exempt = grace_status(wreck, player, now=now)

    # Outside-team salvage during grace is ALLOWED but flags the salvager
    # Suspect (WO-CMB-SUSPECT-LIFE-1: +1h timer capped at 4h cumulative,
    # team snapshot once at first acquisition, -25 personal rep -- every
    # early-salvage event, first or repeat). suspect_flagged mirrors the
    # pre-existing "was this a FRESH acquisition" contract this function's
    # callers already depend on.
    suspect_flagged = False
    if in_grace and not exempt:
        suspect_flagged = suspect_service.apply_suspect_event(
            db, player, reason="early_salvage", now=now
        )

    # --- transfer as much as fits, decrement the wreck ---
    wreck_cargo = dict(wreck.cargo or {})
    contents = dict(cargo.get("contents", {}))
    salvaged: Dict[str, int] = {}

    for commodity, available in list(wreck_cargo.items()):
        if budget <= 0:
            break
        available = int(available or 0)
        if available <= 0:
            # Strip a junk/zero entry so it can't keep the row alive forever.
            del wreck_cargo[commodity]
            continue
        take = min(available, budget)
        contents[commodity] = contents.get(commodity, 0) + take
        salvaged[commodity] = salvaged.get(commodity, 0) + take
        remaining = available - take
        if remaining > 0:
            wreck_cargo[commodity] = remaining
        else:
            del wreck_cargo[commodity]
        budget -= take

    # --- turn cost: charged for the pass AFTER the actual transferred
    # amount is known (composes correctly with the cargo/quantity caps
    # above -- e.g. free hold 50 + 10 turns available -> 50 units, 1 turn,
    # not the full turns budget). Every salvager pays this, exempt or not
    # -- grace only exempts the Suspect flag, never the time cost. In-memory
    # mutation only (spend_turns), folded into this function's single final
    # commit alongside the cargo/wreck writes -- atomic together.
    units_transferred = sum(salvaged.values())
    turns_required = math.ceil(units_transferred / 100) if units_transferred > 0 else 0
    if turns_required > 0:
        spend_turns(player, turns_required)

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
        "turns_spent": turns_required,
    }
