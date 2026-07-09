"""WO-REGOV-OWNER-DIALS -- expose the two canon owner controls with live
columns but no writer: the region's ``governance_quorum_pct`` participation
threshold (SYSTEMS/regional-governance.md:91, band [0.25, 0.60]) and each
member's ``voting_power`` / ``local_rank`` (:71-76, voting_power band
[0.0, 5.0], citizen tier target 1.5; the :77 auto-recalc is DESIGN-ONLY and
is deliberately NOT built here).

DB-free, direct-call route testing per test_region_funded_tradedock.py's
addendum (its ``TestGetMyRegionTreasuryBalance`` -- MagicMock/AsyncMock
session, no real engine). That addendum only ever reads a single row, so
this file adds two things of its own:

  * ``_FakeAsyncSession`` -- a small scripted-queue stand-in whose
    ``execute()`` calls are all recorded (region lookup, membership lookup,
    the trailing UPDATE), not just mocked once.
  * ``_extract_update_values`` -- pulls ``{column_name: literal_value}``
    back out of an ORM ``update(...).values(...)`` statement's private
    ``_values`` mapping (a stable SQLAlchemy Core internal), so a test can
    assert exactly what a route's UPDATE would persist without a real
    engine or a second read-back query.

Covers: PUT /my-region/governance's new governance_quorum_pct field
(schema-level Field bounds + persistence, including "omitted leaves the
column untouched"); the new PATCH /my-region/members/{player_id} route
(voting_power schema bounds incl. the 0.0-is-a-valid-value trap, local_rank
50-char cap, partial-PATCH persistence, non-member 404, non-owner 404,
empty-body 400); and a read-only pin that
regional_governance_service.quorum_pct_for_region reads the dialed column
(no service-file edits made by this WO).
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.api.routes import regional_governance as gov
from src.models.region import Region
from src.services.regional_governance_service import quorum_pct_for_region


# --- shared fakes ------------------------------------------------------------


class _FakeAsyncSession:
    """DB-free AsyncSession stand-in for the async routes under test.
    ``scripted`` is a queue of return values consumed by execute() in call
    order (region lookup, then membership lookup, ...); every execute()
    call -- including the trailing UPDATE -- is recorded in ``calls`` so a
    test can pull the exact UPDATE .values() payload back out via
    _extract_update_values."""

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
    would return it."""
    return MagicMock(scalar_one_or_none=MagicMock(return_value=value))


def _extract_update_values(stmt: Any) -> Dict[str, Any]:
    """Pull {column_name: literal_value} out of an update(...).values(...)
    statement's `_values` mapping -- proves exactly what a route's UPDATE
    would persist, without a real engine or a read-back query."""
    return {col.name: bind.value for col, bind in stmt._values.items()}


# --- PUT /my-region/governance -- governance_quorum_pct ----------------------


@pytest.mark.unit
class TestGovernanceConfigUpdateQuorumSchema:
    """Schema-level [0.25, 0.60] band (Field bounds fire at Pydantic
    construction, before the route ever runs)."""

    def test_above_band_rejected(self) -> None:
        with pytest.raises(ValidationError):
            gov.GovernanceConfigUpdate(
                governance_type="democracy",
                voting_threshold=0.51,
                election_frequency_days=90,
                governance_quorum_pct=0.70,
            )

    def test_below_band_rejected(self) -> None:
        with pytest.raises(ValidationError):
            gov.GovernanceConfigUpdate(
                governance_type="democracy",
                voting_threshold=0.51,
                election_frequency_days=90,
                governance_quorum_pct=0.10,
            )

    @pytest.mark.parametrize("pct", [0.25, 0.60])
    def test_band_boundary_accepted(self, pct: float) -> None:
        config = gov.GovernanceConfigUpdate(
            governance_type="democracy",
            voting_threshold=0.51,
            election_frequency_days=90,
            governance_quorum_pct=pct,
        )
        assert config.governance_quorum_pct == pct

    def test_omitted_defaults_to_none(self) -> None:
        config = gov.GovernanceConfigUpdate(
            governance_type="democracy",
            voting_threshold=0.51,
            election_frequency_days=90,
        )
        assert config.governance_quorum_pct is None


