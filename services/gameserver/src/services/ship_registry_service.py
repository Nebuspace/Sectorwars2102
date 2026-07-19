"""ShipRegistry service -- append-event helper + existing-hull backfill.

Canon: SYSTEMS/ship-registry.md, DATA_MODELS/ships.md#shipregistry.
Schema-only rollout (WO-P10-green-ship-registry-schema) -- the report /
retract / transfer / salvage / trade / abandon gameplay flows are Wave-2;
this file only owns appending event rows and backfilling
INITIAL_REGISTRATION rows for hulls that existed before the auto-registration
mapper events (src/models/ship_registry.py) shipped. Ships created AFTER
that migration never need the backfill -- the before_insert/after_insert
listeners on Ship handle them automatically at insert time.
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from src.models.ship import Ship
from src.models.ship_registry import RegistryEventType, ShipRegistry, generate_registration_number


def append_registry_event(
    db: Session,
    *,
    ship: Ship,
    event_type: RegistryEventType,
    original_owner_id: Optional[UUID] = None,
    previous_owner_id: Optional[UUID] = None,
    new_owner_id: Optional[UUID] = None,
    acting_party_id: Optional[UUID] = None,
    transfer_fee_paid: Optional[int] = None,
    port_id: Optional[UUID] = None,
    event_metadata: Optional[dict] = None,
) -> ShipRegistry:
    """Append one immutable ShipRegistry row for ``ship``. Never updates or
    deletes an existing row -- the ledger is append-only (ship rows keep
    their full history even after destruction). Flushes but does not
    commit; the caller's route owns the transaction boundary (this
    codebase's route-commits/service-flushes convention)."""
    row = ShipRegistry(
        ship_id=ship.id,
        registration_number=ship.registration_number,
        event_type=event_type,
        original_owner_id=original_owner_id,
        previous_owner_id=previous_owner_id,
        new_owner_id=new_owner_id,
        acting_party_id=acting_party_id,
        transfer_fee_paid=transfer_fee_paid,
        port_id=port_id,
        created_at=datetime.now(timezone.utc),
        event_metadata=event_metadata or {},
    )
    db.add(row)
    db.flush()
    return row


def _unique_registration_number(db: Session) -> str:
    """A REG number not already present in ships.registration_number at
    call time (bounded retry, mirrors the before_insert listener's
    collision check in src/models/ship_registry.py)."""
    candidate = generate_registration_number()
    for _ in range(10):
        candidate = generate_registration_number()
        if db.query(Ship.id).filter(Ship.registration_number == candidate).first() is None:
            return candidate
    return candidate  # pragma: no cover -- alphabet is 33**4/year, exhaustion is not realistically reachable


def backfill_initial_registrations(db: Session) -> int:
    """One-time backfill: emit an INITIAL_REGISTRATION ShipRegistry row for
    every existing Ship that doesn't already have one (hulls created before
    the auto-registration mapper events shipped). Idempotent -- a ship that
    already has an INITIAL_REGISTRATION row is skipped, so re-running is
    safe. Ships missing a registration_number get one assigned here too
    (mirrors the before_insert listener's generator, since pre-existing
    rows never went through it). Returns the number of ships backfilled.

    Not wired to any route or scheduler by this WO (schema-only scope) --
    invoke manually (e.g. a one-off admin script) when ready to run it
    against existing data.
    """
    already_registered_ship_ids = {
        row.ship_id
        for row in db.query(ShipRegistry.ship_id).filter(
            ShipRegistry.event_type == RegistryEventType.INITIAL_REGISTRATION
        )
    }
    backfilled = 0
    for ship in db.query(Ship).all():
        if ship.id in already_registered_ship_ids:
            continue
        if not ship.registration_number:
            ship.registration_number = _unique_registration_number(db)
        if ship.registered_owner_id is None and ship.owner_id is not None:
            ship.registered_owner_id = ship.owner_id
        append_registry_event(
            db,
            ship=ship,
            event_type=RegistryEventType.INITIAL_REGISTRATION,
            original_owner_id=ship.registered_owner_id,
            new_owner_id=ship.registered_owner_id,
            acting_party_id=ship.registered_owner_id,
        )
        backfilled += 1
    db.flush()
    return backfilled
