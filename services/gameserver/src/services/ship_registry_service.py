"""ShipRegistry service -- append-event helper + existing-hull backfill +
report-stolen / retract-stolen-report / abandon / claim / trade-sell /
contested-transfer behavioral flows.

Canon: SYSTEMS/ship-registry.md, DATA_MODELS/ships.md#shipregistry.
Schema rollout was WO-P10-green-ship-registry-schema (append-event helper +
backfill only). WO-FIX-SHIP-REGISTRY-BEHAVIORAL-ROUTES added report-stolen /
retract-stolen-report. WO-FIX-SHIP-REGISTRY-TRANSFER-SALVAGE-TRADE-ABANDON
adds abandon / claim (ship-registry.md "Abandonment") and trade-sell / trade
-respond (ship-registry.md "Trading" -- peer-to-peer sale).
WO-BUILD-SHIP-REGISTRY-CONTESTED-TRANSFER-SALVAGE-CLAIM adds the contested
registration-transfer flow itself (ship-registry.md "Legal ownership
transfer" -- 30% fee, 24h owner-dispute window): file_transfer_claim /
approve_transfer_claim / _complete_transfer_claim here, the auto-complete
scheduler sweep in economy_sweeps.py (mirroring the stolen-ship-rep-penalty
sweep's discipline), and a cancel-on-stolen-report hook added to
report_stolen below.
WO-FIX-SHIP-REGISTRY-AUTO-ARCHIVE-MISSING: archive_expired_abandoned_ships
(7 real-day auto-archive → DESTROYED + CargoWreck) + scheduler wrapper.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.bounty_claim import BountyClaim, BountyClaimStatus
from src.models.player import Player
from src.models.ship import Ship, ShipSpecification, ShipStatus, ShipType
from src.models.ship_registry import RegistryEventType, ShipRegistry, generate_registration_number
from src.services.bounty_service import BountyService
from src.services.turn_service import regenerate_turns, spend_turns
from src.services.wanted_service import recompute_is_wanted

logger = logging.getLogger(__name__)

# ship-registry.md "Abandonment" step 5 — 7 real-time days unclaimed.
ABANDONMENT_ARCHIVE_DAYS = 7

# 30% of the ship's appraised value, charged to the claimant AT FILING TIME
# (ship-registry.md "Legal ownership transfer" step 2, "Fee assessment" --
# precedes the notification/dispute-window steps). ``Ship.purchase_value``
# is this codebase's "last appraised value" field, same convention as
# STOLEN_AUTO_BOUNTY_PCT above. The fee is a pure credit sink (no receiving
# party is named anywhere in canon -- it is a registry/port processing fee,
# not a payment to the previous owner, mirroring bounty_service.place_bounty's
# placement-fee sink), fully refunded only if the owner cancels the claim by
# filing a stolen report mid-window (canon failure-modes table: "Fee
# refunded"); kept on successful completion (auto-complete or owner-approve).
TRANSFER_CLAIM_FEE_PCT = 0.30
TRANSFER_CLAIM_DISPUTE_WINDOW = timedelta(hours=24)

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

    Wired at POST /admin/ships/registry/backfill (admin_comprehensive.py's
    admin_backfill_ship_registry, SHIPS_MANAGE-scoped) -- verified
    2026-08-08, "not wired" here was stale. Idempotent, so admins can
    invoke it more than once safely.
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

    # Cancel-on-stolen-report (ship-registry.md "Legal ownership transfer"
    # step 4 / failure-modes table: "Original owner files stolen report
    # mid-transfer -> Transfer canceled; claimant becomes Wanted. Fee
    # refunded."). The claimant becoming Wanted is already covered below --
    # thief_id is the current pilot, which for a pending-transfer ship is
    # ordinarily the claimant (Borrowed >=1h eligibility path) or nobody
    # (Drifting path, no Wanted trigger either way). Refund the exact fee
    # paid at filing; the claimant row needs its own lock since it wasn't
    # locked by the route (only the ship row was).
    cancelled_transfer_claim_id: Optional[UUID] = None
    if ship.pending_transfer_claimant_id is not None:
        cancelled_transfer_claim_id = ship.pending_transfer_claimant_id
        fee_to_refund = ship.pending_transfer_fee_paid or 0
        if fee_to_refund > 0:
            claimant = (
                db.query(Player)
                .filter(Player.id == ship.pending_transfer_claimant_id)
                .with_for_update()
                .first()
            )
            if claimant is not None:
                claimant.credits += fee_to_refund
        ship.pending_transfer_claimant_id = None
        ship.pending_transfer_requested_at = None
        ship.pending_transfer_deadline = None
        ship.pending_transfer_fee_paid = None
        ship.pending_transfer_port_id = None

    ship.stolen_status = True
    ship.stolen_reported_at = now
    ship.stolen_recovery_mode = mode
    ship.stolen_thief_id = thief_id
    ship.stolen_bounty_ref = bounty_ref
    ship.retract_grace_processed = False

    # Wanted-trigger (ranking.md "Wanted status"): "the current pilot (if
    # any) immediately enters Wanted Status" -- fired here, not deferred to
    # a sweep, since the pilot is already known and (when a bounty was
    # placed above) already row-locked. populate_existing() re-reads that
    # possibly-already-locked row rather than trusting a stale identity-map
    # copy; with_for_update() is a safe re-lock if place_bounty didn't run
    # (no_bounty mode, or thief_id is None/the owner themself).
    if thief_id is not None:
        thief_player = (
            db.query(Player)
            .filter(Player.id == thief_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if thief_player is not None:
            recompute_is_wanted(db, thief_player, now=now)

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
            "cancelled_transfer_claimant_id": str(cancelled_transfer_claim_id) if cancelled_transfer_claim_id else None,
        },
    )

    return {
        "ship_id": str(ship.id),
        "stolen_status": True,
        "stolen_reported_at": now.isoformat(),
        # WR10: server-authoritative deadline so the client never has to
        # duplicate STOLEN_RETRACT_GRACE (24h) to render a countdown ticker.
        "retract_grace_expires_at": (now + STOLEN_RETRACT_GRACE).isoformat(),
        "recovery_mode": mode,
        "bounty_id": bounty_ref,
        "cancelled_transfer_claim": cancelled_transfer_claim_id is not None,
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

    # Wanted-trigger reconciliation: the stolen-piloting condition just
    # cleared for this ship; recompute rather than blind-clear so the thief
    # correctly stays Wanted if the reputation trigger (or an unrelated
    # bust timer) still applies.
    if thief_id is not None:
        thief_player = (
            db.query(Player)
            .filter(Player.id == thief_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if thief_player is not None:
            recompute_is_wanted(db, thief_player, now=now)

    return {
        "ship_id": str(ship.id),
        "stolen_status": False,
        "refund": refund,
    }


# --- Abandon / claim (ship-registry.md "Abandonment") ---------------------
#
# Trade (peer-to-peer sale) is NOT built here -- ship-registry.md's own
# "Trading" section delegates the full protocol to ADR-0089, which already
# ships a complete mutual-presence-gated ship-bundle trade session
# (player_trade_service.PlayerTradeService._transfer_ship, wired at
# src/api/routes/player_trade.py) that logs the SAME
# RegistryEventType.OWNERSHIP_TRANSFER event this module uses. Re-verified
# 2026-08-04: this page's own status banner claiming trade has "zero route
# callers" is stale -- the ADR-0089 path is the real trade-transfer, live
# today.
#
# Contested registration transfer / salvage-claim ("Legal ownership
# transfer" -- 30% fee, 24h owner-dispute window) is deferred -- see this
# module's docstring.


def abandon_ship(db: Session, *, ship: Ship, owner: Player, port_id: UUID) -> dict:
    """Voluntarily relinquish ownership of ``ship`` at a port (ship-registry.md
    "Abandonment" steps 1-3). Ejects the owner to escape-pod state and marks
    the ship Abandoned; ownership is NOT transferred yet -- the first later
    claimant (see ``claim_abandoned_ship``) becomes the new registered owner.
    Raises ``ShipRegistryError`` with the exact canon ERR_* code on rejection.
    Flushes but does not commit -- the route owns the commit."""
    if ship.registered_owner_id != owner.id:
        raise ShipRegistryError(
            "ERR_NOT_REGISTERED_OWNER", "Only the registered owner can abandon this ship.",
        )
    if ship.is_abandoned:
        raise ShipRegistryError(
            "ERR_ALREADY_ABANDONED", "This ship has already been abandoned.",
        )
    if not owner.is_docked or owner.current_port_id != port_id:
        raise ShipRegistryError(
            "ERR_NOT_AT_PORT", "You must be docked at this port to abandon the ship here.",
        )
    # "not currently Borrowed by someone else" (ship-registry.md "Abandonment"
    # step 1). The owner themself piloting (or nobody piloting) is fine.
    if ship.current_pilot_id is not None and ship.current_pilot_id != owner.id:
        raise ShipRegistryError(
            "ERR_SHIP_BORROWED", "This ship is currently borrowed and cannot be abandoned.",
        )

    now = datetime.now(timezone.utc)
    ship.is_abandoned = True
    ship.abandoned_at = now
    ship.current_pilot_id = None
    # Owner is "ejected to escape pod" (step 2) -- clears their active-ship
    # pointer if this was the ship they were piloting; mirrors the eject
    # flow's Player.current_ship_id clear (ship-registry.md "Eject and
    # board"). Does not itself spawn an escape pod -- that's the existing
    # eject mechanic's own concern (escape_pod_service), out of scope here;
    # this only clears the pointer so the player isn't left referencing a
    # ship they no longer control.
    if owner.current_ship_id == ship.id:
        owner.current_ship_id = None
        from src.services.ship_service import sync_current_pilot
        sync_current_pilot(owner, None, old_ship=ship, db=db)

    append_registry_event(
        db,
        ship=ship,
        event_type=RegistryEventType.ABANDONED,
        previous_owner_id=owner.id,
        acting_party_id=owner.id,
        port_id=port_id,
    )

    return {
        "ship_id": str(ship.id),
        "is_abandoned": True,
        "abandoned_at": now.isoformat(),
    }


def claim_abandoned_ship(db: Session, *, ship: Ship, claimant: Player, port_id: UUID) -> dict:
    """The first player to dock at the port and claim an Abandoned ship
    becomes its new registered owner -- no transfer fee, no dispute window
    (ship-registry.md "Abandonment" step 4). Raises ``ShipRegistryError``
    with the exact canon ERR_* code on rejection. Flushes but does not
    commit -- the route owns the commit."""
    if not ship.is_abandoned:
        raise ShipRegistryError(
            "ERR_NOT_ABANDONED", "This ship is not available to claim.",
        )
    if not claimant.is_docked or claimant.current_port_id != port_id:
        raise ShipRegistryError(
            "ERR_NOT_AT_PORT", "You must be docked at this port to claim the ship here.",
        )

    previous_owner_id = ship.registered_owner_id
    now = datetime.now(timezone.utc)

    ship.owner_id = claimant.id
    ship.registered_owner_id = claimant.id
    ship.is_abandoned = False
    ship.abandoned_at = None
    # Insurance from the previous owner voids on any ownership change
    # (ship-registry.md "Insurance interaction"); mirrors player_trade_
    # service._transfer_ship's identical clear on its own ownership-change
    # path.
    if ship.insurance is not None:
        ship.insurance = None
        flag_modified(ship, "insurance")

    append_registry_event(
        db,
        ship=ship,
        event_type=RegistryEventType.OWNERSHIP_TRANSFER,
        original_owner_id=ship_registry_original_owner_id(db, ship),
        previous_owner_id=previous_owner_id,
        new_owner_id=claimant.id,
        acting_party_id=claimant.id,
        port_id=port_id,
        transfer_fee_paid=0,
        event_metadata={"via": "abandon_claim"},
    )

    return {
        "ship_id": str(ship.id),
        "registered_owner_id": str(claimant.id),
        "claimed_at": now.isoformat(),
    }


def archive_expired_abandoned_ships(
    db: Session, *, now: Optional[datetime] = None,
) -> int:
    """Auto-archive abandoned ships unclaimed for 7 real days (ship-registry.md
    "Abandonment" step 5 / SYSTEMS/ship-registry.md:316). Marks each hull
    DESTROYED with ``destruction_cause=abandonment_expired``, appends an
    ARCHIVED registry event, and best-effort spawns a CargoWreck for remaining
    cargo (CombatService._spawn_cargo_wreck — empty holds skip the wreck).
    Does **not** call ``ShipService.destroy_ship`` (that path transfers cargo
    to an escape pod; canon wants the hold contents in the wreck).
    Flushes but does not commit — the scheduler wrapper owns the commit.
    Returns the number of ships archived."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=ABANDONMENT_ARCHIVE_DAYS)
    ships = (
        db.query(Ship)
        .filter(
            Ship.is_abandoned.is_(True),
            Ship.abandoned_at.isnot(None),
            Ship.abandoned_at <= cutoff,
            Ship.is_destroyed.is_(False),
        )
        .all()
    )
    archived = 0
    for ship in ships:
        previous_owner_id = ship.registered_owner_id or ship.owner_id
        original_owner = None
        if previous_owner_id is not None:
            original_owner = db.query(Player).filter(Player.id == previous_owner_id).first()

        ship.is_destroyed = True
        ship.is_active = False
        ship.status = ShipStatus.DESTROYED
        ship.destruction_cause = "abandonment_expired"
        ship.is_abandoned = False
        ship.current_pilot_id = None

        try:
            from src.services.combat_service import CombatService
            CombatService(db)._spawn_cargo_wreck(
                ship, "abandonment_expired", original_owner, None,
            )
        except Exception as e:
            logger.error(
                "Abandonment-archive CargoWreck spawn failed for ship %s: %s",
                ship.id, e,
            )

        # Hold emptied into the wreck (or was already empty) — clear residual.
        if ship.cargo:
            ship.cargo = {"contents": {}}
            flag_modified(ship, "cargo")

        append_registry_event(
            db,
            ship=ship,
            event_type=RegistryEventType.ARCHIVED,
            original_owner_id=ship_registry_original_owner_id(db, ship),
            previous_owner_id=previous_owner_id,
            event_metadata={
                "via": "abandonment_expired",
                "abandoned_at": (
                    ship.abandoned_at.isoformat() if ship.abandoned_at else None
                ),
            },
        )
        archived += 1
    return archived


