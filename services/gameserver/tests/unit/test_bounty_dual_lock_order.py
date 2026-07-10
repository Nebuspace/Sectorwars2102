"""WO-ECON-BOUNTY-DUAL-LOCK-ORDER — cancel_bounty / collect_bounty acquire
their Player pair in CONSISTENT ascending-id order (via the shared
``_load_two_players_for_update`` helper), not an unconditional
placer/collector-then-target order. Pure lock-ORDER fix — no credit/
refund amount or business-logic changes anywhere; test_bounty_service_
nh2.py's own suite (unchanged, still green) already proves the payout
math is untouched.

Same DB-free fake-session convention as test_bounty_service_nh2.py,
extended with a ``player_lock_log`` list (mirrors the WO-ECON-CONTRACT-
MONEY-HARDEN sibling fix's ``_RacySession.player_lock_log``
instrumentation in contract_service's own test suite) so the ACQUISITION
ORDER can be asserted directly rather than only inferred from behavior.

Deadlock-freedom under real concurrent Postgres transactions cannot be
demonstrated by a single-threaded fake — that is the orchestrator's live-
Postgres leg (same framing WO-ECON-CONTRACT-MONEY-HARDEN used for the
sibling contract_service fix). What IS fully provable here is the
ORDERING property itself: the necessary precondition for that guarantee.
"""
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services import bounty_service as bs


@pytest.fixture(autouse=True)
def _noop_flag_modified(monkeypatch):
    """Mirrors test_bounty_service_nh2.py's own fixture — the SimpleNamespace
    player stand-ins aren't SQLAlchemy-mapped, so the real flag_modified
    raises; the JSONB-dirty-flag is irrelevant to lock-order behavior."""
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
    """Routes a ``Player.id == <literal>`` filter to the matching seeded
    row (mirrors test_bounty_service_nh2.py's own _FakeQuery exactly) and
    additionally records the id ``.with_for_update()`` was called with, in
    call order — the instrumentation this WO's proof needs."""

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

    def first(self):
        return self._players.get(self._match_id)


class _FakeSession:
    """Keyed-by-id Player store; records lock order, added claims, flush —
    mirrors test_bounty_service_nh2.py's _FakeSession, extended with
    player_lock_log."""

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


# --------------------------------------------------------------------------- #
# cancel_bounty
# --------------------------------------------------------------------------- #

class TestCancelBountyLockOrder:
    def test_locks_ascending_when_placer_has_the_lower_id(self) -> None:
        low_id, high_id = sorted([uuid4(), uuid4()])
        placer = make_player(player_id=low_id, credits=10_000)
        target = make_player(player_id=high_id, bounties=[
            {"id": "b1", "placed_by": str(low_id), "amount": 5000, "type": "player"},
        ])
        db = _FakeSession(placer, target)
        service = bs.BountyService(db)

        result = service.cancel_bounty(placer.id, "b1", target.id)

        assert result["success"] is True
        assert db.player_lock_log == [low_id, high_id]

    def test_locks_ascending_when_placer_has_the_higher_id(self) -> None:
        """The role-reversal that would deadlock against the case above if
        locking simply went "placer first" unconditionally — a concurrent
        cancel_bounty on a DIFFERENT contract sharing this same pair, with
        placer/target swapped, would otherwise lock high-then-low."""
        low_id, high_id = sorted([uuid4(), uuid4()])
        placer = make_player(player_id=high_id, credits=10_000)
        target = make_player(player_id=low_id, bounties=[
            {"id": "b1", "placed_by": str(high_id), "amount": 5000, "type": "player"},
        ])
        db = _FakeSession(placer, target)
        service = bs.BountyService(db)

        result = service.cancel_bounty(placer.id, "b1", target.id)

        assert result["success"] is True
        assert db.player_lock_log == [low_id, high_id]


# --------------------------------------------------------------------------- #
# collect_bounty
# --------------------------------------------------------------------------- #

class TestCollectBountyLockOrder:
    def test_locks_ascending_when_collector_has_the_lower_id(self) -> None:
        low_id, high_id = sorted([uuid4(), uuid4()])
        collector = make_player(player_id=low_id, credits=0)
        target = make_player(player_id=high_id, bounties=[
            {"id": "b1", "placed_by": str(uuid4()), "amount": 5000, "type": "player"},
        ])
        db = _FakeSession(collector, target)
        service = bs.BountyService(db)

        result = service.collect_bounty(collector.id, target.id)

        assert result["success"] is True
        assert db.player_lock_log == [low_id, high_id]

    def test_locks_ascending_when_collector_has_the_higher_id(self) -> None:
        low_id, high_id = sorted([uuid4(), uuid4()])
        collector = make_player(player_id=high_id, credits=0)
        target = make_player(player_id=low_id, bounties=[
            {"id": "b1", "placed_by": str(uuid4()), "amount": 5000, "type": "player"},
        ])
        db = _FakeSession(collector, target)
        service = bs.BountyService(db)

        result = service.collect_bounty(collector.id, target.id)

        assert result["success"] is True
        assert db.player_lock_log == [low_id, high_id]