@pytest.mark.unit
class TestUpdateGovernanceConfigQuorumPersistence:
    """PUT /my-region/governance persists governance_quorum_pct when
    supplied, and leaves the column untouched when the caller omits it
    (backward compatible with callers built before this WO)."""

    @pytest.mark.asyncio
    async def test_quorum_pct_persists_when_supplied(self) -> None:
        owner_user = SimpleNamespace(id=uuid.uuid4())
        region = SimpleNamespace(id=uuid.uuid4())
        db = _FakeAsyncSession([_scalar(region)])

        config = gov.GovernanceConfigUpdate(
            governance_type="democracy",
            voting_threshold=0.55,
            election_frequency_days=90,
            governance_quorum_pct=0.50,
        )
        await gov.update_governance_config(config=config, current_user=owner_user, db=db)

        assert db.committed
        values = _extract_update_values(db.calls[-1])
        assert values["governance_quorum_pct"] == 0.50
        # boundary too, in the same persistence path (not just schema)
        assert values["voting_threshold"] == 0.55

    @pytest.mark.asyncio
    async def test_quorum_pct_omitted_leaves_column_untouched(self) -> None:
        owner_user = SimpleNamespace(id=uuid.uuid4())
        region = SimpleNamespace(id=uuid.uuid4())
        db = _FakeAsyncSession([_scalar(region)])

        config = gov.GovernanceConfigUpdate(
            governance_type="autocracy",
            voting_threshold=0.51,
            election_frequency_days=120,
        )
        await gov.update_governance_config(config=config, current_user=owner_user, db=db)

        assert db.committed
        values = _extract_update_values(db.calls[-1])
        assert "governance_quorum_pct" not in values
        assert values["election_frequency_days"] == 120


@pytest.mark.unit
class TestQuorumPctForRegionReadsDialedValue:
    """Read-path pin (no edits to regional_governance_service.py in this
    WO) -- quorum_pct_for_region reads the owner-dialed column, not a
    hardcoded default, once it's non-NULL."""

    def test_reads_the_dialed_column(self) -> None:
        region = Region(id=uuid.uuid4(), governance_quorum_pct=Decimal("0.45"))
        assert quorum_pct_for_region(region) == Decimal("0.45")

    def test_band_edges_round_trip_unclamped(self) -> None:
        region = Region(id=uuid.uuid4(), governance_quorum_pct=Decimal("0.60"))
        assert quorum_pct_for_region(region) == Decimal("0.60")

    def test_null_column_falls_back_to_canon_default(self) -> None:
        region = Region(id=uuid.uuid4(), governance_quorum_pct=None)
        assert quorum_pct_for_region(region) == Decimal("0.33")


# --- PATCH /my-region/members/{player_id} -------------------------------------


@pytest.mark.unit
class TestMemberDialsUpdateSchema:
    """Schema-level bounds for the new PATCH body."""

    def test_voting_power_above_band_rejected(self) -> None:
        with pytest.raises(ValidationError):
            gov.MemberDialsUpdate(voting_power=6.0)

    def test_voting_power_below_band_rejected(self) -> None:
        with pytest.raises(ValidationError):
            gov.MemberDialsUpdate(voting_power=-0.1)

    def test_voting_power_zero_is_a_valid_value(self) -> None:
        # Regression guard: `if body.voting_power:` would wrongly treat 0.0
        # as "not supplied" -- the route must use `is not None`.
        assert gov.MemberDialsUpdate(voting_power=0.0).voting_power == 0.0

    @pytest.mark.parametrize("power", [0.0, 5.0])
    def test_voting_power_band_boundary_accepted(self, power: float) -> None:
        assert gov.MemberDialsUpdate(voting_power=power).voting_power == power

    def test_local_rank_over_50_chars_rejected(self) -> None:
        with pytest.raises(ValidationError):
            gov.MemberDialsUpdate(local_rank="x" * 51)

    def test_local_rank_at_50_chars_accepted(self) -> None:
        body = gov.MemberDialsUpdate(local_rank="x" * 50)
        assert body.local_rank is not None and len(body.local_rank) == 50


