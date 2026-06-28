"""Unit tests for RegionInviteService (WO-IL3).

Brief: audit/design-briefs/invite-link-onramp.md §4.5 / §5 acceptance criteria.

These are mock-DB unit tests (Tier A — no real DB), mirroring
tests/unit/test_regional_governance.py. They pin the AUTH-FREE invite kernel:

  * validate_invite fails closed on expired / exhausted / revoked / region-gone /
    owner-changed codes, and returns OK only for a fully-redeemable code.
  * consume_invite is race-safe: a concurrent redeem of a max_uses=1 code yields
    EXACTLY ONE success (the loser sees ERR_INVITE_EXHAUSTED), driven by a mock
    that models SELECT ... FOR UPDATE serialization.
  * the per-owner caps and the NEW region_id-keyed ownership helper reject the
    abuse cases the brief §5 fences exist for.

No auth surface is touched; no account is created.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.region import Region
from src.models.region_invite import RegionInvite, RegionInviteStatus
from src.services.region_invite_service import (
    RegionInviteService,
    DEFAULT_MAX_USES,
    MAX_MAX_USES,
    MAX_ACTIVE_INVITES_PER_OWNER_PER_REGION,
    MAX_REDEMPTIONS_PER_OWNER_PER_WINDOW,
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


def _make_region(*, region_id: uuid.UUID, owner_id) -> Region:
    return Region(id=region_id, name="r", display_name="R", owner_id=owner_id)


# ---------------------------------------------------------------------------
# validate_invite — fails closed on every adverse condition (brief §5 Threat 3/4)
# ---------------------------------------------------------------------------

class TestValidateInvite:

    @pytest.fixture
    def ids(self):
        return {"owner": uuid.uuid4(), "region": uuid.uuid4()}

    def _db_for(self, invite, region):
        """Mock db whose scalar() returns the invite for a code lookup and the
        region for a region lookup. validate_invite issues, in order:
          1. scalar(select RegionInvite where code==...) -> invite
          2. scalar(select Region where id==...)         -> region
        """
        db = AsyncMock()
        db.scalar = AsyncMock(side_effect=[invite, region])
        return db

    @pytest.mark.asyncio
    async def test_valid_code_returns_ok(self, ids):
        invite = _make_invite(owner_id=ids["owner"], region_id=ids["region"])
        region = _make_region(region_id=ids["region"], owner_id=ids["owner"])
        db = self._db_for(invite, region)
        got, reason = await RegionInviteService.validate_invite(db, invite.code)
        assert reason == "OK"
        assert got is invite

    @pytest.mark.asyncio
    async def test_missing_code_rejected(self, ids):
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=None)
        got, reason = await RegionInviteService.validate_invite(db, "nope")
        assert got is None
        assert reason == "ERR_INVITE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_empty_code_rejected_without_query(self, ids):
        db = AsyncMock()
        db.scalar = AsyncMock()
        got, reason = await RegionInviteService.validate_invite(db, "")
        assert (got, reason) == (None, "ERR_INVITE_NOT_FOUND")
        db.scalar.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_revoked_status_rejected(self, ids):
        invite = _make_invite(
            owner_id=ids["owner"], region_id=ids["region"],
            status=RegionInviteStatus.REVOKED.value,
        )
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=invite)
        got, reason = await RegionInviteService.validate_invite(db, invite.code)
        assert got is None
        assert reason == "ERR_INVITE_NOT_ACTIVE"

    @pytest.mark.asyncio
    async def test_exhausted_by_count_rejected(self, ids):
        invite = _make_invite(
            owner_id=ids["owner"], region_id=ids["region"],
            uses=1, max_uses=1,
        )
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=invite)
        got, reason = await RegionInviteService.validate_invite(db, invite.code)
        assert got is None
        assert reason == "ERR_INVITE_EXHAUSTED"

    @pytest.mark.asyncio
    async def test_expired_rejected(self, ids):
        invite = _make_invite(
            owner_id=ids["owner"], region_id=ids["region"],
            expires_at=_utc(days=-1),
        )
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=invite)
        got, reason = await RegionInviteService.validate_invite(db, invite.code)
        assert got is None
        assert reason == "ERR_INVITE_EXPIRED"

    @pytest.mark.asyncio
    async def test_naive_expiry_treated_as_utc(self, ids):
        """A naive expires_at (defensive) does not raise and is honored."""
        invite = _make_invite(
            owner_id=ids["owner"], region_id=ids["region"],
            expires_at=datetime.utcnow() - timedelta(days=1),  # naive, past
        )
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=invite)
        got, reason = await RegionInviteService.validate_invite(db, invite.code)
        assert reason == "ERR_INVITE_EXPIRED"

    @pytest.mark.asyncio
    async def test_region_gone_rejected(self, ids):
        invite = _make_invite(owner_id=ids["owner"], region_id=ids["region"])
        db = AsyncMock()
        # invite found, then region lookup returns None.
        db.scalar = AsyncMock(side_effect=[invite, None])
        got, reason = await RegionInviteService.validate_invite(db, invite.code)
        assert got is None
        assert reason == "ERR_REGION_GONE"

    @pytest.mark.asyncio
    async def test_owner_changed_rejected(self, ids):
        """Owner transferred the region after minting -> invite fails closed."""
        invite = _make_invite(owner_id=ids["owner"], region_id=ids["region"])
        new_owner = uuid.uuid4()
        region = _make_region(region_id=ids["region"], owner_id=new_owner)
        db = AsyncMock()
        db.scalar = AsyncMock(side_effect=[invite, region])
        got, reason = await RegionInviteService.validate_invite(db, invite.code)
        assert got is None
        assert reason == "ERR_OWNER_CHANGED"

    @pytest.mark.asyncio
    async def test_orphaned_creator_rejected(self, ids):
        """created_by NULL (user deleted) -> cannot prove owner-still-owns."""
        invite = _make_invite(owner_id=ids["owner"], region_id=ids["region"])
        invite.created_by = None
        region = _make_region(region_id=ids["region"], owner_id=ids["owner"])
        db = AsyncMock()
        db.scalar = AsyncMock(side_effect=[invite, region])
        got, reason = await RegionInviteService.validate_invite(db, invite.code)
        assert got is None
        assert reason == "ERR_OWNER_CHANGED"


# ---------------------------------------------------------------------------
# consume_invite — race-safe atomic claim under a row lock (brief §5 Threat 5)
# ---------------------------------------------------------------------------

class _LockingDB:
    """Mock AsyncSession modeling SELECT ... FOR UPDATE serialization on ONE
    invite row, plus the region read inside consume_invite.

    A single asyncio.Lock stands in for the DB row lock: a consumer that calls
    execute(...with_for_update()) acquires it and holds it until commit/rollback,
    so a concurrent consumer blocks — exactly the serialization the real FOR
    UPDATE provides. The shared ``invite`` object is the single source of truth
    (re-fetched under the lock), so an increment by the winner is visible to the
    loser's under-lock re-check.
    """

    def __init__(self, invite: RegionInvite, region: Region):
        self._invite = invite
        self._region = region
        self._row_lock = asyncio.Lock()
        self._held = False
        self.commit = AsyncMock(side_effect=self._commit)
        self.rollback = AsyncMock(side_effect=self._rollback)
        self.refresh = AsyncMock()

    async def execute(self, *_args, **_kwargs):
        # The only execute() consume_invite issues is the FOR UPDATE re-fetch.
        await self._row_lock.acquire()
        self._held = True
        result = MagicMock()
        result.scalar_one_or_none.return_value = self._invite
        return result

    async def scalar(self, *_args, **_kwargs):
        # The only scalar() consume_invite issues is the region read.
        return self._region

    async def _commit(self):
        self._release()

    async def _rollback(self):
        self._release()

    def _release(self):
        if self._held:
            self._held = False
            self._row_lock.release()


class TestConsumeInvite:

    @pytest.fixture
    def ids(self):
        return {"owner": uuid.uuid4(), "region": uuid.uuid4()}

    @pytest.mark.asyncio
    async def test_single_consume_increments_and_exhausts(self, ids):
        invite = _make_invite(
            owner_id=ids["owner"], region_id=ids["region"], uses=0, max_uses=1
        )
        region = _make_region(region_id=ids["region"], owner_id=ids["owner"])
        db = _LockingDB(invite, region)
        result = await RegionInviteService.consume_invite(db, invite)
        assert result["ok"] is True
        assert result["code"] == "INVITE_CONSUMED"
        assert invite.uses == 1
        assert invite.status == RegionInviteStatus.EXHAUSTED.value
        db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_multi_use_does_not_exhaust_early(self, ids):
        invite = _make_invite(
            owner_id=ids["owner"], region_id=ids["region"], uses=0, max_uses=3
        )
        region = _make_region(region_id=ids["region"], owner_id=ids["owner"])
        db = _LockingDB(invite, region)
        result = await RegionInviteService.consume_invite(db, invite)
        assert result["ok"] is True
        assert invite.uses == 1
        assert invite.status == RegionInviteStatus.ACTIVE.value

    @pytest.mark.asyncio
    async def test_already_exhausted_rejected_under_lock(self, ids):
        invite = _make_invite(
            owner_id=ids["owner"], region_id=ids["region"],
            uses=1, max_uses=1, status=RegionInviteStatus.EXHAUSTED.value,
        )
        region = _make_region(region_id=ids["region"], owner_id=ids["owner"])
        db = _LockingDB(invite, region)
        result = await RegionInviteService.consume_invite(db, invite)
        assert result["ok"] is False
        # status is non-active -> rejected on status (the first under-lock gate).
        assert result["code"] == "ERR_INVITE_NOT_ACTIVE"
        db.rollback.assert_awaited()

    @pytest.mark.asyncio
    async def test_expired_rejected_under_lock(self, ids):
        invite = _make_invite(
            owner_id=ids["owner"], region_id=ids["region"],
            uses=0, max_uses=1, expires_at=_utc(days=-1),
        )
        region = _make_region(region_id=ids["region"], owner_id=ids["owner"])
        db = _LockingDB(invite, region)
        result = await RegionInviteService.consume_invite(db, invite)
        assert result["ok"] is False
        assert result["code"] == "ERR_INVITE_EXPIRED"

    @pytest.mark.asyncio
    async def test_owner_changed_rejected_under_lock(self, ids):
        invite = _make_invite(
            owner_id=ids["owner"], region_id=ids["region"], uses=0, max_uses=1
        )
        region = _make_region(region_id=ids["region"], owner_id=uuid.uuid4())
        db = _LockingDB(invite, region)
        result = await RegionInviteService.consume_invite(db, invite)
        assert result["ok"] is False
        assert result["code"] == "ERR_OWNER_CHANGED"
        db.rollback.assert_awaited()

    @pytest.mark.asyncio
    async def test_region_gone_rejected_under_lock(self, ids):
        invite = _make_invite(
            owner_id=ids["owner"], region_id=ids["region"], uses=0, max_uses=1
        )
        db = _LockingDB(invite, region=None)
        result = await RegionInviteService.consume_invite(db, invite)
        assert result["ok"] is False
        assert result["code"] == "ERR_REGION_GONE"

    @pytest.mark.asyncio
    async def test_concurrent_consume_of_one_use_yields_exactly_one_success(self, ids):
        """The core race-safety acceptance: two simultaneous redeems of a
        max_uses=1 code -> exactly one {ok: True}, the other ERR_INVITE_EXHAUSTED.
        The _LockingDB serializes the FOR UPDATE re-fetch; the loser re-checks
        uses>=max_uses UNDER the lock after the winner's increment is visible."""
        invite = _make_invite(
            owner_id=ids["owner"], region_id=ids["region"], uses=0, max_uses=1
        )
        region = _make_region(region_id=ids["region"], owner_id=ids["owner"])
        db = _LockingDB(invite, region)

        results = await asyncio.gather(
            RegionInviteService.consume_invite(db, invite),
            RegionInviteService.consume_invite(db, invite),
        )
        successes = [r for r in results if r["ok"]]
        failures = [r for r in results if not r["ok"]]
        assert len(successes) == 1
        assert len(failures) == 1
        assert failures[0]["code"] == "ERR_INVITE_EXHAUSTED"
        assert invite.uses == 1
        assert invite.status == RegionInviteStatus.EXHAUSTED.value


