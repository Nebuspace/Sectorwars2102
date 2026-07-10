"""ShipRegistry -- append-only ownership/possession event log for hulls.

Canon: SYSTEMS/ship-registry.md (ADR-0008 S9 ship-registry overhaul),
DATA_MODELS/ships.md#shipregistry. Schema-only rollout
(WO-P10-green-ship-registry-schema) -- no report/retract/transfer/salvage
gameplay lives here yet (Wave-2). This file owns:
  - the ShipRegistry model itself (the append-only ledger table)
  - RegistryEventType (the event_type enum)
  - the REG-XXXX-YYYY registration-number generator
  - the SQLAlchemy mapper events that auto-register every new Ship: a
    before_insert hook assigns registration_number/registered_owner_id, and
    an after_insert hook appends the hull's INITIAL_REGISTRATION row -- so
    every ship-creation call site in the codebase gets a registry entry for
    free, with zero call-site changes.

DOC CONFLICT NOTE (WO instruction: "if ship-registry.md conflicts with the
WO sketch, follow the DOC"): the dispatching WO's column list for Ship
omitted `registered_owner_id`, but both ship-registry.md's source map and
DATA_MODELS/ships.md:44 ("formerly owner_id") name it as a required Ship
state addition alongside current_pilot_id/stolen_status/stolen_reported_at.
Added on Ship (src/models/ship.py) additive-nullable, matching the WO's own
"all ADDITIVE NULLABLE" instruction for the rest of the column set.

NO-CANON: the `YYYY` half of REG-XXXX-YYYY is documented as "game-time, not
real-time; matches the in-universe calendar" (ship-registry.md "Registration
number format"), but no in-universe calendar/epoch service exists anywhere
in this codebase (checked src/core/game_time.py + src/core/*, src/services/*
-- only GAME_TIME_SCALE wall-clock compression exists, no year-progression
helper). Interim: a fixed epoch constant matching the doc's own examples
(REG-A47B-2103, REG-XQF9-2104). A later WO should replace
_REGISTRATION_YEAR with a real elapsed-time -> game-year conversion once an
in-universe calendar service exists -- flagging for Samantha/orchestrator
rather than inventing one here.
"""

import enum
import uuid
from datetime import datetime, timezone
from secrets import choice as _secrets_choice
from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime, Enum as SQLEnum, ForeignKey, Integer, String, event, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from src.core.database import Base
from src.models.ship import Ship

if TYPE_CHECKING:
    from src.models.player import Player
    from src.models.station import Station


class RegistryEventType(str, enum.Enum):
    """ShipRegistry.event_type -- ship-registry.md's six ownership-affecting
    events plus the terminal ARCHIVED state (DATA_MODELS/ships.md#shipregistry).
    Values are lowercase to match the codebase's enum-serialization
    convention (values_callable pins the PG label to .value, not .name --
    see BountyClaimStatus / WarpLayer for the established pattern)."""
    INITIAL_REGISTRATION = "initial_registration"
    OWNERSHIP_TRANSFER = "ownership_transfer"
    STOLEN_REPORTED = "stolen_reported"
    STOLEN_RETRACTED = "stolen_retracted"
    IMPOUNDED = "impounded"
    ARCHIVED = "archived"


