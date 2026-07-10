"""WO-DRIFT-admin-gov-multiregion-owner-500 -- /my-region/* 500'd
(unhandled MultipleResultsFound) for any user owning 2+ regions, because
get_user_region's ``Region.owner_id == user_id`` lookup assumed at most one
row. Max-ruled fix shape: an explicit ``region_id`` query param on the
/my-region/* surface.

  - region_id given + owned by caller       -> 200, that region
  - region_id given + owned by someone else -> 403 (authz denial; never
    distinguishes "doesn't exist" from "someone else's region" -- no leak)
  - region_id absent + exactly 1 owned      -> 200, that region (unchanged
    back-compat path -- every existing /my-region/* caller keeps working)
  - region_id absent + 0 owned              -> 404 (unchanged)
  - region_id absent + 2+ owned             -> 400 listing every owned
    region -- NEVER a silent pick, NEVER a 500

DB-free, direct-call route testing per test_governance_owner_dials.py's
``_FakeAsyncSession`` (a scripted execute() queue, no real engine). Two
additional scripted-result shapes are needed here beyond that file's
``_scalar()``:

  * ``_raises_multiple()`` -- a result whose ``scalar_one_or_none()`` raises
    ``sqlalchemy.exc.MultipleResultsFound``, reproducing the exact crash the
    unpatched code hit on a real 2-row query.
  * ``_scalars_list()`` -- a result whose ``scalars().all()`` returns a list,
    for the get_owned_regions() pick-list follow-up query.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import MultipleResultsFound

from src.api.routes import regional_governance as gov


class _FakeAsyncSession:
    """DB-free AsyncSession stand-in. ``scripted`` is a queue of return
    values consumed by execute() in call order; every execute() call is
    recorded in ``calls``."""

    def __init__(self, scripted: List[Any]) -> None:
        self._scripted = list(scripted)
        self.calls: List[Any] = []
        self.committed = False

    async def execute(self, stmt: Any) -> Any:
        self.calls.append(stmt)
        if self._scripted:
            return self._scripted.pop(0)
        return MagicMock(scalar_one_or_none=MagicMock(return_value=None))

    async def commit(self) -> None:
        self.committed = True


def _scalar(value: Any) -> MagicMock:
    """Wrap a value the way `(await db.execute(...)).scalar_one_or_none()`
    would return it for a normal (<=1 row) result."""
    return MagicMock(scalar_one_or_none=MagicMock(return_value=value))


def _raises_multiple() -> MagicMock:
    """A result whose scalar_one_or_none() raises MultipleResultsFound --
    reproduces the real 2+-row crash this WO fixes."""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(
        side_effect=MultipleResultsFound("Multiple rows were found when one was required")
    )
    return result


def _scalars_list(items: List[Any]) -> MagicMock:
    """A result whose scalars().all() returns items -- the get_owned_regions
    pick-list follow-up query."""
    return MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=items))))


def _region(owner_id: uuid.UUID, name: str = "region-a") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), name=name, display_name=name.title(), owner_id=owner_id
    )


# --- get_user_region / verify_region_owner: the shared disambiguation logic --


@pytest.mark.unit
class TestGetUserRegionDisambiguation:
    @pytest.mark.asyncio
    async def test_no_region_id_one_owned_region_returns_it(self) -> None:
        user_id = uuid.uuid4()
        region = _region(user_id)
        db = _FakeAsyncSession([_scalar(region)])

        result = await gov.get_user_region(db, user_id)

        assert result is region
        assert len(db.calls) == 1

    @pytest.mark.asyncio
    async def test_no_region_id_zero_owned_regions_returns_none(self) -> None:
        user_id = uuid.uuid4()
        db = _FakeAsyncSession([_scalar(None)])

        result = await gov.get_user_region(db, user_id)

        assert result is None

    @pytest.mark.asyncio
    async def test_no_region_id_two_owned_regions_raises_ambiguous(self) -> None:
        user_id = uuid.uuid4()
        region_a = _region(user_id, "alpha")
        region_b = _region(user_id, "bravo")
        db = _FakeAsyncSession([_raises_multiple(), _scalars_list([region_a, region_b])])

        with pytest.raises(gov.AmbiguousRegionOwnerError) as exc:
            await gov.get_user_region(db, user_id)

        assert exc.value.regions == [region_a, region_b]
        # the crash-reproducing query, then the pick-list follow-up query
        assert len(db.calls) == 2

    @pytest.mark.asyncio
    async def test_region_id_owned_by_caller_returns_it_regardless_of_count(self) -> None:
        user_id = uuid.uuid4()
        region = _region(user_id)
        db = _FakeAsyncSession([_scalar(region)])

        result = await gov.get_user_region(db, user_id, region_id=region.id)

        assert result is region

    @pytest.mark.asyncio
    async def test_region_id_not_owned_by_caller_returns_none(self) -> None:
        user_id = uuid.uuid4()
        db = _FakeAsyncSession([_scalar(None)])

        result = await gov.get_user_region(db, user_id, region_id=uuid.uuid4())

        assert result is None


@pytest.mark.unit
class TestVerifyRegionOwnerHTTPBehaviors:
    """The 4 ruled behaviors, expressed as the actual HTTP outcomes."""

    @pytest.mark.asyncio
    async def test_one_owner_no_region_id_is_200_unchanged(self) -> None:
        user = SimpleNamespace(id=uuid.uuid4())
        region = _region(user.id)
        db = _FakeAsyncSession([_scalar(region)])

        result = await gov.verify_region_owner(db, user)

        assert result is region

    @pytest.mark.asyncio
    async def test_two_owner_with_valid_owned_region_id_is_200(self) -> None:
        user = SimpleNamespace(id=uuid.uuid4())
        region_a = _region(user.id, "alpha")
        db = _FakeAsyncSession([_scalar(region_a)])

        result = await gov.verify_region_owner(db, user, region_id=region_a.id)

        assert result is region_a

    @pytest.mark.asyncio
    async def test_two_owner_no_region_id_is_400_and_lists_both(self) -> None:
        user = SimpleNamespace(id=uuid.uuid4())
        region_a = _region(user.id, "alpha")
        region_b = _region(user.id, "bravo")
        db = _FakeAsyncSession([_raises_multiple(), _scalars_list([region_a, region_b])])

        with pytest.raises(HTTPException) as exc:
            await gov.verify_region_owner(db, user)

        assert exc.value.status_code == 400
        detail = exc.value.detail
        assert detail["code"] == "ERR_AMBIGUOUS_REGION_OWNER"
        listed_ids = {r["id"] for r in detail["regions"]}
        assert listed_ids == {str(region_a.id), str(region_b.id)}
        listed_names = {r["name"] for r in detail["regions"]}
        assert listed_names == {"alpha", "bravo"}

    @pytest.mark.asyncio
    async def test_region_id_owned_by_someone_else_is_403(self) -> None:
        user = SimpleNamespace(id=uuid.uuid4())
        db = _FakeAsyncSession([_scalar(None)])

        with pytest.raises(HTTPException) as exc:
            await gov.verify_region_owner(db, user, region_id=uuid.uuid4())

        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_region_id_that_does_not_exist_is_403_not_404(self) -> None:
        """Never leak existence: a nonexistent region_id gets the identical
        403 a real-but-not-owned region_id gets."""
        user = SimpleNamespace(id=uuid.uuid4())
        db = _FakeAsyncSession([_scalar(None)])

        with pytest.raises(HTTPException) as exc:
            await gov.verify_region_owner(db, user, region_id=uuid.uuid4())

        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_zero_owner_no_region_id_is_404_unchanged(self) -> None:
        user = SimpleNamespace(id=uuid.uuid4())
        db = _FakeAsyncSession([_scalar(None)])

        with pytest.raises(HTTPException) as exc:
            await gov.verify_region_owner(db, user)

        assert exc.value.status_code == 404


# --- route-level pass-through: proves the query param actually reaches -------
# --- verify_region_owner for both a query-only GET and a body+path PATCH ----


@pytest.mark.unit
class TestRegionIdThreadedThroughRoutes:
    @pytest.mark.asyncio
    async def test_get_my_region_no_region_id_two_owners_is_400(self) -> None:
        user = SimpleNamespace(id=uuid.uuid4())
        region_a = _region(user.id, "alpha")
        region_b = _region(user.id, "bravo")
        db = _FakeAsyncSession([_raises_multiple(), _scalars_list([region_a, region_b])])

        with pytest.raises(HTTPException) as exc:
            await gov.get_my_region(current_user=user, db=db)

        assert exc.value.status_code == 400
        assert exc.value.detail["code"] == "ERR_AMBIGUOUS_REGION_OWNER"

    @pytest.mark.asyncio
    async def test_get_my_region_with_owned_region_id_is_200(self) -> None:
        user = SimpleNamespace(id=uuid.uuid4())
        region = gov.Region(
            id=uuid.uuid4(),
            name="alpha",
            display_name="Alpha",
            owner_id=user.id,
            subscription_tier="standard",
            subscription_status="active",
            status="active",
            governance_type="autocracy",
            voting_threshold=0.51,
            election_frequency_days=90,
            constitutional_text=None,
            tax_rate=0.10,
            trade_bonuses={},
            economic_specialization=None,
            starting_credits=1000,
            starting_ship="scout",
            language_pack={},
            aesthetic_theme={},
            traditions={},
            total_sectors=500,
            active_players_30d=0,
            total_trade_volume=0.0,
            treasury_balance=0,
        )
        # Real ORM object needs created_at/updated_at for the response dict's
        # .isoformat() calls -- stamp them directly (no flush in this test).
        from datetime import datetime, timezone

        region.created_at = datetime.now(timezone.utc)
        region.updated_at = datetime.now(timezone.utc)
        db = _FakeAsyncSession([_scalar(region)])

        result = await gov.get_my_region(current_user=user, db=db, region_id=region.id)

        assert result["id"] == str(region.id)

    @pytest.mark.asyncio
    async def test_update_member_dials_no_region_id_two_owners_is_400(self) -> None:
        user = SimpleNamespace(id=uuid.uuid4())
        region_a = _region(user.id, "alpha")
        region_b = _region(user.id, "bravo")
        db = _FakeAsyncSession([_raises_multiple(), _scalars_list([region_a, region_b])])

        with pytest.raises(HTTPException) as exc:
            await gov.update_member_dials(
                player_id=uuid.uuid4(),
                body=gov.MemberDialsUpdate(voting_power=1.0),
                current_user=user,
                db=db,
            )

        assert exc.value.status_code == 400
        assert exc.value.detail["code"] == "ERR_AMBIGUOUS_REGION_OWNER"
        assert not db.committed

    @pytest.mark.asyncio
    async def test_update_member_dials_region_id_owned_by_other_is_403(self) -> None:
        user = SimpleNamespace(id=uuid.uuid4())
        db = _FakeAsyncSession([_scalar(None)])

        with pytest.raises(HTTPException) as exc:
            await gov.update_member_dials(
                player_id=uuid.uuid4(),
                body=gov.MemberDialsUpdate(voting_power=1.0),
                current_user=user,
                db=db,
                region_id=uuid.uuid4(),
            )

        assert exc.value.status_code == 403
        assert not db.committed
