"""ShipRegistry service -- append-event helper + existing-hull backfill +
report-stolen / retract-stolen-report behavioral flows.

Canon: SYSTEMS/ship-registry.md, DATA_MODELS/ships.md#shipregistry.
Schema rollout was WO-P10-green-ship-registry-schema (append-event helper +
backfill only). WO-FIX-SHIP-REGISTRY-BEHAVIORAL-ROUTES adds the first two of
the six ownership-affecting behavioral flows: report-stolen and
retract-stolen-report, wired to ``src/api/routes/ship_registry_behaviors.py``.
transfer / salvage / trade / abandon remain deferred (see that WO's report --
each needs its own dispute-window scheduler / mutual-presence / real-credits
surface, out of scope for this pass).
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from src.models.bounty_claim import BountyClaim, BountyClaimStatus
from src.models.player import Player
from src.models.ship import Ship, ShipSpecification
from src.models.ship_registry import RegistryEventType, ShipRegistry, generate_registration_number
from src.services.bounty_service import BountyService

# 50% of the ship's last appraised value (ship-registry.md "Reporting a ship
# stolen" effects #5). ``Ship.purchase_value`` is this codebase's existing
# "last appraised value" field (ship_service._calculate_insurance_payout
# uses it the same way for insurance payouts) -- no separate live-appraisal
# system exists.
STOLEN_AUTO_BOUNTY_PCT = 0.5
# The standard 10% placement fee is waived for auto-stolen-report bounties
# (ship-registry.md: "the registry doesn't double-tax").
STOLEN_AUTO_BOUNTY_FEE_PCT = 0.0
# ADR-0053 WR10 24h retract-grace window; ADR-0049's "retract within 24 hours
# of report = 75% refund" / "after 24 hours = no refund".
STOLEN_RETRACT_GRACE = timedelta(hours=24)
STOLEN_RETRACT_GRACE_REFUND_PCT = 0.75


class ShipRegistryError(Exception):
    """A canon ERR_* rejection (ship-registry.md). ``code`` is the exact
    wire-facing error code from canon; routes translate this to an HTTP
    response rather than a generic 500/400."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


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


# --- Report / retract stolen (ship-registry.md "Reporting a ship stolen") --


def _default_recovery_mode(db: Session, ship: Ship) -> str:
    """ADR-0055 S-F4 default: ``with_bounty`` for insurable hulls,
    ``no_bounty`` for non-insurable hulls, when the request omits
    ``recovery_mode``. Looked up via ShipSpecification.type (no direct FK
    from Ship to ShipSpecification exists in this codebase -- mirrors
    ShipService.get_ship_specifications' own lookup)."""
    spec = db.query(ShipSpecification).filter(ShipSpecification.type == ship.type).first()
    insurable = spec.insurable if spec is not None else True
    return "with_bounty" if insurable else "no_bounty"


def report_stolen(
    db: Session, *, ship: Ship, owner: Player, recovery_mode: Optional[str] = None,
) -> dict:
    """File a stolen report on ``ship`` (ship-registry.md "Reporting a ship
    stolen"). Raises ``ShipRegistryError`` with the exact canon ERR_* code on
    any rejection; the caller (route) owns translating that to an HTTP
    response. Flushes but does not commit -- the route owns the commit."""
    if ship.registered_owner_id != owner.id:
        raise ShipRegistryError(
            "ERR_NOT_REGISTERED_OWNER", "Only the registered owner can report this ship stolen.",
        )
    if ship.stolen_status:
        raise ShipRegistryError(
            "ERR_ALREADY_STOLEN", "This ship already has an active stolen report.",
        )

    mode = recovery_mode or _default_recovery_mode(db, ship)
    if mode not in ("with_bounty", "no_bounty"):
        raise ShipRegistryError(
            "ERR_INVALID_RECOVERY_MODE", "recovery_mode must be 'with_bounty' or 'no_bounty'.",
        )

    thief_id = ship.current_pilot_id

    # Same-team collusion block, filing half (ADR-0055 S-F1): filing against
    # your own team-mate is not a real theft. Checked against LIVE team
    # membership, not a snapshot.
    if thief_id is not None and thief_id != owner.id:
        thief = db.query(Player).filter(Player.id == thief_id).first()
        if thief is not None and owner.team_id is not None and thief.team_id == owner.team_id:
            raise ShipRegistryError(
                "ERR_THIEF_IS_TEAM_MATE", "You cannot file a stolen report against a team-mate.",
            )

    bounty_ref: Optional[str] = None
    if mode == "with_bounty" and thief_id is not None and thief_id != owner.id:
        amount = int(ship.purchase_value * STOLEN_AUTO_BOUNTY_PCT)
        if amount > 0:
            bounty_service = BountyService(db)
            result = bounty_service.place_bounty(
                owner.id, thief_id, amount, fee_pct=STOLEN_AUTO_BOUNTY_FEE_PCT,
            )
            if not result.get("success"):
                # Covers the canon-named insufficient-credits case and any
                # other place_bounty rejection (e.g. below BOUNTY_MIN_AMOUNT
                # on a low-value hull) -- the stolen flag is NOT set either
                # way, matching "the stolen flag is not set" (ship-registry.md).
                raise ShipRegistryError(
                    "ERR_INSUFFICIENT_CREDITS_FOR_AUTO_BOUNTY",
                    result.get("message", "Auto-bounty placement failed."),
                )
            bounty_ref = result["bounty_id"]

    now = datetime.now(timezone.utc)
    ship.stolen_status = True
    ship.stolen_reported_at = now
    ship.stolen_recovery_mode = mode
    ship.stolen_thief_id = thief_id
    ship.stolen_bounty_ref = bounty_ref
    ship.retract_grace_processed = False

    append_registry_event(
        db,
        ship=ship,
        event_type=RegistryEventType.STOLEN_REPORTED,
        acting_party_id=owner.id,
        new_owner_id=ship.registered_owner_id,
        event_metadata={
            "recovery_mode": mode,
            "thief_id": str(thief_id) if thief_id else None,
            "bounty_ref": bounty_ref,
        },
    )

    return {
        "ship_id": str(ship.id),
        "stolen_status": True,
        "stolen_reported_at": now.isoformat(),
        "recovery_mode": mode,
        "bounty_id": bounty_ref,
    }


