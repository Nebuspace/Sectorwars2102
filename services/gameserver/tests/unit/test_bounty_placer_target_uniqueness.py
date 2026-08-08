"""ADR-0055 S-V3 / bounty-and-reputation.md "Bounty uniqueness": one active
bounty per (placer, target) pair. A single placer cannot stack a second
active bounty on the same target while their first is still outstanding;
DISTINCT placers each having their own active bounty on the same target is
intentional (ADR-0054 X-V2 stacking-pressure design) and must NOT be
blocked. "Active" == "still present in the target's JSONB bounty list" --
cancel_bounty/collect_bounty/expire_due_bounties all REMOVE an entry the
moment it resolves, so a placer whose prior bounty was collected/cancelled/
expired can place a new one.

Same DB-free fake-session convention as test_bounty_dual_lock_order.py /
test_bounty_service_nh2.py.
"""
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services import bounty_service as bs


@pytest.fixture(autouse=True)
def _noop_flag_modified(monkeypatch):
    monkeypatch.setattr(bs, "flag_modified", lambda *a, **k: None)


def make_player(*, player_id=None, credits=0, nickname="p", bounties=None):
    settings = {}
    if bounties is not None:
        settings["bounties"] = bounties
    return SimpleNamespace(
        id=player_id or uuid4(),
        credits=credits,
        nickname=nickname,
        personal_reputation=0,
        reputation_tier="Neutral",
        name_color="#FFFFFF",
        settings=settings,
    )


class _FakeQuery:
    def __init__(self, players, lock_log):
        self._players = players
        self._lock_log = lock_log
        self._match_id = None

    def filter(self, cond):
        rhs = getattr(cond, "right", None)
        val = getattr(rhs, "value", None)
        self._match_id = val
        return self

    def with_for_update(self, *a, **k):
        if self._match_id is not None:
            self._lock_log.append(self._match_id)
        return self

    def populate_existing(self):
        return self

    def first(self):
        return self._players.get(self._match_id)


class _FakeSession:
    def __init__(self, *players):
        self._players = {p.id: p for p in players}
        self.player_lock_log: list = []
        self.flushed = False
        self.added: list = []

    def query(self, model):
        return _FakeQuery(self._players, self.player_lock_log)

    def flush(self):
        self.flushed = True

    def add(self, obj):
        self.added.append(obj)

    @contextmanager
    def begin_nested(self):
        yield


class TestPlacerTargetUniqueness:
    def test_same_placer_second_bounty_on_same_target_rejected(self) -> None:
        placer = make_player(credits=10_000)
        target = make_player(bounties=[
            {"id": "b1", "placed_by": str(placer.id), "amount": 1000, "type": "player"},
        ])
        db = _FakeSession(placer, target)
        service = bs.BountyService(db)

        result = service.place_bounty(placer.id, target.id, 1500)

        assert result["success"] is False
        assert "already have an active bounty" in result["message"]
        # No state mutation on rejection -- fee/credits untouched, bounty
        # list untouched.
        assert placer.credits == 10_000
        assert target.settings["bounties"] == [
            {"id": "b1", "placed_by": str(placer.id), "amount": 1000, "type": "player"},
        ]

    def test_distinct_placer_still_succeeds_stacking_preserved(self) -> None:
        first_placer_id = uuid4()
        second_placer = make_player(credits=10_000)
        target = make_player(bounties=[
            {"id": "b1", "placed_by": str(first_placer_id), "amount": 1000, "type": "player"},
        ])
        db = _FakeSession(second_placer, target)
        service = bs.BountyService(db)

        result = service.place_bounty(second_placer.id, target.id, 1500)

        assert result["success"] is True
        bounties = target.settings["bounties"]
        assert len(bounties) == 2
        assert {b["placed_by"] for b in bounties} == {
            str(first_placer_id), str(second_placer.id),
        }

    def test_placer_can_place_again_after_prior_bounty_collected(self) -> None:
        """collect_bounty REMOVES the entry -- uniqueness is per-ACTIVE
        bounty, not lifetime; simulate the post-collection state directly
        (empty bounty list) and confirm a fresh place succeeds."""
        placer = make_player(credits=10_000)
        target = make_player(bounties=[])  # collect_bounty already cleared it
        db = _FakeSession(placer, target)
        service = bs.BountyService(db)

        result = service.place_bounty(placer.id, target.id, 1500)

        assert result["success"] is True
        assert len(target.settings["bounties"]) == 1

    def test_placer_can_place_again_after_own_cancel(self) -> None:
        placer = make_player(credits=10_000, bounties=None)
        target = make_player(bounties=[
            {"id": "b1", "placed_by": str(placer.id), "amount": 1000, "type": "player"},
        ])
        db = _FakeSession(placer, target)
        service = bs.BountyService(db)

        cancel_result = service.cancel_bounty(placer.id, "b1", target.id)
        assert cancel_result["success"] is True
        assert target.settings["bounties"] == []

        place_result = service.place_bounty(placer.id, target.id, 1500)
        assert place_result["success"] is True
        assert len(target.settings["bounties"]) == 1

    def test_placer_can_place_again_after_expiry_sweep_removes_it(self) -> None:
        """expire_due_bounties (proven separately in its own full-scan sweep
        suite -- this minimal fake session has no .all()/boolean-literal
        query support to drive that method directly) REMOVES a due entry
        from the target's list exactly like cancel/collect. Simulate that
        documented removal effect directly and confirm a fresh place then
        succeeds -- the same "presence in the list == active" invariant
        this guard relies on."""
        placer = make_player(credits=10_000)
        target = make_player(bounties=[])  # post-expiry-sweep state

        db = _FakeSession(placer, target)
        service = bs.BountyService(db)

        place_result = service.place_bounty(placer.id, target.id, 1500)
        assert place_result["success"] is True