def ship_registry_original_owner_id(db: Session, ship: Ship) -> Optional[UUID]:
    """The ship's immutable ``original_owner_id`` (Invariant 2), read off its
    own INITIAL_REGISTRATION row rather than re-derived -- mirrors
    player_trade_service._transfer_ship's ``ship.registered_owner_id or
    ship.owner_id or prev_id`` fallback intent but sources it from the
    ledger itself, the one place that value is guaranteed immutable."""
    row = (
        db.query(ShipRegistry.original_owner_id)
        .filter(
            ShipRegistry.ship_id == ship.id,
            ShipRegistry.event_type == RegistryEventType.INITIAL_REGISTRATION,
        )
        .first()
    )
    return row[0] if row is not None else ship.registered_owner_id


# --- Contested registration transfer (ship-registry.md "Legal ownership
# transfer") ---------------------------------------------------------------


def file_transfer_claim(db: Session, *, ship: Ship, claimant: Player, port_id: UUID) -> dict:
    """File a contested registration-transfer claim on ``ship`` at
    ``port_id`` (ship-registry.md "Legal ownership transfer" steps 1-3).
    Charges the 30% fee immediately and opens the 24h owner-dispute window.
    Raises ``ShipRegistryError`` with the exact canon ERR_* code on
    rejection. Flushes but does not commit -- the route owns the commit."""
    if ship.registered_owner_id == claimant.id:
        raise ShipRegistryError(
            "ERR_ALREADY_OWNER", "You already hold the registration for this ship.",
        )
    if ship.pending_transfer_claimant_id is not None:
        raise ShipRegistryError(
            "ERR_TRANSFER_ALREADY_PENDING",
            "A registration-transfer claim is already pending on this ship "
            "(ship-registry.md: first request locks the ship until resolved).",
        )
    if not claimant.is_docked or claimant.current_port_id != port_id:
        raise ShipRegistryError(
            "ERR_NOT_AT_PORT", "You must be docked at this port to file a transfer claim here.",
        )
    if ship.stolen_status:
        raise ShipRegistryError(
            "ERR_SHIP_STOLEN", "A ship with an active stolen report cannot be claimed via transfer.",
        )

    now = datetime.now(timezone.utc)
    # Eligibility (step 1): Drifting (no current pilot) and not stolen (the
    # stolen check above already covers "not flagged stolen"), OR Borrowed
    # by the claimant for at least 1 hour -- "boarding doesn't grant transfer
    # rights immediately; you can't board and instantly claim."
    is_drifting = ship.current_pilot_id is None
    is_eligible_borrower = (
        ship.current_pilot_id == claimant.id
        and ship.current_pilot_since is not None
        and (now - ship.current_pilot_since) >= timedelta(hours=1)
    )
    if not (is_drifting or is_eligible_borrower):
        raise ShipRegistryError(
            "ERR_NOT_ELIGIBLE_FOR_TRANSFER",
            "This ship must be Drifting, or Borrowed by you for at least 1 hour, to file a transfer claim.",
        )

    fee = int(ship.purchase_value * TRANSFER_CLAIM_FEE_PCT)
    if claimant.credits < fee:
        raise ShipRegistryError(
            "ERR_INSUFFICIENT_CREDITS", "You do not have enough credits to pay the transfer fee.",
        )

    claimant.credits -= fee
    deadline = now + TRANSFER_CLAIM_DISPUTE_WINDOW
    ship.pending_transfer_claimant_id = claimant.id
    ship.pending_transfer_requested_at = now
    ship.pending_transfer_deadline = deadline
    ship.pending_transfer_fee_paid = fee
    ship.pending_transfer_port_id = port_id

    return {
        "ship_id": str(ship.id),
        "claimant_id": str(claimant.id),
        "fee_paid": fee,
        "requested_at": now.isoformat(),
        "dispute_deadline": deadline.isoformat(),
    }