def retract_stolen_report(db: Session, *, ship: Ship, owner: Player) -> dict:
    """Retract an active stolen report on ``ship`` (ship-registry.md
    "Reporting a ship stolen" retract paragraph). Raises ``ShipRegistryError``
    with the exact canon ERR_* code on rejection. Flushes but does not
    commit -- the route owns the commit."""
    if ship.registered_owner_id != owner.id:
        raise ShipRegistryError(
            "ERR_NOT_REGISTERED_OWNER", "Only the registered owner can retract this stolen report.",
        )
    if not ship.stolen_status:
        raise ShipRegistryError(
            "ERR_NOT_STOLEN", "This ship has no active stolen report.",
        )

    thief_id = ship.stolen_thief_id
    reported_at = ship.stolen_reported_at

    # Anti-collusion lock (ADR-0049 SK9): once a kill has fired against the
    # bounty (a PAID BountyClaim on the thief, claimed after this report was
    # filed), the report can no longer be retracted -- closes the
    # file -> kill -> retract -> refund collusion cycle at the most direct
    # point. Checked BEFORE any refund mutation, in the same transaction.
    if thief_id is not None and reported_at is not None:
        already_collected = (
            db.query(BountyClaim)
            .filter(
                BountyClaim.target_id == thief_id,
                BountyClaim.status == BountyClaimStatus.PAID,
                BountyClaim.claimed_at >= reported_at,
            )
            .first()
        )
        if already_collected is not None:
            raise ShipRegistryError(
                "ERR_BOUNTY_ALREADY_COLLECTED",
                "This stolen report can no longer be retracted -- the bounty has already been collected.",
            )

    now = datetime.now(timezone.utc)
    within_grace = (
        reported_at is not None
        and not ship.retract_grace_processed
        and (now - reported_at) <= STOLEN_RETRACT_GRACE
    )

    refund = 0
    if ship.stolen_bounty_ref and thief_id is not None:
        bounty_service = BountyService(db)
        refund_pct = STOLEN_RETRACT_GRACE_REFUND_PCT if within_grace else 0.0
        cancel_result = bounty_service.cancel_bounty(
            owner.id, ship.stolen_bounty_ref, thief_id, refund_pct=refund_pct,
        )
        if cancel_result.get("success"):
            refund = cancel_result.get("refund", 0)
        # A failed cancel (e.g. the bounty was already collected -- covered
        # above -- or already cancelled) is not itself an error here: the
        # retract still proceeds, just with refund staying 0.

    append_registry_event(
        db,
        ship=ship,
        event_type=RegistryEventType.STOLEN_RETRACTED,
        acting_party_id=owner.id,
        new_owner_id=ship.registered_owner_id,
        event_metadata={"refund": refund, "within_grace": within_grace},
    )

    ship.stolen_status = False
    ship.stolen_reported_at = None
    ship.stolen_recovery_mode = None
    ship.stolen_thief_id = None
    ship.stolen_bounty_ref = None
    ship.retract_grace_processed = False

    return {
        "ship_id": str(ship.id),
        "stolen_status": False,
        "refund": refund,
    }