@pytest.mark.unit
class TestUpdateMemberDialsRoute:
    """PATCH /my-region/members/{player_id} -- owner-scoped partial update."""

    def _region_and_membership(self):
        region = SimpleNamespace(id=uuid.uuid4())
        membership = SimpleNamespace(id=uuid.uuid4(), voting_power=1.0, local_rank=None)
        return region, membership

    @pytest.mark.asyncio
    async def test_voting_power_persists(self) -> None:
        owner_user = SimpleNamespace(id=uuid.uuid4())
        region, membership = self._region_and_membership()
        db = _FakeAsyncSession([_scalar(region), _scalar(membership)])

        result = await gov.update_member_dials(
            player_id=uuid.uuid4(),
            body=gov.MemberDialsUpdate(voting_power=1.5),
            current_user=owner_user,
            db=db,
        )

        assert db.committed
        values = _extract_update_values(db.calls[-1])
        assert values == {"voting_power": 1.5}
        assert result["voting_power"] == 1.5
        # local_rank untouched -> response falls back to the existing value
        assert result["local_rank"] is None

    @pytest.mark.asyncio
    async def test_zero_voting_power_is_allowed_and_persists(self) -> None:
        owner_user = SimpleNamespace(id=uuid.uuid4())
        region, membership = self._region_and_membership()
        db = _FakeAsyncSession([_scalar(region), _scalar(membership)])

        result = await gov.update_member_dials(
            player_id=uuid.uuid4(),
            body=gov.MemberDialsUpdate(voting_power=0.0),
            current_user=owner_user,
            db=db,
        )

        values = _extract_update_values(db.calls[-1])
        assert values == {"voting_power": 0.0}
        assert result["voting_power"] == 0.0

    @pytest.mark.asyncio
    async def test_local_rank_alone_persists_without_touching_voting_power(self) -> None:
        owner_user = SimpleNamespace(id=uuid.uuid4())
        region, membership = self._region_and_membership()
        db = _FakeAsyncSession([_scalar(region), _scalar(membership)])

        result = await gov.update_member_dials(
            player_id=uuid.uuid4(),
            body=gov.MemberDialsUpdate(local_rank="Fleet Admiral"),
            current_user=owner_user,
            db=db,
        )

        values = _extract_update_values(db.calls[-1])
        assert values == {"local_rank": "Fleet Admiral"}
        assert result["local_rank"] == "Fleet Admiral"
        # voting_power untouched -> response falls back to the existing value
        assert result["voting_power"] == membership.voting_power

    @pytest.mark.asyncio
    async def test_non_member_player_id_is_404(self) -> None:
        owner_user = SimpleNamespace(id=uuid.uuid4())
        region, _membership = self._region_and_membership()
        db = _FakeAsyncSession([_scalar(region), _scalar(None)])

        with pytest.raises(HTTPException) as exc:
            await gov.update_member_dials(
                player_id=uuid.uuid4(),
                body=gov.MemberDialsUpdate(voting_power=1.0),
                current_user=owner_user,
                db=db,
            )
        assert exc.value.status_code == 404
        assert not db.committed

    @pytest.mark.asyncio
    async def test_caller_owning_no_region_is_404_before_membership_lookup(self) -> None:
        # Pins the existing verify_region_owner convention (get_my_region /
        # get_regional_members) for this new route too: a caller who owns no
        # region at all gets 404, and the membership query never fires.
        db = _FakeAsyncSession([_scalar(None)])

        with pytest.raises(HTTPException) as exc:
            await gov.update_member_dials(
                player_id=uuid.uuid4(),
                body=gov.MemberDialsUpdate(voting_power=1.0),
                current_user=SimpleNamespace(id=uuid.uuid4()),
                db=db,
            )
        assert exc.value.status_code == 404
        assert len(db.calls) == 1

    @pytest.mark.asyncio
    async def test_empty_body_is_400(self) -> None:
        owner_user = SimpleNamespace(id=uuid.uuid4())
        region, membership = self._region_and_membership()
        db = _FakeAsyncSession([_scalar(region), _scalar(membership)])

        with pytest.raises(HTTPException) as exc:
            await gov.update_member_dials(
                player_id=uuid.uuid4(),
                body=gov.MemberDialsUpdate(),
                current_user=owner_user,
                db=db,
            )
        assert exc.value.status_code == 400
        assert not db.committed
