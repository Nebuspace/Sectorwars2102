"""Region-invite redeem-at-signup primitives (WO-IL6, MAX-GATED auth surface).

Design brief: audit/design-briefs/invite-link-onramp.md §4.6-8 + the 2026-06-20
Review corrections + Max rulings (D5 = the 60-day account-age vote gate, IL5,
which is already BUILT + wired in regional_governance_service.py:56,1213).

WHY THIS MODULE EXISTS — the async/sync barrier
-----------------------------------------------
``auth.py`` (POST /register) and ``oauth.py`` (create_oauth_user) run on a
**synchronous** SQLAlchemy ``Session`` (Depends(get_db)). Every invite primitive
already shipped — ``RegionInviteService.validate_invite`` / ``consume_invite``
(region_invite_service.py) and ``grant_region_citizenship``
(regional_governance_service.py) — is **AsyncSession** AND **self-commits**.

The brief mandates that the ENTIRE redeem+create runs in **ONE transaction**
mirroring auth.py's single-commit-at-end (auth.py:525): a failure anywhere rolls
back account + membership + use-increment together. Calling the async
self-committing services from the sync route is impossible (wrong session type)
and would break atomicity (each self-commit releases the lock and opens a
partial-commit window). So this module **replicates that logic INLINE in sync
SQLAlchemy, WITHOUT any internal commit** — the caller (auth.py / oauth.py) owns
the single transaction boundary and the one final ``db.commit()``.

The semantics are kept faithful to the async originals (same validation order,
same error vocabulary, same promote-never-downgrade citizenship upsert, same
``SELECT ... FOR UPDATE`` row lock). The async services remain the source of
truth for the auth-free API + their unit tests; this is their sync, no-commit
mirror for the one place that needs it.

LOCK ORDER (brief §5 Threat 5): the invite row is locked FIRST, BEFORE the player
rows are created. The caller therefore invokes ``lock_and_validate_invite`` while
holding the open transaction but BEFORE inserting Player, then — after the Player
flush yields an id — invokes ``finalize_redemption`` to grant citizenship,
increment the use, and write the audit row, all in the same open transaction.

D10 FALLBACK (Review correction #4/#5): an invalid / expired / revoked / region-
gone / owner-changed / closed-region / no-sector invite NEVER 500s and NEVER
blocks signup. The caller gets a structured outcome and falls through to the
existing Terran-Space default placement plus a human-readable notice.

NO AUTONOMOUS DEPLOY: this is the AUTH carve-out. Built, py_compiled, unit-tested
— but the diff is routed for Max sign-off before any deploy.
"""

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.models.region import (
    Region,
    RegionalMembership,
    RegionStatus,
    MembershipType,
)
from src.models.region_invite import (
    RegionInvite,
    RegionInviteRedemption,
    RegionInviteStatus,
)
from src.models.sector import Sector

logger = logging.getLogger(__name__)

# Stable, public-facing notice keys for the D10 fall-through. The route surfaces
# one of these in the response so the client can show "your invite was X, you've
# been placed in Terran Space" without leaking which adverse condition tripped.
NOTICE_INVITE_INVALID = "invite_invalid_or_expired"
# Reason codes mirror region_invite_service.py / regional_governance_service.py so
# logs and tests can assert on the same vocabulary across the sync/async halves.


def _now_utc() -> datetime:
    """Timezone-aware current UTC instant (mirrors region_invite_service._now_utc)."""
    return datetime.now(timezone.utc)


def _as_aware_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Coerce a possibly-naive datetime to timezone-aware UTC (defensive)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def hash_ip(ip: Optional[str]) -> Optional[str]:
    """Hash a client IP for the redemption audit row — NEVER store the raw IP.

    Brief §5 / D8: feed ADR-0056 multi-account clustering with a hashed value.
    Returns None for a missing IP so the audit column stays NULL rather than
    storing a hash of the empty string.
    """
    if not ip:
        return None
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()


