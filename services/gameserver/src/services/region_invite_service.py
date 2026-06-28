"""Region invite-link onramp service (WO-IL3).

Design brief: audit/design-briefs/invite-link-onramp.md §4.2 / §4.5 / §5 +
the 2026-06-20 Review corrections and Max rulings.

This is the **auth-free** half of the invite-link region-citizenship onramp.
It mints, lists, revokes, validates and consumes region invites. It deliberately
does NOT create accounts, touch ``auth.py`` / ``oauth.py``, or grant citizenship
— that signup-wiring half is WO-IL6 (Max-gated). The signup path will merely
*call* ``validate_invite`` + ``consume_invite`` from here, keeping all invite
logic on the safe side of the auth line.

Security model (brief §5):

  * **Mint** is owner-scoped: only the verified owner of ``region_id`` may mint
    an invite for it. Ownership is re-checked server-side every call — the
    client-supplied ``region_id`` is never trusted (brief §5 Threat 6). A NEW
    region_id-keyed helper is used (NOT the single-region ``verify_region_owner``
    in regional_governance.py, which assumes one region per owner).
  * **High-entropy codes** via ``secrets.token_urlsafe(16)`` (brief §5 Threat 3),
    not sequential/guessable. Collision-retry on the UNIQUE constraint.
  * **One-time by default** (``max_uses=1``) with a **mandatory TTL** (default
    7 days) — no infinitely-reusable link (brief §5, D2/D3).
  * **Per-owner caps** bound swarm size at the mint side (brief §5 Threat 1/2):
    ≤ MAX_ACTIVE_INVITES_PER_OWNER_PER_REGION active invites per owner per region,
    and ≤ MAX_REDEMPTIONS_PER_OWNER_PER_WINDOW redemptions per owner per rolling
    window. **These numbers are NO-CANON (invented in brief D4) — flagged for Max
    to ratify in sw2102-docs/DECISIONS.md.**
  * **Validate fails closed** on status≠active / uses≥max_uses / now≥expires_at /
    region gone / **owner no longer owns the region** (brief §5 Threat 3/4).
  * **Consume is race-safe**: ``SELECT ... FOR UPDATE`` on the invite row, the
    ``uses < max_uses`` check is re-evaluated UNDER the lock, then ``uses += 1``
    and status flips to ``exhausted`` at the cap (brief §5 Threat 5). Concurrent
    redeems of a ``max_uses=1`` code yield exactly one success. The DB
    ``uses <= max_uses`` CHECK constraint is the last-line backstop and an
    IntegrityError on commit is surfaced as exhausted.

Conventions mirror regional_governance_service.py: ``AsyncSession`` throughout,
``await db.execute(select(...).with_for_update())`` then ``scalar_one()`` for the
row lock (the SYNC ``port_ownership_service.py`` ``db.query(...)`` pattern is NOT
mirrored — wrong session type here), service methods returning ``{ok, code, ...}``
dicts for the owner-action surface, and timezone-aware UTC comparisons.
"""

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Union