def approve_transfer_claim(db: Session, *, ship: Ship, owner: Player) -> dict:
    """The registered owner explicitly approves a pending transfer claim,
    completing it immediately (ship-registry.md "Legal ownership transfer"
    step 4, "Owner explicitly approves -> transfer completes immediately").
    Raises ``ShipRegistryError`` with the exact canon ERR_* code on
    rejection. Flushes but does not commit -- the route owns the commit."""
    if ship.registered_owner_id != owner.id:
        raise ShipRegistryError(
            "ERR_NOT_REGISTERED_OWNER", "Only the registered owner can approve this transfer claim.",
        )
    if ship.pending_transfer_claimant_id is None:
        raise ShipRegistryError(
            "ERR_NO_PENDING_TRANSFER", "This ship has no pending transfer claim.",
        )
    return _complete_transfer_claim(db, ship=ship, via="owner_approved")


def _complete_transfer_claim(db: Session, *, ship: Ship, via: str) -> dict:
    """Shared completion path for both ``approve_transfer_claim`` (owner
    approves) and the auto-complete scheduler sweep (owner takes no action
    within 24h). Transfers registration to the pending claimant, keeps the
    already-charged fee (only a stolen-report cancellation refunds it -- see
    ``report_stolen``), and clears the pending-transfer columns. Caller must
    already hold the row lock on ``ship`` (and, for the sweep, have
    re-verified the claim is still pending). Flushes but does not commit."""
    claimant_id = ship.pending_transfer_claimant_id
    fee_paid = ship.pending_transfer_fee_paid
    port_id = ship.pending_transfer_port_id
    previous_owner_id = ship.registered_owner_id
    now = datetime.now(timezone.utc)

    ship.owner_id = claimant_id
    ship.registered_owner_id = claimant_id
    # Insurance from the previous owner voids on any ownership change
    # (ship-registry.md "Insurance interaction"); same clear as
    # claim_abandoned_ship / player_trade_service._transfer_ship.
    if ship.insurance is not None:
        ship.insurance = None
        flag_modified(ship, "insurance")

    ship.pending_transfer_claimant_id = None
    ship.pending_transfer_requested_at = None
    ship.pending_transfer_deadline = None
    ship.pending_transfer_fee_paid = None
    ship.pending_transfer_port_id = None

    append_registry_event(
        db,
        ship=ship,
        event_type=RegistryEventType.OWNERSHIP_TRANSFER,
        original_owner_id=ship_registry_original_owner_id(db, ship),
        previous_owner_id=previous_owner_id,
        new_owner_id=claimant_id,
        acting_party_id=claimant_id,
        port_id=port_id,
        transfer_fee_paid=fee_paid,
        event_metadata={"via": via},
    )

    return {
        "ship_id": str(ship.id),
        "registered_owner_id": str(claimant_id),
        "fee_paid": fee_paid,
        "completed_at": now.isoformat(),
        "via": via,
    }


