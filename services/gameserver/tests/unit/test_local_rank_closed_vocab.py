"""LEG-2576 (vocab half) — closed local_rank vocabulary on MemberDialsUpdate.

Canon: ADR-0093 item 20 / FEATURES/gameplay/regional-governance.md —
administrator | moderator | null only. Honorific, not authority.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.api.routes.regional_governance import ALLOWED_LOCAL_RANKS, MemberDialsUpdate


def test_allowed_local_ranks_frozenset():
    assert ALLOWED_LOCAL_RANKS == frozenset({"administrator", "moderator"})


@pytest.mark.parametrize("rank", ["administrator", "moderator"])
def test_valid_local_rank_accepted(rank: str):
    body = MemberDialsUpdate(local_rank=rank)
    assert body.local_rank == rank


def test_null_and_blank_clear_to_none():
    assert MemberDialsUpdate(local_rank=None).local_rank is None
    assert MemberDialsUpdate(local_rank="").local_rank is None
    assert MemberDialsUpdate(local_rank="   ").local_rank is None


@pytest.mark.parametrize("rank", ["Senator", "admin", "ADMINISTRATOR", "owner", "x"])
def test_invalid_local_rank_rejected(rank: str):
    with pytest.raises(ValidationError) as exc:
        MemberDialsUpdate(local_rank=rank)
    assert "local_rank" in str(exc.value)


def test_exclude_unset_distinguishes_clear_from_omit():
    omitted = MemberDialsUpdate(voting_power=1.0).model_dump(exclude_unset=True)
    assert "local_rank" not in omitted
    cleared = MemberDialsUpdate(local_rank=None).model_dump(exclude_unset=True)
    # Constructed with explicit None — field is set.
    assert "local_rank" in cleared
    assert cleared["local_rank"] is None
