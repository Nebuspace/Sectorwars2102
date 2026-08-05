"""Unit tests for diplomatic.first_citizen auto-award (80dbf6ae Accept gap).

Pure / mocked — no DB fixture required. Locks:
  * catalog trigger ownership (governance_votes → first_citizen only)
  * dispatcher signature + export
  * vote-count aggregation (election + policy) feeding _evaluate_and_award
  * defensive no-raise on query failure
"""

from __future__ import annotations

import inspect
import uuid
from unittest.mock import MagicMock, patch

import pytest

from src.services.medal_catalog import MEDAL_CATALOG, medals_for_trigger
from src.services import medal_service


def test_governance_votes_owns_exactly_first_citizen():
    matches = medals_for_trigger("governance_votes")
    assert [m["id"] for m in matches] == ["diplomatic.first_citizen"]
    entry = MEDAL_CATALOG["diplomatic.first_citizen"]
    assert entry["criteria"]["type"] == "governance_votes"
    assert entry["criteria"]["threshold"] == 1


def test_first_citizen_dispatcher_exported_with_expected_signature():
    fn = getattr(medal_service, "check_and_award_first_citizen_medal", None)
    assert fn is not None
    assert "check_and_award_first_citizen_medal" in medal_service.__all__
    assert list(inspect.signature(fn).parameters) == ["db", "player_id"]


def test_first_citizen_sums_election_and_policy_votes():
    """Election count 0 + policy count 1 (or vice versa) still crosses threshold 1."""
    player_id = uuid.uuid4()
    db = MagicMock()

    # Two sequential query(...).filter(...).count() calls.
    election_q = MagicMock()
    election_q.filter.return_value.count.return_value = 0
    policy_q = MagicMock()
    policy_q.filter.return_value.count.return_value = 1
    db.query.side_effect = [election_q, policy_q]

    with patch.object(
        medal_service, "_evaluate_and_award", return_value=["diplomatic.first_citizen"]
    ) as evaluate:
        awarded = medal_service.check_and_award_first_citizen_medal(db, player_id)

    assert awarded == ["diplomatic.first_citizen"]
    evaluate.assert_called_once()
    args, kwargs = evaluate.call_args
    assert args[0] is db
    assert args[1] == player_id
    assert args[2] == "governance_votes"
    assert args[3] == 1  # 0 + 1
    assert kwargs["source_event_key"] == "governance.vote_cast"
    assert kwargs["awarded_via"] == "system"


def test_first_citizen_dispatcher_swallows_query_errors():
    db = MagicMock()
    db.query.side_effect = RuntimeError("db down")
    # Must not raise — mirrors other check_and_award_* defensive contracts.
    assert medal_service.check_and_award_first_citizen_medal(db, uuid.uuid4()) == []


def test_governance_dispatch_helper_resolves_hook():
    """regional_governance_service._dispatch_first_citizen_medal is getattr-safe."""
    from src.services.regional_governance_service import _dispatch_first_citizen_medal

    sync_db = MagicMock()
    player_id = uuid.uuid4()
    with patch.object(
        medal_service,
        "check_and_award_first_citizen_medal",
        return_value=["diplomatic.first_citizen"],
    ) as hook:
        _dispatch_first_citizen_medal(sync_db, player_id)
        hook.assert_called_once_with(sync_db, player_id)