# --------------------------------------------------------------------------- #
# The deadlock-specific proof: BOTH functions agree on order for the SAME
# pair, regardless of which one is called or which role each player plays.
# --------------------------------------------------------------------------- #

class TestCrossFunctionOrderingConsistency:
    """A same-pair cancel_bounty racing a same-pair collect_bounty (or two
    calls to either with roles reversed) is exactly the scenario that could
    deadlock if the two functions used different lock orderings. Proves
    they don't — both independently converge on the same ascending order
    for the same two ids."""

    def test_cancel_and_collect_agree_on_order_for_the_same_pair(self) -> None:
        low_id, high_id = sorted([uuid4(), uuid4()])

        # cancel_bounty: placer has the HIGH id, target has the LOW id.
        placer = make_player(player_id=high_id, credits=10_000)
        cancel_target = make_player(player_id=low_id, bounties=[
            {"id": "b1", "placed_by": str(high_id), "amount": 5000, "type": "player"},
        ])
        cancel_db = _FakeSession(placer, cancel_target)
        bs.BountyService(cancel_db).cancel_bounty(placer.id, "b1", cancel_target.id)

        # collect_bounty: collector has the LOW id, target has the HIGH id —
        # the SAME two ids as above, roles/call-argument-order reversed.
        collector = make_player(player_id=low_id, credits=0)
        collect_target = make_player(player_id=high_id, bounties=[
            {"id": "b2", "placed_by": str(uuid4()), "amount": 3000, "type": "player"},
        ])
        collect_db = _FakeSession(collector, collect_target)
        bs.BountyService(collect_db).collect_bounty(collector.id, collect_target.id)

        # Both converge on the IDENTICAL ascending order for the identical
        # pair of ids — the structural property that makes the two
        # functions mutually deadlock-safe, not just internally consistent.
        assert cancel_db.player_lock_log == [low_id, high_id]
        assert collect_db.player_lock_log == [low_id, high_id]


# --------------------------------------------------------------------------- #
# Defensive: id_a == id_b never double-locks
# --------------------------------------------------------------------------- #

class TestSelfPairDefensiveGuard:
    def test_cancel_bounty_same_id_locks_exactly_once(self) -> None:
        pid = uuid4()
        player = make_player(player_id=pid, credits=10_000, bounties=[
            {"id": "b1", "placed_by": str(pid), "amount": 5000, "type": "player"},
        ])
        db = _FakeSession(player)
        service = bs.BountyService(db)

        service.cancel_bounty(pid, "b1", pid)

        assert db.player_lock_log == [pid]

    def test_collect_bounty_same_id_locks_exactly_once(self) -> None:
        pid = uuid4()
        player = make_player(player_id=pid, credits=0, bounties=[
            {"id": "b1", "placed_by": str(uuid4()), "amount": 5000, "type": "player"},
        ])
        db = _FakeSession(player)
        service = bs.BountyService(db)

        service.collect_bounty(pid, pid)

        assert db.player_lock_log == [pid]


# --------------------------------------------------------------------------- #
# Payout math genuinely untouched (spot-check — the full proof is
# test_bounty_service_nh2.py's own unchanged, still-green suite)
# --------------------------------------------------------------------------- #

class TestPayoutMathUnaffectedByReordering:
    def test_cancel_bounty_refund_unaffected_by_lock_order(self) -> None:
        low_id, high_id = sorted([uuid4(), uuid4()])
        placer = make_player(player_id=high_id, credits=1000)
        target = make_player(player_id=low_id, bounties=[
            {"id": "b1", "placed_by": str(high_id), "amount": 5000, "type": "player"},
        ])
        db = _FakeSession(placer, target)
        service = bs.BountyService(db)

        result = service.cancel_bounty(placer.id, "b1", target.id)

        assert result["refund"] == 5000
        assert placer.credits == 1000 + 5000

    def test_collect_bounty_payout_unaffected_by_lock_order(self) -> None:
        low_id, high_id = sorted([uuid4(), uuid4()])
        collector = make_player(player_id=high_id, credits=0)
        target = make_player(player_id=low_id, bounties=[
            {"id": "b1", "placed_by": str(uuid4()), "amount": 5000, "type": "player"},
        ])
        db = _FakeSession(collector, target)
        service = bs.BountyService(db)

        result = service.collect_bounty(collector.id, target.id)

        assert result["total_collected"] == 5000
        assert collector.credits == 5000