# --- Eject / board (ship-registry.md "Eject and board" + "Hatch pin lock") -

# WO-BUILD-SHIP-EJECT-BOARD-REGISTRY-STATES interpretation note (no
# ship-level "docked at port" marker exists -- only Player.is_docked/
# current_port_id -- so canon's "both ships docked at the same port = 0
# turns" collapses to a single check on the ACTING player): 0 turns while
# docked at a station (the safe, planned-switch case); 1 turn in open space
# (friction against loadout-cycling mid-fight). Cross-sector eject+board is
# impossible by construction -- board() requires the target ship's sector to
# match the boarder's current sector, so there is no third tier to encode.
EJECT_BOARD_SPACE_TURN_COST = 1


def _eject_board_turn_cost(player: Player) -> int:
    """0 turns docked at a port, 1 turn in open space -- see the module-level
    note above this section for the interpretation this reads off."""
    return 0 if player.is_docked else EJECT_BOARD_SPACE_TURN_COST


def eject_ship(db: Session, *, player: Player) -> dict:
    """Voluntarily eject from the currently-piloted ship (ship-registry.md
    "Eject and board"): the old ship's ``current_pilot_id`` clears and it
    transitions to Drifting in its current sector; the player reseats onto
    their escape pod (the single-pod-per-player invariant already
    established by ``ShipService._ensure_escape_pod`` -- reuses an existing
    owned pod if the player has one, otherwise creates one, exactly matching
    canon's "briefly in escape-pod state"). Raises ``ShipRegistryError`` with
    a stable code on any rejection. Flushes but does not commit -- the route
    owns the commit and the player-row lock."""
    if player.current_ship_id is None:
        raise ShipRegistryError("ERR_NO_CURRENT_SHIP", "You are not currently piloting a ship.")

    old_ship = db.query(Ship).filter(Ship.id == player.current_ship_id).with_for_update().first()
    if old_ship is None:
        # Dangling pointer (the row it names no longer exists) -- clear it
        # rather than leave the invariant broken, but still reject: there is
        # genuinely nothing to eject from. Calling the shared pilot-pointer
        # sync helper below with new_ship=None is a documented total no-op
        # (nothing to clear on a ship side, no Wanted change) -- called
        # anyway to keep this file's write-site convention uniform (every
        # current_ship_id write pairs with a sync call, per test_registry_
        # pilot_wiring.py's structural sweep) rather than a special-cased
        # exception.
        from src.services.ship_service import sync_current_pilot
        player.current_ship_id = None
        sync_current_pilot(player, None, old_ship=None, db=db)
        raise ShipRegistryError("ERR_NO_CURRENT_SHIP", "Your current ship could not be found.")
    if old_ship.type == ShipType.ESCAPE_POD:
        raise ShipRegistryError(
            "ERR_ALREADY_IN_ESCAPE_POD",
            "You are already in an escape pod; there is nothing to eject from.",
        )

    regenerate_turns(db, player)
    turn_cost = _eject_board_turn_cost(player)
    if player.turns < turn_cost:
        raise ShipRegistryError(
            "ERR_INSUFFICIENT_TURNS",
            f"Not enough turns to eject. Need {turn_cost}, have {player.turns}.",
        )

    from src.services.ship_service import ShipService, sync_current_pilot
    escape_pod = ShipService(db)._ensure_escape_pod(player, old_ship.sector_id)
    player.current_ship_id = escape_pod.id
    sync_current_pilot(player, escape_pod, old_ship=old_ship, db=db)
    if turn_cost:
        spend_turns(player, turn_cost)

    db.flush()
    return {
        "ejected_ship_id": str(old_ship.id),
        "ejected_ship_now_drifting": True,
        "escape_pod_id": str(escape_pod.id),
        "turns_spent": turn_cost,
    }


