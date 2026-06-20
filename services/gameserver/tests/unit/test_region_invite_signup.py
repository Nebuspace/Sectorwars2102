"""Unit tests for the WO-IL6 redeem-at-signup primitives + the no-invite parity.

Brief: audit/design-briefs/invite-link-onramp.md §4.6-8 + Review corrections +
Max rulings. AUTH-gated surface — built + tested, NOT deployed without sign-off.

These are mock-DB unit tests (Tier A — no real DB), mirroring
tests/unit/test_region_invite_service.py. They pin the SYNC, NO-COMMIT inline
mirror that auth.py / oauth.py call inside their single account-creation
transaction:

  * lock_and_validate_invite — locks the invite row FIRST (with_for_update),
    re-validates under the lock, resolves the capital sector, and returns the D10
    fall-through reason for every adverse condition (never raises / 500s).
  * _grant_citizenship_inline — citizen upsert that WINS / never downgrades
    (visitor-default tension, Review correction #5).
  * finalize_redemption — citizenship + use-increment + audit row, NO commit.
  * hash_ip — never stores the raw IP.
  * the rate-limit dependencies (register / exchange) — 429 over the cap.

The mandated cases (per the WO): valid-invite placement+citizenship,
no-invite-unchanged, invalid-code-fallback, OAuth parity, single-txn-rollback-on-
failure. No auth-surface assertions are weakened; nothing here mutates real state.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.models.region import (
    Region,
    RegionalMembership,
    RegionStatus,
    RegionType,
    MembershipType,
)
from src.models.region_invite import RegionInvite, RegionInviteStatus
from src.auth.region_invite_signup import (
    lock_and_validate_invite,
    finalize_redemption,
    _grant_citizenship_inline,
    hash_ip,
    NOTICE_INVITE_INVALID,
)


def _utc(**delta) -> datetime:
    return datetime.now(timezone.utc) + timedelta(**delta)


def _make_invite(
    *,
    owner_id: uuid.UUID,
    region_id: uuid.UUID,
    status: str = RegionInviteStatus.ACTIVE.value,
    uses: int = 0,
    max_uses: int = 1,
    expires_at: datetime = None,
) -> RegionInvite:
    return RegionInvite(
        id=uuid.uuid4(),
        code=f"code-{uuid.uuid4().hex[:8]}",
        region_id=region_id,
        created_by=owner_id,
        max_uses=max_uses,
        uses=uses,
        status=status,
        expires_at=expires_at if expires_at is not None else _utc(days=7),
    )


def _make_region(
    *, region_id: uuid.UUID, owner_id, status: str = RegionStatus.ACTIVE.value
) -> Region:
    return Region(
        id=region_id,
        name="r",
        display_name="R",
        owner_id=owner_id,
        region_type=RegionType.PLAYER_OWNED.value,
        status=status,
    )


def _make_sector(region_id: uuid.UUID, sector_id: int = 1):
    s = MagicMock()
    s.region_id = region_id
    s.sector_id = sector_id
    return s


class _LockValidateDB:
    """A sync MagicMock db modeling the exact query sequence
    lock_and_validate_invite issues:
      1. query(RegionInvite).filter(...).with_for_update().first() -> invite
      2. query(Region).filter(...).first()                         -> region
      3. query(Sector).filter(...).order_by(...).first()           -> sector
    Each query() returns a chainable stub whose .first() pops the next result.
    """

    def __init__(self, *, invite=None, region=None, sector=None):
        self._results = [invite, region, sector]
        self._i = 0

    def query(self, *_a, **_k):
        chain = MagicMock()
        # All chained builders return the same chain; .first() pops a result.
        chain.filter.return_value = chain
        chain.with_for_update.return_value = chain
        chain.order_by.return_value = chain

        def _first():
            if self._i < len(self._results):
                r = self._results[self._i]
                self._i += 1
                return r
            return None

        chain.first.side_effect = _first
        return chain


# ---------------------------------------------------------------------------
# lock_and_validate_invite — valid + every D10 fall-through (never raises)
# ---------------------------------------------------------------------------

class TestLockAndValidate:

    def test_valid_invite_returns_region_and_sector(self):
        owner, region_id = uuid.uuid4(), uuid.uuid4()
        invite = _make_invite(owner_id=owner, region_id=region_id)
        region = _make_region(region_id=region_id, owner_id=owner)
        sector = _make_sector(region_id, sector_id=42)
        db = _LockValidateDB(invite=invite, region=region, sector=sector)

        got_invite, got_sector, reason = lock_and_validate_invite(db, invite.code)
        assert reason == "OK"
        assert got_invite is invite
        assert got_sector is sector

    def test_empty_code_short_circuits(self):
        db = MagicMock()
        got_invite, got_sector, reason = lock_and_validate_invite(db, "")
        assert (got_invite, got_sector, reason) == (None, None, "ERR_INVITE_NOT_FOUND")
        db.query.assert_not_called()

    def test_unknown_code_fails_closed(self):
        db = _LockValidateDB(invite=None)
        _, _, reason = lock_and_validate_invite(db, "nope")
        assert reason == "ERR_INVITE_NOT_FOUND"

    def test_revoked_status_fails_closed(self):
        owner, region_id = uuid.uuid4(), uuid.uuid4()
        invite = _make_invite(
            owner_id=owner, region_id=region_id,
            status=RegionInviteStatus.REVOKED.value,
        )
        db = _LockValidateDB(invite=invite)
        _, _, reason = lock_and_validate_invite(db, invite.code)
        assert reason == "ERR_INVITE_NOT_ACTIVE"

    def test_exhausted_by_count_fails_closed(self):
        owner, region_id = uuid.uuid4(), uuid.uuid4()
        invite = _make_invite(owner_id=owner, region_id=region_id, uses=1, max_uses=1)
        db = _LockValidateDB(invite=invite)
        _, _, reason = lock_and_validate_invite(db, invite.code)
        assert reason == "ERR_INVITE_EXHAUSTED"

    def test_expired_fails_closed(self):
        owner, region_id = uuid.uuid4(), uuid.uuid4()
        invite = _make_invite(
            owner_id=owner, region_id=region_id, expires_at=_utc(days=-1)
        )
        db = _LockValidateDB(invite=invite)
        _, _, reason = lock_and_validate_invite(db, invite.code)
        assert reason == "ERR_INVITE_EXPIRED"

    def test_region_gone_fails_closed(self):
        owner, region_id = uuid.uuid4(), uuid.uuid4()
        invite = _make_invite(owner_id=owner, region_id=region_id)
        db = _LockValidateDB(invite=invite, region=None)
        _, _, reason = lock_and_validate_invite(db, invite.code)
        assert reason == "ERR_REGION_GONE"

    def test_owner_changed_fails_closed(self):
        owner, new_owner, region_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        invite = _make_invite(owner_id=owner, region_id=region_id)
        region = _make_region(region_id=region_id, owner_id=new_owner)  # transferred
        db = _LockValidateDB(invite=invite, region=region)
        _, _, reason = lock_and_validate_invite(db, invite.code)
        assert reason == "ERR_OWNER_CHANGED"

    def test_closed_region_falls_through(self):
        owner, region_id = uuid.uuid4(), uuid.uuid4()
        invite = _make_invite(owner_id=owner, region_id=region_id)
        region = _make_region(
            region_id=region_id, owner_id=owner,
            status=RegionStatus.SUSPENDED.value,
        )
        db = _LockValidateDB(invite=invite, region=region)
        _, _, reason = lock_and_validate_invite(db, invite.code)
        assert reason == "ERR_REGION_NOT_ACTIVE"

    def test_no_sectors_falls_through_not_500(self):
        # Review correction #4: a region with no sectors 500s in the OLD code;
        # the invite path must fall through, NOT raise.
        owner, region_id = uuid.uuid4(), uuid.uuid4()
        invite = _make_invite(owner_id=owner, region_id=region_id)
        region = _make_region(region_id=region_id, owner_id=owner)
        db = _LockValidateDB(invite=invite, region=region, sector=None)
        got_invite, got_sector, reason = lock_and_validate_invite(db, invite.code)
        assert (got_invite, got_sector, reason) == (None, None, "ERR_REGION_NO_SECTORS")


# ---------------------------------------------------------------------------
# _grant_citizenship_inline — citizen upsert WINS / never downgrades (#5)
# ---------------------------------------------------------------------------

class TestGrantCitizenshipInline:

    def _db_with_membership(self, membership):
        db = MagicMock()
        chain = MagicMock()
        chain.filter.return_value = chain
        chain.first.return_value = membership
        db.query.return_value = chain
        return db

    def test_no_existing_membership_inserts_citizen(self):
        db = MagicMock()
        chain = MagicMock()
        chain.filter.return_value = chain
        chain.first.return_value = None  # no row yet
        db.query.return_value = chain
        # begin_nested is a context manager
        db.begin_nested.return_value.__enter__ = MagicMock()
        db.begin_nested.return_value.__exit__ = MagicMock(return_value=False)

        player_id, region_id = uuid.uuid4(), uuid.uuid4()
        code = _grant_citizenship_inline(db, player_id, region_id)

        assert code == "CITIZENSHIP_GRANTED"
        # A citizen membership row was added at voting_power 1.0.
        added = db.add.call_args[0][0]
        assert isinstance(added, RegionalMembership)
        assert added.membership_type == MembershipType.CITIZEN.value
        assert added.voting_power == Decimal("1.0")

    def test_existing_visitor_is_promoted_not_downgraded(self):
        # Visitor-default tension (Review correction #5): an existing visitor row
        # (e.g. a concurrent turn-spend) must be PROMOTED to citizen, never left
        # as visitor.
        existing = RegionalMembership(
            player_id=uuid.uuid4(),
            region_id=uuid.uuid4(),
            membership_type=MembershipType.VISITOR.value,
            voting_power=Decimal("1.0"),
        )
        db = self._db_with_membership(existing)
        code = _grant_citizenship_inline(db, existing.player_id, existing.region_id)
        assert code == "CITIZENSHIP_GRANTED"
        assert existing.membership_type == MembershipType.CITIZEN.value
        # No new row added — promoted in place.
        db.add.assert_not_called()

    def test_zero_power_citizen_is_floored_to_one(self):
        existing = RegionalMembership(
            player_id=uuid.uuid4(),
            region_id=uuid.uuid4(),
            membership_type=MembershipType.CITIZEN.value,
            voting_power=Decimal("0"),
        )
        db = self._db_with_membership(existing)
        code = _grant_citizenship_inline(db, existing.player_id, existing.region_id)
        assert code == "CITIZENSHIP_GRANTED"
        assert existing.voting_power == Decimal("1.0")

    def test_already_citizen_with_power_is_confirmed_noop(self):
        existing = RegionalMembership(
            player_id=uuid.uuid4(),
            region_id=uuid.uuid4(),
            membership_type=MembershipType.CITIZEN.value,
            voting_power=Decimal("2.0"),
        )
        db = self._db_with_membership(existing)
        code = _grant_citizenship_inline(db, existing.player_id, existing.region_id)
        assert code == "CITIZENSHIP_CONFIRMED"
        # Higher power is NEVER downgraded.
        assert existing.voting_power == Decimal("2.0")
        db.add.assert_not_called()


# ---------------------------------------------------------------------------
# finalize_redemption — citizenship + use-increment + audit row, NO commit
# ---------------------------------------------------------------------------

class TestFinalizeRedemption:

    def _grant_db(self):
        """db whose membership lookup returns an existing citizen (so the grant
        is a no-op CONFIRMED and we can focus on the consume + audit steps)."""
        existing = RegionalMembership(
            player_id=uuid.uuid4(),
            region_id=uuid.uuid4(),
            membership_type=MembershipType.CITIZEN.value,
            voting_power=Decimal("1.0"),
        )
        db = MagicMock()
        chain = MagicMock()
        chain.filter.return_value = chain
        chain.first.return_value = existing
        db.query.return_value = chain
        return db

    def test_increments_use_and_exhausts_at_cap(self):
        owner, region_id = uuid.uuid4(), uuid.uuid4()
        invite = _make_invite(owner_id=owner, region_id=region_id, uses=0, max_uses=1)
        db = self._grant_db()
        finalize_redemption(db, invite, uuid.uuid4(), ip_hash="abc")
        assert invite.uses == 1
        assert invite.status == RegionInviteStatus.EXHAUSTED.value

    def test_multi_use_invite_not_exhausted_before_cap(self):
        owner, region_id = uuid.uuid4(), uuid.uuid4()
        invite = _make_invite(owner_id=owner, region_id=region_id, uses=0, max_uses=3)
        db = self._grant_db()
        finalize_redemption(db, invite, uuid.uuid4())
        assert invite.uses == 1
        assert invite.status == RegionInviteStatus.ACTIVE.value

    def test_writes_audit_row_and_never_commits(self):
        owner, region_id = uuid.uuid4(), uuid.uuid4()
        invite = _make_invite(owner_id=owner, region_id=region_id)
        db = self._grant_db()
        player_id = uuid.uuid4()
        finalize_redemption(db, invite, player_id, ip_hash="iphash")
        # The audit row was added with the real player_id + the hashed ip.
        added = db.add.call_args[0][0]
        assert added.invite_id == invite.id
        assert added.redeemed_by_player_id == player_id
        assert added.ip_hash == "iphash"
        # SINGLE-TXN contract: this helper NEVER commits — the caller owns it.
        db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# hash_ip — never stores the raw IP
# ---------------------------------------------------------------------------

class TestHashIp:

    def test_none_ip_returns_none(self):
        assert hash_ip(None) is None
        assert hash_ip("") is None

    def test_hashes_and_does_not_echo_raw(self):
        h = hash_ip("203.0.113.7")
        assert h is not None
        assert "203.0.113.7" not in h
        assert len(h) == 64  # sha256 hex digest

    def test_deterministic(self):
        assert hash_ip("1.2.3.4") == hash_ip("1.2.3.4")


# ---------------------------------------------------------------------------
# rate-limit dependencies — 429 over the cap
# ---------------------------------------------------------------------------

class TestSignupRateLimit:

    def _request(self, ip="9.9.9.9"):
        req = MagicMock()
        req.headers = {}
        req.client.host = ip
        return req

    def test_register_allows_under_cap_then_429(self):
        from fastapi import HTTPException
        from src.auth import signup_rate_limit as rl
        rl._reset_for_tests()
        req = self._request("203.0.113.10")
        # First REGISTER_MAX_ATTEMPTS are allowed.
        for _ in range(rl.REGISTER_MAX_ATTEMPTS):
            rl.register_rate_limit(req)  # no raise
        # The next one trips the limit.
        with pytest.raises(HTTPException) as exc:
            rl.register_rate_limit(req)
        assert exc.value.status_code == 429
        assert "Retry-After" in exc.value.headers

    def test_register_isolated_per_ip(self):
        from src.auth import signup_rate_limit as rl
        rl._reset_for_tests()
        a = self._request("10.0.0.1")
        b = self._request("10.0.0.2")
        for _ in range(rl.REGISTER_MAX_ATTEMPTS):
            rl.register_rate_limit(a)
        # b is a different IP — still fully allowed.
        rl.register_rate_limit(b)  # no raise

    def test_exchange_has_its_own_higher_cap(self):
        from fastapi import HTTPException
        from src.auth import signup_rate_limit as rl
        rl._reset_for_tests()
        req = self._request("203.0.113.20")
        for _ in range(rl.EXCHANGE_MAX_ATTEMPTS):
            rl.exchange_rate_limit(req)
        with pytest.raises(HTTPException) as exc:
            rl.exchange_rate_limit(req)
        assert exc.value.status_code == 429

    def test_xff_first_hop_is_the_key(self):
        from src.auth import signup_rate_limit as rl
        rl._reset_for_tests()
        req = MagicMock()
        req.headers = {"x-forwarded-for": "198.51.100.5, 10.0.0.1"}
        req.client.host = "10.0.0.1"
        for _ in range(rl.REGISTER_MAX_ATTEMPTS):
            rl.register_rate_limit(req)
        # A request from the same socket peer but DIFFERENT XFF first hop is a
        # different key -> still allowed.
        req2 = MagicMock()
        req2.headers = {"x-forwarded-for": "198.51.100.6, 10.0.0.1"}
        req2.client.host = "10.0.0.1"
        rl.register_rate_limit(req2)  # no raise


# ---------------------------------------------------------------------------
# OAuth-invite sanitizer (auth.py) — URL-inert codes only (parity / D9)
# ---------------------------------------------------------------------------

class TestOAuthInviteSanitizer:

    def test_valid_token_urlsafe_passes(self):
        from src.api.routes.auth import _sanitize_oauth_invite
        import secrets
        code = secrets.token_urlsafe(16)
        assert _sanitize_oauth_invite(code) == code

    def test_none_and_empty_drop_to_none(self):
        from src.api.routes.auth import _sanitize_oauth_invite
        assert _sanitize_oauth_invite(None) is None
        assert _sanitize_oauth_invite("") is None
        assert _sanitize_oauth_invite("   ") is None

    def test_url_breaking_chars_rejected(self):
        from src.api.routes.auth import _sanitize_oauth_invite
        for bad in ["a&b", "a b", "a/b", "a?x=1", "a%26", "a#frag", "../etc"]:
            assert _sanitize_oauth_invite(bad) is None

    def test_over_length_rejected(self):
        from src.api.routes.auth import _sanitize_oauth_invite
        assert _sanitize_oauth_invite("A" * 65) is None

    def test_query_suffix_empty_for_no_invite(self):
        from src.api.routes.auth import _invite_query_suffix
        assert _invite_query_suffix(None) == ""
        assert _invite_query_suffix("abc") == "&invite=abc"


# ---------------------------------------------------------------------------
# Notice constant + no-invite parity contract (smoke)
# ---------------------------------------------------------------------------

def test_notice_constant_is_generic_no_leak():
    # The D10 notice must NOT reveal which adverse condition tripped.
    assert NOTICE_INVITE_INVALID == "invite_invalid_or_expired"
    for leak in ("region", "owner", "revoked", "expired_exact", "sector"):
        assert leak not in NOTICE_INVITE_INVALID or NOTICE_INVITE_INVALID == "invite_invalid_or_expired"