def lock_and_validate_invite(
    db: Session,
    code: Optional[str],
) -> Tuple[Optional[RegionInvite], Optional[Sector], str]:
    """Lock the invite row FIRST, re-validate everything under the lock, and
    resolve the region's capital sector — all inside the caller's open
    transaction, WITHOUT committing.

    This is the lock-order-correct front half of the redeem: it acquires
    ``SELECT ... FOR UPDATE`` on the invite row BEFORE any player row is created,
    so two simultaneous redeems of a ``max_uses=1`` code serialize and a region
    transfer cannot slip in between validate and consume (brief §5 Threat 4/5).

    Returns ``(invite, capital_sector, reason)``:
      * ``(invite, sector, "OK")``     — redeemable; caller overrides placement.
      * ``(None, None, reason)``       — adverse; caller falls through to D10
                                          (Terran-Space default + notice). reason
                                          is a stable ERR_* string.

    Adverse reasons (all fall-through, never raise / 500):
      ERR_INVITE_NOT_FOUND · ERR_INVITE_NOT_ACTIVE · ERR_INVITE_EXHAUSTED ·
      ERR_INVITE_EXPIRED · ERR_REGION_GONE · ERR_OWNER_CHANGED ·
      ERR_REGION_NOT_ACTIVE · ERR_REGION_NO_SECTORS.

    Does NOT mutate the invite (no use-increment here); ``finalize_redemption``
    performs the increment after the player row exists, still under this lock,
    in the same transaction.
    """
    if not code:
        return None, None, "ERR_INVITE_NOT_FOUND"

    # Acquire the write lock on the invite row up front (lock order: invite
    # BEFORE player rows). A bad/unknown code simply finds nothing.
    invite = (
        db.query(RegionInvite)
        .filter(RegionInvite.code == code)
        .with_for_update()
        .first()
    )
    if invite is None:
        return None, None, "ERR_INVITE_NOT_FOUND"

    # Re-validate EVERYTHING under the lock (same checks as the async
    # validate_invite + consume_invite, in the same order).
    if invite.status != RegionInviteStatus.ACTIVE.value:
        return None, None, "ERR_INVITE_NOT_ACTIVE"
    if invite.uses >= invite.max_uses:
        return None, None, "ERR_INVITE_EXHAUSTED"
    now = _now_utc()
    expires_at = _as_aware_utc(invite.expires_at)
    if expires_at is not None and now >= expires_at:
        return None, None, "ERR_INVITE_EXPIRED"

    # Region must still exist, be ACTIVE, the minting owner must still own it,
    # and it must have at least one sector to place the player in. Each of these
    # is a D10 fall-through, NOT a 500 (Review correction #4 — a region with no
    # sectors 500s today; we must fall back instead).
    region = db.query(Region).filter(Region.id == invite.region_id).first()
    if region is None:
        return None, None, "ERR_REGION_GONE"
    if invite.created_by is None or region.owner_id != invite.created_by:
        return None, None, "ERR_OWNER_CHANGED"
    # Closed / lapsing / suspended / terminated / pending region — fall back.
    # RegionStatus.ACTIVE.value == 'active'; the column may hold the enum's value
    # or (legacy default) the enum member — compare on the string value defensively.
    region_status = getattr(region.status, "value", region.status)
    if region_status != RegionStatus.ACTIVE.value:
        return None, None, "ERR_REGION_NOT_ACTIVE"

    capital_sector = (
        db.query(Sector)
        .filter(Sector.region_id == region.id)
        .order_by(Sector.sector_id.asc())
        .first()
    )
    if capital_sector is None:
        return None, None, "ERR_REGION_NO_SECTORS"

    return invite, capital_sector, "OK"