# ---------------------------------------------------------------------------
# owns_region — NEW region_id-keyed ownership helper (brief §5 Threat 6)
# ---------------------------------------------------------------------------

class TestOwnsRegion:

    @pytest.mark.asyncio
    async def test_owner_of_this_region_passes(self):
        owner = uuid.uuid4()
        region_id = uuid.uuid4()
        region = _make_region(region_id=region_id, owner_id=owner)
        db = AsyncMock()
        db.execute.return_value.scalar_one_or_none.return_value = region
        got = await RegionInviteService.owns_region(db, owner, region_id)
        assert got is region

    @pytest.mark.asyncio
    async def test_non_owner_gets_none(self):
        db = AsyncMock()
        # The AND(id, owner_id) query matches no row for a non-owner.
        db.execute.return_value.scalar_one_or_none.return_value = None
        got = await RegionInviteService.owns_region(db, uuid.uuid4(), uuid.uuid4())
        assert got is None


# ---------------------------------------------------------------------------
# mint_invite — owner gate, validation, per-owner caps (brief §5 Threat 1/2/6)
# ---------------------------------------------------------------------------

class TestMintInvite:

    @pytest.fixture
    def ids(self):
        return {"owner": uuid.uuid4(), "region": uuid.uuid4()}

    @pytest.mark.asyncio
    async def test_non_owner_rejected(self, ids):
        db = AsyncMock()
        db.execute.return_value.scalar_one_or_none.return_value = None  # owns_region None
        result = await RegionInviteService.mint_invite(
            db, ids["owner"], ids["region"]
        )
        assert result["ok"] is False
        assert result["code"] == "ERR_NOT_REGION_OWNER"
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_max_uses_rejected(self, ids):
        region = _make_region(region_id=ids["region"], owner_id=ids["owner"])
        db = AsyncMock()
        db.execute.return_value.scalar_one_or_none.return_value = region
        result = await RegionInviteService.mint_invite(
            db, ids["owner"], ids["region"], max_uses=MAX_MAX_USES + 1
        )
        assert result["ok"] is False
        assert result["code"] == "ERR_INVALID_MAX_USES"

    @pytest.mark.asyncio
    async def test_zero_max_uses_rejected(self, ids):
        region = _make_region(region_id=ids["region"], owner_id=ids["owner"])
        db = AsyncMock()
        db.execute.return_value.scalar_one_or_none.return_value = region
        result = await RegionInviteService.mint_invite(
            db, ids["owner"], ids["region"], max_uses=0
        )
        assert result["ok"] is False
        assert result["code"] == "ERR_INVALID_MAX_USES"

    @pytest.mark.asyncio
    async def test_past_expiry_rejected(self, ids):
        region = _make_region(region_id=ids["region"], owner_id=ids["owner"])
        db = AsyncMock()
        db.execute.return_value.scalar_one_or_none.return_value = region
        result = await RegionInviteService.mint_invite(
            db, ids["owner"], ids["region"], expires_at=_utc(days=-1)
        )
        assert result["ok"] is False
        assert result["code"] == "ERR_INVALID_EXPIRY"

    @pytest.mark.asyncio
    async def test_active_invite_cap_rejected(self, ids):
        region = _make_region(region_id=ids["region"], owner_id=ids["owner"])
        db = AsyncMock()
        db.execute.return_value.scalar_one_or_none.return_value = region
        # scalar() is called for the active-invite count -> at cap.
        db.scalar = AsyncMock(return_value=MAX_ACTIVE_INVITES_PER_OWNER_PER_REGION)
        result = await RegionInviteService.mint_invite(
            db, ids["owner"], ids["region"]
        )
        assert result["ok"] is False
        assert result["code"] == "ERR_ACTIVE_INVITE_CAP"
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_redemption_cap_rejected(self, ids):
        region = _make_region(region_id=ids["region"], owner_id=ids["owner"])
        db = AsyncMock()
        db.execute.return_value.scalar_one_or_none.return_value = region
        # first scalar() = active count (under cap), second = redemptions (at cap).
        db.scalar = AsyncMock(
            side_effect=[0, MAX_REDEMPTIONS_PER_OWNER_PER_WINDOW]
        )
        result = await RegionInviteService.mint_invite(
            db, ids["owner"], ids["region"]
        )
        assert result["ok"] is False
        assert result["code"] == "ERR_REDEMPTION_CAP"
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_mint_success_creates_high_entropy_code(self, ids):
        region = _make_region(region_id=ids["region"], owner_id=ids["owner"])
        db = AsyncMock()
        db.execute.return_value.scalar_one_or_none.return_value = region
        db.scalar = AsyncMock(side_effect=[0, 0])  # active count, redemptions

        added = {}

        def _add(obj):
            added["invite"] = obj

        db.add = MagicMock(side_effect=_add)
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        result = await RegionInviteService.mint_invite(
            db, ids["owner"], ids["region"]
        )
        assert result["ok"] is True
        assert result["code"] == "INVITE_CREATED"
        inv = added["invite"]
        assert isinstance(inv, RegionInvite)
        assert inv.region_id == ids["region"]
        assert inv.created_by == ids["owner"]
        assert inv.max_uses == DEFAULT_MAX_USES
        assert inv.uses == 0
        assert inv.status == RegionInviteStatus.ACTIVE.value
        # high-entropy code: non-trivial length, URL-safe, expiry in the future.
        assert isinstance(inv.code, str) and len(inv.code) >= 20
        assert inv.expires_at > datetime.now(timezone.utc)
        db.commit.assert_awaited()