from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.region import Region
from src.models.region_invite import (
    RegionInvite,
    RegionInviteRedemption,
    RegionInviteStatus,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Invented numbers (NO-CANON — brief §6 D2/D3/D4). Flagged for Max to ratify in
# sw2102-docs/DECISIONS.md. Kept as module constants so a single ruling change
# is one edit, and so the unit tests pin the documented kernel, not magic ints.
# ---------------------------------------------------------------------------

# D3 — mandatory TTL; default 7 days when the caller does not specify expires_at.
DEFAULT_INVITE_TTL = timedelta(days=7)

# D2 — one-time codes by default; multi-use links capped at an upper bound so an
# owner cannot mint a single infinitely-shareable link.
DEFAULT_MAX_USES = 1
MAX_MAX_USES = 10

# D4 — per-owner caps (NO-CANON). Bound swarm size at the mint side.
#   * active = status='active' AND not expired AND uses < max_uses.
MAX_ACTIVE_INVITES_PER_OWNER_PER_REGION = 10
#   * redemptions counted across a rolling window (per owner, all their regions).
MAX_REDEMPTIONS_PER_OWNER_PER_WINDOW = 20
REDEMPTION_WINDOW = timedelta(days=7)

# Code entropy: secrets.token_urlsafe(16) -> 22-char base64url, well under the
# String(32) column width and high-entropy (brief §5 Threat 3).
_CODE_NBYTES = 16
_CODE_MINT_RETRIES = 5


def _now_utc() -> datetime:
    """Timezone-aware current UTC instant.

    All invite TTL/window comparisons use this so a naive/aware mismatch with the
    TIMESTAMP(timezone=True) columns can never raise (scan risk: the model's
    ``is_redeemable`` uses ``datetime.utcnow()`` and is NOT the authoritative
    gate — this service is).
    """
    return datetime.now(timezone.utc)


def _as_aware_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Coerce a possibly-naive datetime to timezone-aware UTC.

    Defensive against a naive ``expires_at`` (e.g. a hand-built test row or a
    legacy value): treat naive as UTC rather than raising on the comparison.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class RegionInviteService:
    """Mint / list / revoke / validate / consume region invites (auth-free)."""

    # ---- ownership (NEW region_id-keyed helper) --------------------------

    @staticmethod
    async def owns_region(
        db: AsyncSession,
        owner_user_id: uuid.UUID,
        region_id: uuid.UUID,
    ) -> Optional[Region]:
        """Return the region IFF ``owner_user_id`` owns *this specific*
        ``region_id`` — else None.

        This is the NEW region_id-keyed ownership check the brief mandates. It
        does NOT reuse ``verify_region_owner`` (regional_governance.py), which is
        single-region (no region_id param; assumes one region per owner and would
        return the wrong region for a multi-region owner). The query requires
        BOTH ``id == region_id`` AND ``owner_id == owner_user_id`` in one shot;
        a NULL owner_id (Central Nexus / Terran Space) never matches a real UUID,
        so unowned hub regions are safely excluded.
        """
        result = await db.execute(
            select(Region).where(
                and_(Region.id == region_id, Region.owner_id == owner_user_id)
            )
        )
        return result.scalar_one_or_none()

    # ---- mint -----------------------------------------------------------

    @staticmethod
    async def mint_invite(
        db: AsyncSession,
        owner_user_id: uuid.UUID,
        region_id: uuid.UUID,
        max_uses: int = DEFAULT_MAX_USES,
        expires_at: Optional[datetime] = None,
    ) -> Dict:
        """Mint a new invite for ``region_id`` on behalf of its owner.

        Returns ``{ok, code, ...}``. The caller (route) maps ``code`` to HTTP.
        Validation order (cheap → expensive):
          1. ``owns_region`` server-side re-check       -> ERR_NOT_REGION_OWNER (403)
          2. ``max_uses`` in [1, MAX_MAX_USES]          -> ERR_INVALID_MAX_USES (400)
          3. ``expires_at`` defaulting + future check   -> ERR_INVALID_EXPIRY  (400)
          4. per-owner active-invite cap (this region)  -> ERR_ACTIVE_INVITE_CAP (409)
          5. per-owner rolling redemption cap           -> ERR_REDEMPTION_CAP   (409)
          6. mint with a collision-retried high-entropy code (201)
        """
        region = await RegionInviteService.owns_region(db, owner_user_id, region_id)
        if region is None:
            return {"ok": False, "code": "ERR_NOT_REGION_OWNER"}

        if not isinstance(max_uses, int) or max_uses < 1 or max_uses > MAX_MAX_USES:
            return {"ok": False, "code": "ERR_INVALID_MAX_USES"}

        now = _now_utc()
        if expires_at is None:
            expires_at = now + DEFAULT_INVITE_TTL
        else:
            expires_at = _as_aware_utc(expires_at)
            if expires_at <= now:
                return {"ok": False, "code": "ERR_INVALID_EXPIRY"}

        # Per-owner active-invite cap for THIS region (brief §5 Threat 1/2). An
        # "active" invite = status='active' AND not expired AND uses < max_uses.
        active_count = await RegionInviteService._count_active_invites(
            db, owner_user_id, region_id, now
        )
        if active_count >= MAX_ACTIVE_INVITES_PER_OWNER_PER_REGION:
            return {"ok": False, "code": "ERR_ACTIVE_INVITE_CAP"}

        # Per-owner redemptions-per-rolling-window cap (across all the owner's
        # invites/regions — a swarm bound, not a single-region bound).
        recent_redemptions = await RegionInviteService._count_recent_redemptions(
            db, owner_user_id, now
        )
        if recent_redemptions >= MAX_REDEMPTIONS_PER_OWNER_PER_WINDOW:
            return {"ok": False, "code": "ERR_REDEMPTION_CAP"}

        # Mint with a collision-retried high-entropy code. token_urlsafe is
        # cryptographically random, so a collision is astronomically unlikely;
        # the retry loop is belt-and-suspenders against the UNIQUE constraint.
        last_error: Optional[Exception] = None
        for _ in range(_CODE_MINT_RETRIES):
            code = secrets.token_urlsafe(_CODE_NBYTES)
            invite = RegionInvite(
                code=code,
                region_id=region_id,
                created_by=owner_user_id,
                max_uses=max_uses,
                uses=0,
                expires_at=expires_at,
                status=RegionInviteStatus.ACTIVE.value,
            )
            db.add(invite)
            try:
                await db.commit()
            except IntegrityError as exc:  # pragma: no cover - collision is rare
                last_error = exc
                await db.rollback()
                continue
            await db.refresh(invite)
            return {
                "ok": True,
                "code": "INVITE_CREATED",
                "invite": invite,
            }

        logger.error(
            "mint_invite: code-collision retries exhausted for owner=%s region=%s: %s",
            owner_user_id, region_id, last_error,
        )
        return {"ok": False, "code": "ERR_CODE_COLLISION"}

    # ---- list -----------------------------------------------------------

    @staticmethod
    async def list_invites(
        db: AsyncSession,
        owner_user_id: uuid.UUID,
        region_id: uuid.UUID,
    ) -> Dict:
        """List the invites this owner minted for ``region_id`` (newest first).

        Owner-scoped: returns ERR_NOT_REGION_OWNER if the caller does not own the
        region. Filters on ``created_by == owner`` AND ``region_id`` so a future
        co-owner / transferred region cannot leak another owner's codes.
        """
        region = await RegionInviteService.owns_region(db, owner_user_id, region_id)
        if region is None:
            return {"ok": False, "code": "ERR_NOT_REGION_OWNER"}

        result = await db.execute(
            select(RegionInvite)
            .where(
                and_(
                    RegionInvite.region_id == region_id,
                    RegionInvite.created_by == owner_user_id,
                )
            )
            .order_by(RegionInvite.created_at.desc())
        )
        invites = list(result.scalars().all())
        return {"ok": True, "code": "INVITES_LISTED", "invites": invites}

    # ---- revoke ---------------------------------------------------------

    @staticmethod
    async def revoke_invite(
        db: AsyncSession,
        owner_user_id: uuid.UUID,
        invite_id: uuid.UUID,
    ) -> Dict:
        """Revoke an invite the caller minted (owner-only).

        Sets status -> 'revoked' and stamps ``revoked_at``. Owner-scoped on
        ``created_by`` AND ``owns_region`` (defence-in-depth: even if a row's
        created_by were somehow stale, the caller must still own the region). An
        already-revoked invite is an idempotent success; an exhausted/expired one
        is reported as already-terminal (no-op success) rather than re-revoked.
        """
        invite = await db.scalar(
            select(RegionInvite).where(RegionInvite.id == invite_id)
        )
        if invite is None:
            return {"ok": False, "code": "ERR_INVITE_NOT_FOUND"}

        # Caller must be the minter AND still own the region the invite targets.
        if invite.created_by != owner_user_id:
            return {"ok": False, "code": "ERR_NOT_INVITE_OWNER"}
        region = await RegionInviteService.owns_region(
            db, owner_user_id, invite.region_id
        )
        if region is None:
            return {"ok": False, "code": "ERR_NOT_REGION_OWNER"}

        if invite.status == RegionInviteStatus.REVOKED.value:
            return {"ok": True, "code": "INVITE_ALREADY_REVOKED", "invite": invite}
        if invite.status in (
            RegionInviteStatus.EXHAUSTED.value,
            RegionInviteStatus.EXPIRED.value,
        ):
            # Terminal already; revoking adds nothing. Still stamp revoked status
            # so the owner's intent (kill it) is recorded and it can never redeem.
            invite.status = RegionInviteStatus.REVOKED.value
            invite.revoked_at = _now_utc()
            await db.commit()
            await db.refresh(invite)
            return {"ok": True, "code": "INVITE_REVOKED", "invite": invite}

        invite.status = RegionInviteStatus.REVOKED.value
        invite.revoked_at = _now_utc()
        await db.commit()
        await db.refresh(invite)
        return {"ok": True, "code": "INVITE_REVOKED", "invite": invite}

    # ---- validate (pure, auth-free) -------------------------------------

    @staticmethod
    async def validate_invite(
        db: AsyncSession,
        code: str,
    ) -> Tuple[Optional[RegionInvite], str]:
        """Resolve + fully validate a code WITHOUT mutating anything.

        Returns ``(invite, "OK")`` when redeemable, else ``(None, reason)`` where
        reason is a stable ERR_* string. Fails closed on every adverse condition
        (brief §5 Threat 3/4). The owner-still-owns-region re-check is performed
        here (read-only); ``consume_invite`` re-checks it again UNDER the lock so
        a region transfer cannot slip between validate and consume.

        Reasons: ERR_INVITE_NOT_FOUND · ERR_INVITE_NOT_ACTIVE (revoked/expired/
        exhausted status) · ERR_INVITE_EXHAUSTED (uses>=max_uses) ·
        ERR_INVITE_EXPIRED (now>=expires_at) · ERR_REGION_GONE ·
        ERR_OWNER_CHANGED.
        """
        if not code:
            return None, "ERR_INVITE_NOT_FOUND"

        invite = await db.scalar(
            select(RegionInvite).where(RegionInvite.code == code)
        )
        if invite is None:
            return None, "ERR_INVITE_NOT_FOUND"

        # status must be 'active' — a revoked/expired/exhausted-stamped row is
        # rejected on status alone (the explicit terminal states).
        if invite.status != RegionInviteStatus.ACTIVE.value:
            return None, "ERR_INVITE_NOT_ACTIVE"

        # exhausted by count (independent of the status stamp — a row could be at
        # the cap before the exhausted flip is observed under contention).
        if invite.uses >= invite.max_uses:
            return None, "ERR_INVITE_EXHAUSTED"

        # expiry (timezone-safe).
        now = _now_utc()
        expires_at = _as_aware_utc(invite.expires_at)
        if expires_at is not None and now >= expires_at:
            return None, "ERR_INVITE_EXPIRED"

        # region must still exist AND the minting owner must still own it
        # (brief §5 Threat 4 — invite fails closed when ownership changes).
        region = await db.scalar(
            select(Region).where(Region.id == invite.region_id)
        )
        if region is None:
            return None, "ERR_REGION_GONE"
        if invite.created_by is None or region.owner_id != invite.created_by:
            return None, "ERR_OWNER_CHANGED"

        return invite, "OK"

    # ---- consume (race-safe, auth-free) ---------------------------------

    @staticmethod
    async def consume_invite(
        db: AsyncSession,
        invite: RegionInvite,
    ) -> Dict:
        """Atomically claim one use of an invite under a row lock.

        Race-safe per brief §5 Threat 5: re-reads the invite row with
        ``SELECT ... FOR UPDATE``, re-evaluates ALL redeemability conditions
        UNDER the lock (status / uses / expiry / region-still-owned), increments
        ``uses``, and flips status to 'exhausted' when ``uses >= max_uses``.
        Concurrent redeems of a ``max_uses=1`` code yield exactly ONE
        ``{ok: True}`` — the loser sees ERR_INVITE_EXHAUSTED.

        Returns ``{ok, code, invite}``. Does NOT create the redemption-audit row
        or grant citizenship — those live in the Max-gated WO-IL6 signup path
        which calls this under the SAME transaction (lock the invite row first,
        then the player rows — brief lock-order note).

        NOTE: the caller owns the surrounding transaction boundary. This method
        commits its own ``uses`` increment (mirroring cast_policy_vote) so the
        lock is released promptly; WO-IL6 will instead thread this into its
        single account-creation transaction (it can call the internal increment
        under its own lock). For the auth-free unit-tested path, the self-commit
        is the contract.
        """
        # Re-acquire the row under a write lock. select_for_update serializes
        # concurrent consumers of the same invite.
        locked = await db.execute(
            select(RegionInvite)
            .where(RegionInvite.id == invite.id)
            .with_for_update()
        )
        invite = locked.scalar_one_or_none()
        if invite is None:
            return {"ok": False, "code": "ERR_INVITE_NOT_FOUND"}

        # Re-check EVERYTHING under the lock — a concurrent consume/revoke/expiry
        # may have changed state between validate and this lock acquisition.
        if invite.status != RegionInviteStatus.ACTIVE.value:
            await db.rollback()
            return {"ok": False, "code": "ERR_INVITE_NOT_ACTIVE"}
        if invite.uses >= invite.max_uses:
            await db.rollback()
            return {"ok": False, "code": "ERR_INVITE_EXHAUSTED"}
        now = _now_utc()
        expires_at = _as_aware_utc(invite.expires_at)
        if expires_at is not None and now >= expires_at:
            await db.rollback()
            return {"ok": False, "code": "ERR_INVITE_EXPIRED"}

        # Owner-still-owns re-check UNDER the lock (brief §5 Threat 4): a region
        # transfer concurrent with the redeem must fail closed.
        region = await db.scalar(
            select(Region).where(Region.id == invite.region_id)
        )
        if region is None:
            await db.rollback()
            return {"ok": False, "code": "ERR_REGION_GONE"}
        if invite.created_by is None or region.owner_id != invite.created_by:
            await db.rollback()
            return {"ok": False, "code": "ERR_OWNER_CHANGED"}

        # Claim one use. Flip to exhausted at the cap so a later validate rejects
        # on status (and so the per-owner active-count excludes it).
        invite.uses = int(invite.uses or 0) + 1
        if invite.uses >= invite.max_uses:
            invite.status = RegionInviteStatus.EXHAUSTED.value

        try:
            await db.commit()
        except IntegrityError:
            # The DB ``uses <= max_uses`` CHECK is the last-line backstop against
            # a concurrent over-increment that escaped the lock (defence-in-depth).
            await db.rollback()
            return {"ok": False, "code": "ERR_INVITE_EXHAUSTED"}
        await db.refresh(invite)
        return {"ok": True, "code": "INVITE_CONSUMED", "invite": invite}

    # ---- internal counters ----------------------------------------------

    @staticmethod
    async def _count_active_invites(
        db: AsyncSession,
        owner_user_id: uuid.UUID,
        region_id: uuid.UUID,
        now: datetime,
    ) -> int:
        """Count this owner's *active* invites for a region.

        Active = status='active' AND not expired AND uses < max_uses. Drives the
        per-owner active-invite cap at mint (brief §5 Threat 1/2).
        """
        count = await db.scalar(
            select(func.count(RegionInvite.id)).where(
                and_(
                    RegionInvite.region_id == region_id,
                    RegionInvite.created_by == owner_user_id,
                    RegionInvite.status == RegionInviteStatus.ACTIVE.value,
                    RegionInvite.expires_at > now,
                    RegionInvite.uses < RegionInvite.max_uses,
                )
            )
        )
        return int(count or 0)

    @staticmethod
    async def _count_recent_redemptions(
        db: AsyncSession,
        owner_user_id: uuid.UUID,
        now: datetime,
    ) -> int:
        """Count redemptions of THIS owner's invites within the rolling window.

        Joins the redemption audit rows to the invites this owner minted and
        counts those redeemed within ``REDEMPTION_WINDOW`` of ``now``. Drives the
        per-owner redemptions-per-window cap (brief §5 Threat 1, D4).
        """
        window_start = now - REDEMPTION_WINDOW
        count = await db.scalar(
            select(func.count(RegionInviteRedemption.id))
            .join(
                RegionInvite,
                RegionInviteRedemption.invite_id == RegionInvite.id,
            )
            .where(
                and_(
                    RegionInvite.created_by == owner_user_id,
                    RegionInviteRedemption.redeemed_at >= window_start,
                )
            )
        )
        return int(count or 0)