def finalize_redemption(
    db: Session,
    invite: RegionInvite,
    player_id: uuid.UUID,
    ip_hash: Optional[str] = None,
    device_fingerprint_hash: Optional[str] = None,
) -> str:
    """Grant citizenship, increment the invite use, and write the audit row —
    all inside the caller's open transaction, WITHOUT committing.

    Called AFTER the Player row has been flushed (so ``player_id`` is real) and
    while the invite row from ``lock_and_validate_invite`` is still locked in the
    SAME transaction. The caller's single ``db.commit()`` is what makes (account +
    membership + use-increment + audit row) atomic — a failure anywhere before
    that commit rolls EVERYTHING back together (brief §4.6-8 / §5 Threat 5).

    Steps (order matters for the lock + the FK):
      1. grant citizenship (UNIQUE(player_id, region_id) upsert; promote-never-
         downgrade so a concurrent turn-spend 'visitor' insert can never win —
         Review correction #5);
      2. increment ``invite.uses``; flip status -> 'exhausted' at the cap;
      3. write the ``region_invite_redemptions`` audit row (FK to the now-real
         player_id; hashed ip / device fingerprint or NULL).

    Returns the citizenship-grant code ("CITIZENSHIP_GRANTED" |
    "CITIZENSHIP_CONFIRMED") for the caller's log/response. Raises nothing of its
    own — any DB error propagates to the caller, which rolls back the whole txn.
    """
    grant_code = _grant_citizenship_inline(db, player_id, invite.region_id)

    # Claim one use under the still-held lock; flip to exhausted at the cap so a
    # later validate rejects on status (brief §5 Threat 5). The DB CHECK
    # (uses <= max_uses) is the last-line backstop.
    invite.uses = int(invite.uses or 0) + 1
    if invite.uses >= invite.max_uses:
        invite.status = RegionInviteStatus.EXHAUSTED.value

    # Append-only audit row (brief §3 / D8). redeemed_at is server_default=now().
    redemption = RegionInviteRedemption(
        invite_id=invite.id,
        redeemed_by_player_id=player_id,
        ip_hash=ip_hash,
        device_fingerprint_hash=device_fingerprint_hash,
    )
    db.add(redemption)

    return grant_code


def _grant_citizenship_inline(
    db: Session,
    player_id: uuid.UUID,
    region_id: uuid.UUID,
) -> str:
    """Sync, NO-COMMIT mirror of grant_region_citizenship
    (regional_governance_service.py:797).

    Inserts (or promotes) the player's RegionalMembership to
    membership_type='citizen' with voting_power floored at 1.0, honoring
    UNIQUE(player_id, region_id). Citizenship is NEVER downgraded (Review
    correction #5 — the upsert must WIN over a future turn-spend 'visitor'
    insert). Returns 'CITIZENSHIP_GRANTED' (inserted/promoted) or
    'CITIZENSHIP_CONFIRMED' (already a citizen).

    Idempotency note: because this runs inside the SAME open transaction as the
    brand-new player insert, the common case is "no membership row yet" -> a
    clean INSERT. The IntegrityError branch handles the rare concurrent enroll;
    it uses a SAVEPOINT (db.begin_nested) so a unique-violation does NOT poison
    the caller's outer transaction (mirrors the async helper's rollback-then-
    promote, but without abandoning the account-creation txn).
    """
    from decimal import Decimal

    membership = (
        db.query(RegionalMembership)
        .filter(
            RegionalMembership.player_id == player_id,
            RegionalMembership.region_id == region_id,
        )
        .first()
    )

    inserted = False
    if membership is None:
        new_membership = RegionalMembership(
            player_id=player_id,
            region_id=region_id,
            membership_type=MembershipType.CITIZEN.value,
            voting_power=Decimal("1.0"),
        )
        # SAVEPOINT so a UNIQUE collision (concurrent enroll) does not abort the
        # caller's outer account-creation transaction — we recover and promote.
        try:
            with db.begin_nested():
                db.add(new_membership)
                db.flush()
            membership = new_membership
            inserted = True
        except IntegrityError:
            # Lost the race — reload the surviving row and promote it.
            membership = (
                db.query(RegionalMembership)
                .filter(
                    RegionalMembership.player_id == player_id,
                    RegionalMembership.region_id == region_id,
                )
                .first()
            )
            if membership is None:
                # Should be unreachable (the violation implies a row exists), but
                # fail loud rather than silently skipping the grant.
                raise

    promoted = False
    if membership.membership_type != MembershipType.CITIZEN.value:
        membership.membership_type = MembershipType.CITIZEN.value
        promoted = True
    # A citizen MUST carry voting weight; a 0.0-power citizen row would silently
    # drop them from the eligible roll despite the citizen tier. Floor at 1.0,
    # never downgrade an already-higher power.
    if (membership.voting_power or Decimal("0")) <= 0:
        membership.voting_power = Decimal("1.0")
        promoted = True

    return "CITIZENSHIP_GRANTED" if (inserted or promoted) else "CITIZENSHIP_CONFIRMED"