# ---------------------------------------------------------------------------
# revoke_invite — owner-only, idempotent (brief §4.2)
# ---------------------------------------------------------------------------

class TestRevokeInvite:

    @pytest.fixture
    def ids(self):
        return {"owner": uuid.uuid4(), "region": uuid.uuid4()}

    @pytest.mark.asyncio
    async def test_revoke_sets_status_and_timestamp(self, ids):
        invite = _make_invite(owner_id=ids["owner"], region_id=ids["region"])
        region = _make_region(region_id=ids["region"], owner_id=ids["owner"])
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=invite)
        db.execute.return_value.scalar_one_or_none.return_value = region
        result = await RegionInviteService.revoke_invite(
            db, ids["owner"], invite.id
        )
        assert result["ok"] is True
        assert result["code"] == "INVITE_REVOKED"
        assert invite.status == RegionInviteStatus.REVOKED.value
        assert invite.revoked_at is not None
        db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_revoke_missing_invite_404(self, ids):
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=None)
        result = await RegionInviteService.revoke_invite(
            db, ids["owner"], uuid.uuid4()
        )
        assert result["ok"] is False
        assert result["code"] == "ERR_INVITE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_revoke_by_non_minter_rejected(self, ids):
        invite = _make_invite(owner_id=ids["owner"], region_id=ids["region"])
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=invite)
        result = await RegionInviteService.revoke_invite(
            db, uuid.uuid4(), invite.id  # different caller than the minter
        )
        assert result["ok"] is False
        assert result["code"] == "ERR_NOT_INVITE_OWNER"

    @pytest.mark.asyncio
    async def test_revoke_when_no_longer_region_owner_rejected(self, ids):
        """Minter who lost the region cannot revoke (region check fails)."""
        invite = _make_invite(owner_id=ids["owner"], region_id=ids["region"])
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=invite)
        db.execute.return_value.scalar_one_or_none.return_value = None  # owns_region None
        result = await RegionInviteService.revoke_invite(
            db, ids["owner"], invite.id
        )
        assert result["ok"] is False
        assert result["code"] == "ERR_NOT_REGION_OWNER"

    @pytest.mark.asyncio
    async def test_revoke_already_revoked_is_idempotent(self, ids):
        invite = _make_invite(
            owner_id=ids["owner"], region_id=ids["region"],
            status=RegionInviteStatus.REVOKED.value,
        )
        region = _make_region(region_id=ids["region"], owner_id=ids["owner"])
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=invite)
        db.execute.return_value.scalar_one_or_none.return_value = region
        result = await RegionInviteService.revoke_invite(
            db, ids["owner"], invite.id
        )
        assert result["ok"] is True
        assert result["code"] == "INVITE_ALREADY_REVOKED"
        # idempotent — no second commit needed.
        db.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# list_invites — owner-scoped (brief §4.2)
# ---------------------------------------------------------------------------

class TestListInvites:

    @pytest.mark.asyncio
    async def test_non_owner_rejected(self):
        db = AsyncMock()
        db.execute.return_value.scalar_one_or_none.return_value = None  # owns_region None
        result = await RegionInviteService.list_invites(
            db, uuid.uuid4(), uuid.uuid4()
        )
        assert result["ok"] is False
        assert result["code"] == "ERR_NOT_REGION_OWNER"

    @pytest.mark.asyncio
    async def test_owner_gets_their_invites(self):
        owner = uuid.uuid4()
        region_id = uuid.uuid4()
        region = _make_region(region_id=region_id, owner_id=owner)
        invites = [
            _make_invite(owner_id=owner, region_id=region_id),
            _make_invite(owner_id=owner, region_id=region_id),
        ]
        db = AsyncMock()
        # owns_region uses execute().scalar_one_or_none(); list uses
        # execute().scalars().all(). Configure both on the same mock result.
        result_mock = db.execute.return_value
        result_mock.scalar_one_or_none.return_value = region
        result_mock.scalars.return_value.all.return_value = invites
        result = await RegionInviteService.list_invites(db, owner, region_id)
        assert result["ok"] is True
        assert result["invites"] == invites