def board_ship(db: Session, *, ship: Ship, boarder: Player, pin: Optional[str] = None) -> dict:
    """Board ``ship`` (ship-registry.md "Eject and board" + "Hatch pin
    lock"). Eligibility: the registered owner always qualifies (no pin); a
    matching ``pin`` qualifies anyone (regular Borrow). A pin-less attempt on
    a locked, non-owned ship is rejected 409 -- canon's own stated behavior
    for "no pin and ship still locked". The salvage-break bypass (a
    completed break clears ``hatch_pin_code``, opening the ship to anyone in
    the sector) stays dormant pending its own WO -- canon itself tags the
    break operation "📐 Design-only -- not in code today" -- so this
    function never needs to special-case it: a cleared pin already satisfies
    ``pin_matches`` the moment that mechanic ships (``pin=""`` would not
    match a NULL/empty ``hatch_pin_code`` here, so the future WO's own
    eligibility write is the actual unlock, not this function).

    Boarding a ``stolen_status`` ship immediately enters Wanted Status --
    already ``sync_current_pilot``'s existing behavior via
    ``recompute_is_wanted``, not reimplemented here. Raises
    ``ShipRegistryError`` with a stable code on any rejection. Flushes but
    does not commit -- the route owns the commit and the player-row lock."""
    if ship.is_destroyed:
        raise ShipRegistryError("ERR_SHIP_DESTROYED", "This ship has been destroyed.")
    if ship.status == ShipStatus.HARMONIZING:
        raise ShipRegistryError(
            "ERR_SHIP_HARMONIZING",
            "This ship is harmonizing into a warp gate focus and cannot be boarded.",
        )
    if ship.current_pilot_id == boarder.id:
        raise ShipRegistryError("ERR_ALREADY_PILOTING", "You are already piloting this ship.")
    if ship.sector_id != boarder.current_sector_id:
        raise ShipRegistryError(
            "ERR_DIFFERENT_SECTOR",
            f"This ship is in sector {ship.sector_id}; travel there to board it.",
        )

    is_owner = ship.registered_owner_id == boarder.id
    pin_matches = bool(pin) and bool(ship.hatch_pin_code) and pin == ship.hatch_pin_code
    if not (is_owner or pin_matches):
        raise ShipRegistryError(
            "ERR_SHIP_LOCKED",
            "This ship's hatch is locked. You need the pin (or a completed salvage break) to board it.",
        )

    regenerate_turns(db, boarder)
    turn_cost = _eject_board_turn_cost(boarder)
    if boarder.turns < turn_cost:
        raise ShipRegistryError(
            "ERR_INSUFFICIENT_TURNS",
            f"Not enough turns to board. Need {turn_cost}, have {boarder.turns}.",
        )

    old_ship = None
    if boarder.current_ship_id is not None and boarder.current_ship_id != ship.id:
        old_ship = (
            db.query(Ship).filter(Ship.id == boarder.current_ship_id).with_for_update().first()
        )

    from src.services.ship_service import sync_current_pilot
    boarder.current_ship_id = ship.id
    # sync_current_pilot's require_ship_access gate (detention) runs inside
    # this call and raises StationSecurityError -- the route catches that
    # type separately from ShipRegistryError, mirroring station_security.py.
    sync_current_pilot(boarder, ship, old_ship=old_ship, db=db)
    if turn_cost:
        spend_turns(boarder, turn_cost)

    db.flush()

    if is_owner:
        new_state = "owner_aboard"
    elif ship.stolen_status:
        new_state = "stolen_piloted"
    else:
        new_state = "borrowed"

    return {
        "ship_id": str(ship.id),
        "boarded": True,
        "state": new_state,
        "turns_spent": turn_cost,
        "now_wanted": bool(boarder.is_wanted),
    }