class ShipRegistry(Base):
    """Append-only ownership/possession ledger. One row per registry event;
    rows are never updated or deleted, even after the ship is destroyed
    (DATA_MODELS/ships.md#shipregistry "Lifecycle")."""

    __tablename__ = "ship_registry"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # No ondelete action (deliberately NOT CASCADE): the registry must
    # outlive the ship row per the append-only/audit-trail invariant
    # (ship-registry.md Invariant 7 -- "After ship destruction the Ship row
    # enters DESTROYED, but ShipRegistry rows remain for historical lookup").
    # Ships are never hard-deleted in this codebase (status=DESTROYED is the
    # terminal state, mirroring CargoWreck's destruction handling), so the
    # DB-default RESTRICT-on-delete this leaves in place is a feature, not a
    # gap: it structurally blocks ever hard-deleting a hull with history.
    ship_id = Column(UUID(as_uuid=True), ForeignKey("ships.id"), nullable=False, index=True)
    # Redundant against Ship.registration_number for fast lookup without a
    # join (DATA_MODELS/ships.md#shipregistry "Indexes").
    registration_number = Column(String(15), nullable=False, index=True)
    event_type = Column(
        SQLEnum(RegistryEventType, name="registry_event_type", values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
    )
    # First-ever registered owner; immutable across every row for a given
    # ship (Invariant 2). Nullable here (canon's target is "not null")
    # because NPC-piloted hulls (Ship.owner_id NULL, Ship.is_npc True) have
    # no owner to record -- canon's target assumes every hull is
    # player-owned, which the NPC fleet contradicts.
    original_owner_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="SET NULL"), nullable=True)
    previous_owner_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="SET NULL"), nullable=True)
    new_owner_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="SET NULL"), nullable=True)
    acting_party_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="SET NULL"), nullable=True)
    transfer_fee_paid = Column(Integer, nullable=True)
    port_id = Column(UUID(as_uuid=True), ForeignKey("stations.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # Python attribute renamed from canon's "metadata" -- that name collides
    # with SQLAlchemy's Base.metadata (the MetaData object every declarative
    # model inherits). Matches the established codebase workaround (see
    # AIComprehensiveAssistant.source_metadata, GameEvent.log_metadata,
    # PlayerActivity.activity_metadata) of renaming both the attribute and
    # the column together rather than fighting the collision with an
    # explicit Column("metadata", ...) mapping.
    event_metadata = Column(JSONB, nullable=False, default=dict)

    ship = relationship("Ship", back_populates="registry_history", foreign_keys=[ship_id])
    original_owner = relationship("Player", foreign_keys=[original_owner_id])
    previous_owner = relationship("Player", foreign_keys=[previous_owner_id])
    new_owner = relationship("Player", foreign_keys=[new_owner_id])
    acting_party = relationship("Player", foreign_keys=[acting_party_id])
    port = relationship("Station", foreign_keys=[port_id])

    def __repr__(self):
        return f"<ShipRegistry {self.event_type} ship={self.ship_id}>"


# --- REG-XXXX-YYYY generation -------------------------------------------

# A-Z minus ambiguous I/O, 0-9 minus ambiguous 0/1 (ship-registry.md
# "Registration number format").
_REG_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

# NO-CANON interim epoch -- see module docstring.
_REGISTRATION_YEAR = 2103


def _generate_registration_block() -> str:
    """Pure: a random 4-character REG alphanumeric block. No DB access, no
    uniqueness check -- callers handle collision retry."""
    return "".join(_secrets_choice(_REG_ALPHABET) for _ in range(4))


def generate_registration_number(year: int = _REGISTRATION_YEAR) -> str:
    """Pure: a REG-XXXX-YYYY registration number for the given game year."""
    return f"REG-{_generate_registration_block()}-{year:04d}"


def build_initial_registration_values(ship: Ship) -> dict:
    """Pure: the ShipRegistry column values for ``ship``'s
    INITIAL_REGISTRATION row. ``ship`` must already carry
    id/registration_number/registered_owner_id -- the before_insert listener
    below sets those first (SQLAlchemy always runs before_insert for a row
    before its after_insert)."""
    return {
        "id": uuid.uuid4(),
        "ship_id": ship.id,
        "registration_number": ship.registration_number,
        "event_type": RegistryEventType.INITIAL_REGISTRATION.value,
        "original_owner_id": ship.registered_owner_id,
        "previous_owner_id": None,
        "new_owner_id": ship.registered_owner_id,
        "acting_party_id": ship.registered_owner_id,
        "transfer_fee_paid": None,
        "port_id": None,
        "created_at": datetime.now(timezone.utc),
        "event_metadata": {},
    }


@event.listens_for(Ship, "before_insert")
def _assign_registration_fields(mapper, connection, target):
    """Auto-assign registration_number + backfill registered_owner_id from
    owner_id for every new Ship row -- zero changes needed at any of the
    many existing ship-creation call sites across the codebase."""
    if not target.registration_number:
        candidate = generate_registration_number()
        # Bounded collision retry against the table the row is about to
        # land in. Alphabet is 33**4 (~1.19M) per year, so a collision is
        # rare, but a live check is cheap and this generator must never
        # violate the unique constraint.
        ships_table = Ship.__table__
        for _ in range(10):
            exists = connection.execute(
                ships_table.select().where(ships_table.c.registration_number == candidate)
            ).first()
            if exists is None:
                break
            candidate = generate_registration_number()
        target.registration_number = candidate
    if target.registered_owner_id is None and target.owner_id is not None:
        target.registered_owner_id = target.owner_id


@event.listens_for(Ship, "after_insert")
def _emit_initial_registration_event(mapper, connection, target):
    """Append the hull's INITIAL_REGISTRATION row. Uses Core (the
    ``connection`` mapper events hand over), not the ORM Session -- the
    flush that triggers after_insert is still in progress, so a Session
    write here would re-enter it."""
    connection.execute(ShipRegistry.__table__.insert().values(**build_initial_registration_values(target)))
