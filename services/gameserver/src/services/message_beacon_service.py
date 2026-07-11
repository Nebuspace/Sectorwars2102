"""MessageBeacon lifecycle -- WO-P4-play-beacon-kernel, canon:
FEATURES/gameplay/message-beacons.md. Deploy / read / salvage / expiry-
sweep + the per-sector FIFO cap and anti-grief gates, all in one module
(these lanes share this file + the model, per the WO's own instruction --
NOT concurrently fannable).

SYNC Session throughout -- matches contract_service.py / slipdrive_service.py
/ fuel_delivery_service.py's own convention over this codebase's
`api/routes/*.py`'s `db: Session = Depends(get_db)`. FLUSH-ONLY -- the route
(or the scheduler wrapper, for the sweep) owns the commit.

WS BROADCAST SPLIT (deliberate, matches combat_service.py / npc_scheduler's
own precedent):
  * deploy() / salvage() are called from a LIVE async route handler -- a
    running event loop always exists at call time, so they fire their own
    `beacon_deployed` / `beacon_salvaged` broadcasts directly, fire-and-
    forget (loop.create_task, swallow every failure), exactly like
    combat_service.py's `_emit_combat_ws_events`.
  * sweep_expired() runs inside a scheduler wrapper executed via
    `asyncio.to_thread` (a worker thread -- NO running loop reachable). It
    therefore does NOT try to broadcast itself; it returns the built
    `beacon_expired` event dicts, and the scheduler wrapper hands them to
    the shared `scheduler._common._broadcast_events` drain back on the
    event loop -- the exact "dual-transport event builder" split this
    codebase already uses for every other sweep-originated event.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError

from src.models.message_beacon import MessageBeacon
from src.models.multi_account import MultiAccountFlag, MultiAccountSeverity
from src.models.player import Player
from src.models.region import Region
from src.models.sector import Sector
from src.models.ship import Ship
from src.services import turn_service
from src.services.ai_security_service import get_security_service

logger = logging.getLogger(__name__)


class BeaconError(Exception):
    """400-class: player-facing validation failure. .args[0] is the
    human-readable detail string the route layer surfaces. Messages that
    carry a stable machine-readable reason are prefixed ``ERR_<CODE>: ``."""


class BeaconNotFoundError(BeaconError):
    """404-class."""


# ── Deploy costs (message-beacons.md:24) ──────────────────────────────────
DEPLOY_TURN_COST = 5
DEPLOY_CREDIT_COST = 500
DEPLOY_EQUIPMENT_QTY = 1

# ── Salvage economics (message-beacons.md:42, "50% of the deploy cost") ───
SALVAGE_TURN_COST = 1
SALVAGE_CREDIT_REFUND = 250  # equipment is NOT refunded -- destroyed with the casing

# ── Message constraints (message-beacons.md:24, :109) ─────────────────────
MESSAGE_MIN_LENGTH = 1
MESSAGE_MAX_LENGTH = 500

# ── Per-sector visibility cap (message-beacons.md:56, ADR-0056 N-V2) ──────
DEFAULT_SECTOR_CAP = 10
MAX_SECTOR_CAP = 50
# [NO-CANON] region-configurable hook: canon says "region operators may
# raise the cap up to 50" but specifies no storage location and this WO's
# scope does not include a region-admin route/migration to set it. Reuses
# the EXISTING, already-additive `Region.trade_bonuses` JSONB operator-
# tuning bag (no schema change) under this key -- a real, functioning hook
# for a future admin surface to write to, not a new invented column.
REGION_BEACON_CAP_KEY = "beacon_sector_cap"

# ── Anti-griefing (message-beacons.md:105-118) ─────────────────────────────
RATE_LIMIT_PER_DAY = 5
# "Neutral" tier floor (ranking.md:133 / personal_reputation_service.
# REPUTATION_TIERS -- Neutral is the single-point score 0, not a range).
# Canon (:114): "personal_rep >= neutral" -- deploy requires >= 0.
PERSONAL_REP_GATE_MIN = 0

# ── Expiry choices (message-beacons.md:27) ─────────────────────────────────
EXPIRY_CHOICES: Dict[str, Optional[timedelta]] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "never": None,
}


def _now() -> datetime:
    return datetime.now(UTC)


def _load_player(db: Session, player_id: uuid.UUID) -> Player:
    player = db.query(Player).filter(Player.id == player_id).first()
    if player is None:
        raise BeaconError(f"Player {player_id} not found")
    return player


def _load_beacon(db: Session, beacon_id: uuid.UUID) -> MessageBeacon:
    beacon = db.query(MessageBeacon).filter(MessageBeacon.id == beacon_id).first()
    if beacon is None:
        raise BeaconNotFoundError(f"Beacon {beacon_id} not found")
    return beacon


# ── Per-sector serialization (WO-P4 REVISE fix 1, mack/cipher) ────────────
# Two live races without this: (a) concurrent deploys each see only their
# own insert under READ COMMITTED -> both skip FIFO displacement -> the
# cap silently breaches; (b) concurrent {deploy,salvage,read_once,sweep}
# each rebuild Sector.message_beacons from their own snapshot ->
# last-writer-wins -> the JSONB denorm permanently diverges from the
# MessageBeacon rows that back it. A per-(region,sector) transaction-scoped
# advisory lock, acquired at the START of each mutating section (the read
# -> cap-decide -> denorm-rebuild block), serializes exactly the
# operations that touch the SAME sector while leaving every other sector
# free to proceed concurrently.
_SECTOR_LOCK_BASE = 0x42434E53  # 'BCNS' -- Beacon sector. Ascii-packed,
# distinct from scheduler._common's own lock-key family (that module
# guards sweep-WORKER double-fire, a different concern from this
# per-sector read-decide-write race) so the two can never collide or
# accidentally shadow one another.
_LOCK_KEY_MASK_63 = (1 << 63) - 1


def _sector_lock_key(region_id: uuid.UUID, sector_id: int) -> int:
    """Deterministic per-(region,sector) advisory-lock key -- the same
    content-hash scheme as scheduler._common.region_lock_key (blake2b, NOT
    Python's per-process-randomized hash()), so every gameserver instance
    derives the SAME key for the SAME sector."""
    digest = hashlib.blake2b(f"{region_id}:{sector_id}".encode("utf-8"), digest_size=8).digest()
    combined = int.from_bytes(digest, "big")
    return (_SECTOR_LOCK_BASE ^ combined) & _LOCK_KEY_MASK_63


def _lock_sector(db: Session, region_id: uuid.UUID, sector_id: int) -> None:
    """Blocking, transaction-scoped acquire (``pg_advisory_xact_lock`` --
    NOT the ``_try_`` variant): a concurrent operation against the SAME
    sector must wait its turn rather than fail outright, since this is a
    legitimate contention case (two players deploying in the same sector
    at once), not an error. Released automatically at commit/rollback --
    no matching unlock call needed."""
    db.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": _sector_lock_key(region_id, sector_id)},
    )


# ── Anti-account-multiplication hook (message-beacons.md:115, ADR-0056 E-V5) ─

def _participation_weight(db: Session, player_id: uuid.UUID) -> float:
    """Free-tier accounts in a flagged HARD-severity multi-account cluster
    weight 0x for beacon-cap/visibility purposes (canon:115); everyone else
    is 1.0.

    [SOFT-DEP] The real `participation_weight` computation is explicitly
    OUT OF SCOPE for the schema-owning WO that built `MultiAccountFlag`
    (models/multi_account.py's own docstring: "that computation itself is
    out of scope for this WO") -- this is a genuine seam consulting the
    live schema those flags land in, NOT a fake/stubbed detector. It reads
    real rows if any detection service ever writes them, and reads nothing
    (defaults 1.0) while none exists -- never blocks on the absent service,
    never invents a heuristic of its own.
    """
    flagged = (
        db.query(MultiAccountFlag)
        .filter(
            MultiAccountFlag.player_id == player_id,
            MultiAccountFlag.severity == MultiAccountSeverity.HARD,
        )
        .first()
    )
    return 0.0 if flagged is not None else 1.0


def _sector_cap(region: Region) -> int:
    """Region-configurable per-sector visibility cap (message-beacons.md:56).
    Reads the existing `Region.trade_bonuses` operator-tuning JSONB (no
    schema change) under REGION_BEACON_CAP_KEY; an absent/invalid value
    defaults to DEFAULT_SECTOR_CAP, clamped to [1, MAX_SECTOR_CAP] so a
    malformed config value can never disable the cap entirely."""
    bonuses = region.trade_bonuses if isinstance(region.trade_bonuses, dict) else {}
    raw = bonuses.get(REGION_BEACON_CAP_KEY)
    try:
        cap = int(raw) if raw is not None else DEFAULT_SECTOR_CAP
    except (TypeError, ValueError):
        cap = DEFAULT_SECTOR_CAP
    return max(1, min(MAX_SECTOR_CAP, cap))


def _beacon_summary(beacon: MessageBeacon) -> Dict[str, Any]:
    """The Sector.message_beacons JSONB denorm entry shape (message-
    beacons.md:91-100)."""
    return {
        "id": str(beacon.id),
        "deployer_nickname": beacon.deployer_nickname_at_deploy,
        "deployed_at": beacon.deployed_at.isoformat() if beacon.deployed_at else None,
        "preview": beacon.message[:60],
        "expiry": beacon.expiry.isoformat() if beacon.expiry else None,
    }


def _rebuild_sector_denorm(db: Session, region_id: uuid.UUID, sector_id: int) -> None:
    """Rebuild `Sector.message_beacons` from the live MessageBeacon rows for
    (region_id, sector_id) -- canon's own prescribed reconciliation strategy
    for JSONB/row drift (message-beacons.md:143, "Reconcile from rows;
    rebuild JSONB"), reused here as the update mechanism itself rather than
    only a periodic repair, so the denorm can never drift from the rows
    that back it. Multi-account HARD-flagged deployers' beacons are
    excluded from the visible list (canon:115, "aren't surfaced in the
    sector-view list") -- their rows still exist (salvageable, readable by
    direct id) but never appear in the ambient sector view.

    Looks up the Sector by (region_id, sector_id) -- the compound identity
    this whole subsystem keys on; a sector not found (should not happen for
    a live deploy/salvage/expiry against a real player location) is a
    no-op rather than a crash, matching this codebase's defensive-JSONB-
    denorm convention elsewhere."""
    sector = (
        db.query(Sector)
        .filter(Sector.region_id == region_id, Sector.sector_id == sector_id)
        .first()
    )
    if sector is None:
        return

    rows = (
        db.query(MessageBeacon)
        .filter(MessageBeacon.region_id == region_id, MessageBeacon.sector_id == sector_id)
        .order_by(MessageBeacon.deployed_at.asc())
        .all()
    )
    visible = [
        _beacon_summary(b) for b in rows
        if _participation_weight(db, b.deployer_player_id) > 0.0
    ]
    sector.message_beacons = visible
    flag_modified(sector, "message_beacons")


def _apply_sector_cap(db: Session, region_id: uuid.UUID, sector_id: int, cap: int) -> None:
    """FIFO-displace the oldest VISIBLE (weight > 0) beacons in (region_id,
    sector_id) until at most `cap` remain (message-beacons.md:56). Runs
    BEFORE the denorm rebuild so the rebuild reflects the post-displacement
    state in one pass. Weight-0 (multi-account-flagged) rows never count
    toward the cap (canon:115, "don't count toward the per-sector cap") and
    are never displaced by this pass -- only real, counted beacons compete
    for the cap slots."""
    rows = (
        db.query(MessageBeacon)
        .filter(MessageBeacon.region_id == region_id, MessageBeacon.sector_id == sector_id)
        .order_by(MessageBeacon.deployed_at.asc())
        .all()
    )
    counted = [b for b in rows if _participation_weight(db, b.deployer_player_id) > 0.0]
    overflow = len(counted) - cap
    if overflow <= 0:
        return
    for beacon in counted[:overflow]:
        # Belt + suspenders (WO-P4 REVISE fix 4): the caller already holds
        # this sector's advisory lock (fix 1), so this should be
        # unreachable in steady state -- but if it ever fires (e.g. a GM
        # force-delete outside the locked lineage), the row is already
        # gone either way, which is the outcome this loop wants. A failed
        # flush leaves the SESSION (not just this statement) unusable
        # until a rollback -- and `deploy()` still has an insert + player
        # debit pending in this SAME transaction that must survive, so
        # each delete runs inside its own SAVEPOINT (db.begin_nested()):
        # on failure only THIS delete unwinds, the outer transaction and
        # the rest of the overflow set are unaffected.
        try:
            with db.begin_nested():
                db.delete(beacon)
                db.flush()
        except (StaleDataError, ObjectDeletedError):
            logger.debug(
                "Sector cap displacement hit an already-removed beacon "
                "(region=%s sector=%s) -- benign race, continuing",
                region_id, sector_id,
            )


# ── WS broadcast (deploy/salvage only -- see module docstring) ────────────

def _dispatch_event_frame(frame: Dict[str, Any]) -> None:
    """Fire-and-forget sector broadcast for a LIVE (running-loop) caller.
    Takes an ALREADY-BUILT event dict (via build_beacon_event), so callers
    that need to delete the row can build the frame first and dispatch
    after -- no ORM instance is touched post-mutation. Mirrors combat_
    service.py's _emit_combat_ws_events / medal_service.py's _dispatch_
    medal_awarded_event: import inside the function, grab the running loop,
    schedule via loop.create_task so the send runs after the caller's
    transaction has committed and yielded, swallow every failure (no loop,
    no socket) so a WS hiccup can never break the beacon action or its
    commit."""
    try:
        import asyncio
        from src.services.websocket_service import connection_manager

        loop = asyncio.get_running_loop()
        loop.create_task(connection_manager.broadcast_to_sector(frame["sector_id"], dict(frame)))
    except Exception:
        logger.debug(
            "Skipped %s WS broadcast for beacon %s (no loop or socket)",
            frame.get("type"), frame.get("beacon_id"), exc_info=True,
        )


def build_beacon_event(event_type: str, beacon: MessageBeacon, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Pure event-dict builder -- the dual-transport split (reference_dual_
    transport_event_builder convention): the live emitter (_dispatch_
    beacon_event) calls this internally; sweep_expired() (no running loop)
    calls it directly to build the SAME payload shape for the scheduler's
    post-commit _broadcast_events drain. `sector_id` is the routing key
    _broadcast_events' generic fallback reads."""
    event = {
        "type": event_type,
        "sector_id": beacon.sector_id,
        "beacon_id": str(beacon.id),
        "region_id": str(beacon.region_id),
        "deployer_nickname": beacon.deployer_nickname_at_deploy,
        "timestamp": _now().isoformat(),
    }
    if extra:
        event.update(extra)
    return event


# ── Deploy ──────────────────────────────────────────────────────────────

def deploy(
    db: Session,
    player_id: uuid.UUID,
    sector_id: int,
    message: str,
    expiry: str = "never",
    read_once: bool = False,
) -> Dict[str, Any]:
    """Deploy a beacon at the player's current sector (message-beacons.md
    :22-35). FLUSH-ONLY -- the route commits.

    Validation order: location/docked state -> nexus-protected sector ->
    rate limit -> personal-rep gate -> message length -> content-policy
    filter -> resource affordability. Every check raises BEFORE any
    mutation -- a rejected deploy never partially debits."""
    if expiry not in EXPIRY_CHOICES:
        raise BeaconError(
            f"invalid_expiry: '{expiry}' -- must be one of {sorted(EXPIRY_CHOICES)}"
        )

    # Lock the player row up front -- every subsequent check reads live
    # state (turns/credits) off this same locked row, and the eventual
    # debit happens on it too (mirrors trading.py's dock/buy lock-then-
    # validate-then-mutate shape). WO-MONEY-REREAD-SERVICES: player was
    # already loaded unlocked by the route's get_current_player dependency
    # on this same session; populate_existing() forces this lock to re-read
    # live credits/turns rather than returning the stale identity-mapped
    # instance.
    player = (
        db.query(Player).filter(Player.id == player_id).populate_existing().with_for_update().first()
    )
    if player is None:
        raise BeaconError(f"Player {player_id} not found")

    turn_service.regenerate_turns(db, player)

    if player.current_sector_id != sector_id:
        raise BeaconError("You must be in the sector to deploy a beacon there")
    if player.is_docked:
        raise BeaconError("You cannot deploy a beacon while docked at a station")

    sector = db.query(Sector).filter(Sector.sector_id == sector_id).first()
    if sector is None:
        raise BeaconError(f"Sector {sector_id} not found")
    if sector.region_id is None:
        raise BeaconError("Sector has no region assigned -- cannot deploy a beacon here")
    if sector.is_nexus_protected:
        raise BeaconError(
            "ERR_NEXUS_PROTECTED_SECTOR: beacons cannot be deployed in a nexus-protected sector"
        )

    # Per-player rate limit: 5 deploys / UTC day (message-beacons.md:113).
    # _now() (not a direct datetime.now(UTC) call) -- WO-P4 REVISE fix 7:
    # this is the same module seam beacon.deployed_at/expiry already use
    # below, so a test can pin the whole clock deterministically via one
    # `monkeypatch.setattr(svc, "_now", ...)` rather than the rate-limit
    # window silently drifting from whatever the sibling checks use.
    day_start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = (
        db.query(func.count(MessageBeacon.id))
        .filter(
            MessageBeacon.deployer_player_id == player_id,
            MessageBeacon.deployed_at >= day_start,
        )
        .scalar()
    )
    if today_count >= RATE_LIMIT_PER_DAY:
        raise BeaconError(
            f"ERR_RATE_LIMIT_EXCEEDED: {RATE_LIMIT_PER_DAY} beacon deploys per UTC day reached"
        )

    # Personal-rep gate (message-beacons.md:114): Wanted / deeply-negative
    # accounts cannot deploy. Existing beacons by an account that later
    # goes negative are untouched (canon explicit) -- this is a DEPLOY-time
    # gate only.
    if (player.personal_reputation or 0) < PERSONAL_REP_GATE_MIN:
        raise BeaconError(
            "ERR_PERSONAL_REP_TOO_LOW: personal reputation must be Neutral or better to deploy a beacon"
        )

    if not (MESSAGE_MIN_LENGTH <= len(message or "") <= MESSAGE_MAX_LENGTH):
        raise BeaconError(
            f"invalid_message_length: must be {MESSAGE_MIN_LENGTH}-{MESSAGE_MAX_LENGTH} characters"
        )

    # Content-policy filter -- REUSE the already-shipped ARIA input-
    # validation pipeline verbatim (WO-ARIA-PROMPT-DEFENSE), never
    # reinvented. skip_sql_injection=True mirrors first_login.py's own
    # creative/free-form-text rationale (route-marking text plausibly
    # contains words like "select the northern route" / "delete this
    # waypoint" that would false-positive as SQL injection on ordinary
    # public bulletin text) -- XSS/prompt-injection/profanity checks all
    # run (canon :110/:112).
    security_service = get_security_service()
    is_safe, violations = security_service.validate_input(
        message, str(player_id), f"beacon-deploy:{player_id}",
        skip_sql_injection=True, skip_xss=False, seed_from=player,
    )
    if not is_safe:
        raise BeaconError(
            "ERR_CONTENT_POLICY_VIOLATION: message failed content-policy validation "
            f"({', '.join(v.violation_type.value for v in violations)})"
        )
    # WO-P4 FINAL-FIX (orchestrator ruling D15, option B -- store RAW,
    # encode-at-output): the earlier REVISE fix 6 stored an html.escape'd
    # value, which is the OWASP anti-pattern -- it silently inflates length
    # (a 500-char message full of `<>&"'` escapes into >500 chars,
    # overflowing this column's String(500) bound) and risks a future
    # double-escape if a consumer ALSO encodes at render time. The
    # standard-correct place to neutralize markup is at OUTPUT (React
    # auto-escapes on render; any other consumer is responsible for its own
    # output encoding), not at storage. sanitize_input's NFKC-normalize-
    # before-regex bypass (documented below) is a real gap, but it's
    # ai_security_service's own bug to fix, not beacon's to paper over by
    # corrupting the stored value's length contract.
    sanitized_message = security_service.sanitize_input(message)

    if player.credits < DEPLOY_CREDIT_COST:
        raise BeaconError(
            f"insufficient_credits: deploying costs {DEPLOY_CREDIT_COST}cr, "
            f"you have {player.credits}"
        )
    if player.turns < DEPLOY_TURN_COST:
        raise BeaconError(
            f"insufficient_turns: deploying costs {DEPLOY_TURN_COST} turns, "
            f"you have {player.turns}"
        )

    # WO-P4 REVISE fix 2 (mack, HIGH): lock the SHIP row before the cargo
    # RMW below -- player.current_ship (a lazy relationship read) applies
    # no row lock, so a concurrent cargo mutation (mining pickup,
    # contraband seizure, a second deploy) could last-writer-wins-clobber
    # this equipment debit. Mirrors mining_service._lock_player_and_ship /
    # contraband_service._lock_station_player_ship's own Ship-locking
    # convention -- Player is already locked above, so Player-then-Ship
    # stays the consistent lock order those services use too.
    if player.current_ship_id is None:
        raise BeaconError("No active ship to carry the beacon casing")
    ship = (
        db.query(Ship)
        .filter(Ship.id == player.current_ship_id, Ship.owner_id == player_id)
        .with_for_update()
        .first()
    )
    if ship is None:
        raise BeaconError("No active ship to carry the beacon casing")
    cargo = ship.cargo if isinstance(ship.cargo, dict) else {}
    contents = dict(cargo.get("contents") or {})
    held_equipment = int(contents.get("equipment", 0) or 0)
    if held_equipment < DEPLOY_EQUIPMENT_QTY:
        raise BeaconError(
            f"insufficient_cargo: deploying a beacon consumes {DEPLOY_EQUIPMENT_QTY} "
            f"equipment cargo, you have {held_equipment}"
        )

    # --- All validation passed -- mutate. ---
    turn_service.spend_turns(player, DEPLOY_TURN_COST)
    player.credits -= DEPLOY_CREDIT_COST

    contents["equipment"] = held_equipment - DEPLOY_EQUIPMENT_QTY
    cargo["contents"] = contents
    cargo["used"] = sum(int(q) for q in contents.values() if isinstance(q, (int, float)))
    ship.cargo = cargo
    flag_modified(ship, "cargo")

    expiry_delta = EXPIRY_CHOICES[expiry]
    now = _now()
    beacon = MessageBeacon(
        id=uuid.uuid4(),
        region_id=sector.region_id,
        sector_id=sector_id,
        deployer_player_id=player_id,
        deployer_nickname_at_deploy=player.nickname or player.username,
        message=sanitized_message,
        expiry=(now + expiry_delta) if expiry_delta is not None else None,
        read_once=read_once,
        deployed_at=now,
    )
    db.add(beacon)
    db.flush()

    region = db.query(Region).filter(Region.id == sector.region_id).first()
    cap = _sector_cap(region) if region is not None else DEFAULT_SECTOR_CAP

    # WO-P4 REVISE fix 1 (mack, CRITICAL): serialize the cap-check +
    # denorm-rebuild section per sector -- see _lock_sector's own
    # docstring for the exact race this closes. Player-then-Sector stays
    # the consistent lock order every path in this module uses (Player is
    # already locked above; salvage()/read()'s read_once branch acquire
    # this same sector lock AFTER their own Player lock too).
    _lock_sector(db, sector.region_id, sector_id)
    _apply_sector_cap(db, sector.region_id, sector_id, cap)
    _rebuild_sector_denorm(db, sector.region_id, sector_id)
    db.flush()

    _dispatch_event_frame(build_beacon_event("beacon_deployed", beacon))

    logger.info(
        "Player %s deployed beacon %s in sector %s", player_id, beacon.id, sector_id,
    )
    return {
        "id": str(beacon.id),
        "sector_id": beacon.sector_id,
        "region_id": str(beacon.region_id),
        "message": beacon.message,
        "expiry": beacon.expiry,
        "read_once": beacon.read_once,
        "deployed_at": beacon.deployed_at,
        "credits": player.credits,
        "turns": player.turns,
    }


# ── Read ────────────────────────────────────────────────────────────────

def read(db: Session, beacon_id: uuid.UUID, player_id: uuid.UUID) -> Dict[str, Any]:
    """Read a beacon's full message + author identity (message-beacons.md
    :41). Costs 0 turns. If `read_once`, the row is deleted on this call
    (canon:52) -- no bus event fires for this path (distinct from a
    salvage or an expiry-tick removal; message-beacons.md's Lifecycle
    section lists it as its own removal cause). FLUSH-ONLY.

    WO-P4 REVISE fix 3 (cipher, HIGH): requires the reader to actually be
    IN the beacon's sector -- id-only lookup let a leaked/guessed uuid
    trigger a remote read_once-delete or (via salvage()) a remote 250cr
    farm, breaking canon's region-isolation (message-beacons.md:120-122).
    A location mismatch raises the SAME BeaconNotFoundError as a beacon
    that doesn't exist at all (anti-oracle -- a caller can't distinguish
    "wrong sector" from "no such beacon")."""
    beacon = _load_beacon(db, beacon_id)

    player = db.query(Player).filter(Player.id == player_id).first()
    if player is None:
        raise BeaconError(f"Player {player_id} not found")
    if player.current_sector_id != beacon.sector_id:
        raise BeaconNotFoundError(f"Beacon {beacon_id} not found")

    result = {
        "id": str(beacon.id),
        "sector_id": beacon.sector_id,
        "region_id": str(beacon.region_id),
        "message": beacon.message,
        "deployer_nickname": beacon.deployer_nickname_at_deploy,
        "deployed_at": beacon.deployed_at,
        "expiry": beacon.expiry,
        "read_once": beacon.read_once,
    }

    if beacon.read_once:
        region_id, sector_id = beacon.region_id, beacon.sector_id
        # Fix 1: serialize this sector's delete + denorm-rebuild against
        # any concurrent deploy/salvage/sweep touching the same sector.
        _lock_sector(db, region_id, sector_id)
        try:
            db.delete(beacon)
            db.flush()
        except (StaleDataError, ObjectDeletedError):
            # Fix 4: a concurrent salvage/expiry already removed this exact
            # row between our SELECT and this delete -- from the reader's
            # perspective that's indistinguishable from "already gone".
            raise BeaconNotFoundError(f"Beacon {beacon_id} not found") from None
        _rebuild_sector_denorm(db, region_id, sector_id)
        db.flush()
        return result

    # Fix 5 (mack, MED): atomic SQL increment, not a Python read-modify-
    # write -- `beacon.read_count = beacon.read_count + 1` loses updates
    # under concurrent reads (last commit wins, not "+1 each"). The bulk
    # UPDATE's SET clause runs entirely inside Postgres; synchronize_session
    # =False leaves the in-memory `beacon` object stale on purpose (this
    # codebase's own convention, e.g. combat_service.py's drone-deactivate
    # bulk update), so db.refresh() re-reads the authoritative post-update
    # row for the response payload.
    #
    # WO-P4 FINAL-FIX change 2 (mack): a plain read takes NO row lock
    # (unlike read_once, which is protected by fix 1's sector lock) -- a
    # concurrent salvage/read_once/sweep can remove this exact row in the
    # window between _load_beacon's SELECT above and this UPDATE/refresh.
    # The UPDATE would then silently match 0 rows, and db.refresh() raises
    # ObjectDeletedError for a row that's no longer there -- same
    # uncaught-past-the-route-handler shape fix 4 already closed at the
    # other three delete sites.
    now = _now()
    try:
        db.query(MessageBeacon).filter(MessageBeacon.id == beacon.id).update(
            {
                MessageBeacon.read_count: MessageBeacon.read_count + 1,
                MessageBeacon.last_read_at: now,
            },
            synchronize_session=False,
        )
        db.flush()
        db.refresh(beacon)
    except (StaleDataError, ObjectDeletedError):
        raise BeaconNotFoundError(f"Beacon {beacon_id} not found") from None
    result["read_count"] = beacon.read_count
    return result


# ── Salvage ─────────────────────────────────────────────────────────────

def salvage(db: Session, beacon_id: uuid.UUID, player_id: uuid.UUID) -> Dict[str, Any]:
    """Salvage (remove) a beacon -- any player, deployer included (message-
    beacons.md:42, :51). Costs 1 turn, refunds 250cr; the equipment cargo
    is NOT refunded (destroyed with the casing). FLUSH-ONLY.

    WO-P4 REVISE fix 3 (cipher, HIGH): requires the salvager to actually
    be IN the beacon's sector -- id-only lookup let a leaked/guessed uuid
    trigger a remote 250cr/1-turn salvage-farm, breaking canon's
    region-isolation (message-beacons.md:120-122). See read()'s own
    docstring for the anti-oracle rationale (same error either way)."""
    beacon = _load_beacon(db, beacon_id)

    # WO-MONEY-REREAD-SERVICES: player was already loaded unlocked by the
    # route's get_current_player dependency on this same session;
    # populate_existing() forces this lock to re-read live credits/turns
    # rather than returning the stale identity-mapped instance.
    player = (
        db.query(Player).filter(Player.id == player_id).populate_existing().with_for_update().first()
    )
    if player is None:
        raise BeaconError(f"Player {player_id} not found")
    if player.current_sector_id != beacon.sector_id:
        raise BeaconNotFoundError(f"Beacon {beacon_id} not found")

    turn_service.regenerate_turns(db, player)
    if player.turns < SALVAGE_TURN_COST:
        raise BeaconError(
            f"insufficient_turns: salvaging costs {SALVAGE_TURN_COST} turn(s), "
            f"you have {player.turns}"
        )

    region_id, sector_id = beacon.region_id, beacon.sector_id
    beacon_id_str = str(beacon.id)

    # Build the broadcast frame BEFORE deleting -- once the row is gone,
    # SQLAlchemy expires the in-memory instance and its attributes are no
    # longer safely readable.
    frame = build_beacon_event("beacon_salvaged", beacon)

    turn_service.spend_turns(player, SALVAGE_TURN_COST)
    player.credits = (player.credits or 0) + SALVAGE_CREDIT_REFUND

    # Fix 1: serialize this sector's delete + denorm-rebuild against any
    # concurrent deploy/salvage/read_once/sweep touching the same sector.
    _lock_sector(db, region_id, sector_id)
    try:
        db.delete(beacon)
        db.flush()
    except (StaleDataError, ObjectDeletedError):
        # Fix 4: a concurrent salvage/read_once/sweep already removed this
        # exact row between our SELECT and this delete.
        raise BeaconNotFoundError(f"Beacon {beacon_id} not found") from None
    _rebuild_sector_denorm(db, region_id, sector_id)
    db.flush()

    _dispatch_event_frame(frame)

    logger.info(
        "Player %s salvaged beacon %s in sector %s (refund %d)",
        player_id, beacon_id_str, sector_id, SALVAGE_CREDIT_REFUND,
    )
    return {
        "id": beacon_id_str,
        "salvage_refund": SALVAGE_CREDIT_REFUND,
        "credits": player.credits,
        "turns": player.turns,
    }


# ── Expiry sweep ────────────────────────────────────────────────────────

def sweep_expired(db: Session, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Bulk-remove every beacon whose expiry timer has strictly passed
    (message-beacons.md:53). The deployer is NOT notified (canon explicit)
    -- the returned events are SECTOR broadcasts only (anyone currently
    present sees the beacon disappear from the list), never a personal
    push to the deployer.

    Returns pure event dicts (no WS call here -- see module docstring's
    "WS BROADCAST SPLIT"); the scheduler wrapper commits and hands
    `events` to `scheduler._common._broadcast_events` back on the event
    loop. FLUSH-ONLY.

    WO-P4 REVISE fixes 1 + 4 (mack): each candidate's sector is locked
    (fix 1) before its delete, serializing against a concurrent deploy/
    salvage/read_once on the same sector; the delete itself runs inside
    its own SAVEPOINT (fix 4, same "failed flush poisons the whole
    session" reasoning as _apply_sector_cap) so ONE candidate a
    concurrent salvage/read_once beat this sweep to doesn't abort the
    rest of the batch -- that candidate is just skipped, no event/count
    for it."""
    now = now or _now()

    expired = 0
    events: List[Dict[str, Any]] = []
    touched_sectors: set[Tuple[uuid.UUID, int]] = set()

    while True:
        candidate = (
            db.query(MessageBeacon)
            .filter(MessageBeacon.expiry.isnot(None), MessageBeacon.expiry < now)
            .first()
        )
        if candidate is None:
            break

        region_id, sector_id = candidate.region_id, candidate.sector_id
        _lock_sector(db, region_id, sector_id)
        # Build the frame before deleting -- once the row is gone the
        # in-memory instance is expired and its attributes are no longer
        # safely readable (matches salvage()'s own ordering).
        frame = build_beacon_event("beacon_expired", candidate)
        try:
            with db.begin_nested():
                db.delete(candidate)
                db.flush()
        except (StaleDataError, ObjectDeletedError):
            continue

        events.append(frame)
        touched_sectors.add((region_id, sector_id))
        expired += 1

    for region_id, sector_id in touched_sectors:
        _rebuild_sector_denorm(db, region_id, sector_id)
    db.flush()

    return {"expired": expired, "events": events}
