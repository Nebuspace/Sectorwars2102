"""WO-FLEET-KILL-LOCK-ORDER — ``_distribute_fleet_kill_rewards`` acquires the
killed (target) player's row AND every distinct participant's row in ONE
ascending-id batch (via the new ``_lock_players_ascending`` helper), not the
prior shape (killed_player locked first, then each hunter locked one-by-one
inside ``collect_bounty_share`` in ``killer_fleet.members`` iteration order —
NOT ascending-id). The prior shape could deadlock: two concurrent fleet kills
sharing a pair of players with the killed/participant roles REVERSED could
lock that pair in opposite orders (classic AB-BA). Pure lock-ORDER fix — the
reward-loop's business logic (even-split payout, designated-last pot claim)
is untouched, which is why ``collect_bounty_share`` is mocked out here rather
than re-faked: this suite proves ACQUISITION order only.

Same DB-free hand-rolled fake-session convention as
test_bounty_dual_lock_order.py (itself an extension of
test_bounty_service_nh2.py's fake), extended to record BOTH the
``with_for_update()`` acquisitions AND ``flush()`` calls in one ordered
``events`` list so the flush-before-batch precondition (WO-BOUNTY-COLLECT-
FLUSH's fleet twin) can be asserted directly, not just the lock order.

Deadlock-freedom under real concurrent Postgres transactions cannot be
demonstrated by a single-threaded fake — that is the orchestrator's live-
Postgres leg. What IS fully provable here is the ORDERING property itself:
the necessary precondition for that guarantee.
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services import bounty_service as bounty_service_module
from src.services import fleet_service as fs


@pytest.fixture(autouse=True)
def _fake_collect_bounty_share(monkeypatch):
    """Mock BountyService.collect_bounty_share (an ALREADY-SHIPPED, separately
    tested calculator — see test_bounty_dual_lock_order.py / test_bounty_
    service_nh2.py) rather than re-faking its JSONB bounty-pot internals
    here. Always reports had_bounty=True + paid=0 so the reward loop takes
    its cheapest path (skips both the +100 rep award and the whole innocent-
    penalty / grey-flag branch) — this suite is about LOCK ORDER, not the
    payout math those other suites already cover."""
    def _fake(self, hunter_id, target_id, num_participants, claim_player_pot):
        return {
            "success": True,
            "had_bounty": True,
            "paid": 0,
            "system_paid": 0,
            "player_paid": 0,
        }
    monkeypatch.setattr(
        bounty_service_module.BountyService, "collect_bounty_share", _fake
    )


def make_player(*, player_id=None):
    return SimpleNamespace(
        id=player_id or uuid4(),
        credits=0,
        nickname="p",
        personal_reputation=0,
        reputation_tier="Neutral",
        name_color="#FFFFFF",
        settings={},
    )


def make_ship(owner_id):
    return SimpleNamespace(owner_id=owner_id)


def make_fleet(member_player_ids):
    return SimpleNamespace(
        members=[SimpleNamespace(player_id=pid) for pid in member_player_ids]
    )


class _FakeQuery:
    """Routes a ``Player.id == <literal>`` filter to the matching seeded row
    and appends a ``("lock", id)`` event, in call order, to the SAME ordered
    ``events`` list the owning ``_FakeSession`` uses for ``flush()`` — so
    lock-vs-flush ordering is directly observable, not just lock-vs-lock."""

    def __init__(self, players, events):
        self._players = players
        self._events = events
        self._match_id = None

    def filter(self, cond):
        rhs = getattr(cond, "right", None)
        val = getattr(rhs, "value", None)
        self._match_id = val
        return self

    def populate_existing(self):
        return self

    def with_for_update(self, *a, **k):
        if self._match_id is not None:
            self._events.append(("lock", self._match_id))
        return self

    def first(self):
        return self._players.get(self._match_id)


class _FakeSession:
    """Keyed-by-id Player store; records lock acquisitions AND flush calls,
    interleaved in one ordered ``events`` list."""

    def __init__(self, *players):
        self._players = {p.id: p for p in players}
        self.events: list = []
        self.added: list = []

    @property
    def player_lock_log(self):
        return [e[1] for e in self.events if e[0] == "lock"]

    def query(self, model):
        return _FakeQuery(self._players, self.events)

    def flush(self):
        self.events.append(("flush",))

    def add(self, obj):
        self.added.append(obj)


def _assert_no_swallowed_error(monkeypatch):
    """``_distribute_fleet_kill_rewards`` catches every exception broadly
    (best-effort: a reward hiccup must never break battle resolution) — a
    bug INSIDE the method would otherwise silently produce a truncated
    ``player_lock_log`` instead of a visible test failure. Spy on
    ``logger.error`` so any swallowed exception still fails the test loudly."""
    calls = []
    monkeypatch.setattr(fs.logger, "error", lambda *a, **k: calls.append((a, k)))
    return calls


# --------------------------------------------------------------------------- #
# 1. Role-reversal pair — the deadlock-specific proof.
# --------------------------------------------------------------------------- #

class TestRoleReversalConverges:
    def test_killed_high_participant_low(self, monkeypatch) -> None:
        low_id, high_id = sorted([uuid4(), uuid4()])
        errors = _assert_no_swallowed_error(monkeypatch)

        killed = make_player(player_id=high_id)
        participant = make_player(player_id=low_id)
        db = _FakeSession(killed, participant)
        service = fs.FleetService(db)

        service._distribute_fleet_kill_rewards(
            make_ship(owner_id=high_id), make_fleet([low_id])
        )

        assert not errors
        assert db.player_lock_log == [low_id, high_id]

    def test_killed_low_participant_high_same_pair_reversed_roles(
        self, monkeypatch
    ) -> None:
        """The role-reversal that would deadlock against the case above if
        locking simply went "killed-target-first, then participants" — a
        concurrent fleet kill on the SAME pair of players, with killed/
        participant roles swapped, would otherwise lock high-then-low."""
        low_id, high_id = sorted([uuid4(), uuid4()])
        errors = _assert_no_swallowed_error(monkeypatch)

        killed = make_player(player_id=low_id)
        participant = make_player(player_id=high_id)
        db = _FakeSession(killed, participant)
        service = fs.FleetService(db)

        service._distribute_fleet_kill_rewards(
            make_ship(owner_id=low_id), make_fleet([high_id])
        )

        assert not errors
        # Converges on the IDENTICAL ascending order as the reversed-role
        # case above — the structural property that makes concurrent fleet
        # kills on a shared pair mutually deadlock-safe.
        assert db.player_lock_log == [low_id, high_id]


# --------------------------------------------------------------------------- #
# 2. N=4, killed player NOT first/last in sorted order — catches a naive
#    "killed-first-then-ascending-participants" implementation, which would
#    NOT put killed_player_id at its true ascending POSITION.
# --------------------------------------------------------------------------- #

class TestKilledPlayerLandsAtItsAscendingPosition:
    def test_four_players_killed_in_the_middle_members_scrambled(
        self, monkeypatch
    ) -> None:
        a, b, c, d = sorted(uuid4() for _ in range(4))
        errors = _assert_no_swallowed_error(monkeypatch)

        players = [make_player(player_id=pid) for pid in (a, b, c, d)]
        db = _FakeSession(*players)
        service = fs.FleetService(db)

        # killed_player_id = c (neither first nor last of the sorted set);
        # killer_fleet.members deliberately scrambled: [d, a, b].
        service._distribute_fleet_kill_rewards(
            make_ship(owner_id=c), make_fleet([d, a, b])
        )

        assert not errors
        # Exactly 4 lock acquisitions total (collect_bounty_share is mocked
        # out, so no further Player locks happen past the upfront batch).
        assert db.player_lock_log == [a, b, c, d]


# --------------------------------------------------------------------------- #
# 3. Flush precedes the whole lock batch (WO-BOUNTY-COLLECT-FLUSH, fleet
#    twin) — the flush must land before the FIRST lock acquisition, not
#    interleaved partway through the batch.
# --------------------------------------------------------------------------- #

class TestFlushPrecedesTheBatch:
    def test_flush_fires_before_first_lock_acquisition(self, monkeypatch) -> None:
        low_id, high_id = sorted([uuid4(), uuid4()])
        errors = _assert_no_swallowed_error(monkeypatch)

        killed = make_player(player_id=high_id)
        participant = make_player(player_id=low_id)
        db = _FakeSession(killed, participant)
        service = fs.FleetService(db)

        service._distribute_fleet_kill_rewards(
            make_ship(owner_id=high_id), make_fleet([low_id])
        )

        assert not errors
        assert db.events[0] == ("flush",)
        flush_index = db.events.index(("flush",))
        first_lock_index = next(
            i for i, e in enumerate(db.events) if e[0] == "lock"
        )
        assert flush_index < first_lock_index


# --------------------------------------------------------------------------- #
# 4. Helper unit coverage — ``_lock_players_ascending`` in isolation.
# --------------------------------------------------------------------------- #

class TestLockPlayersAscendingHelper:
    def test_locks_in_ascending_order_regardless_of_input_order(self) -> None:
        a, b, c = sorted(uuid4() for _ in range(3))
        players = [make_player(player_id=pid) for pid in (a, b, c)]
        db = _FakeSession(*players)
        service = fs.FleetService(db)

        locked = service._lock_players_ascending({c, a, b})

        assert db.player_lock_log == [a, b, c]
        assert set(locked.keys()) == {a, b, c}

    def test_missing_player_id_is_skipped_not_kept_as_none(self) -> None:
        pid = uuid4()
        missing_id = uuid4()
        db = _FakeSession(make_player(player_id=pid))
        service = fs.FleetService(db)

        locked = service._lock_players_ascending({pid, missing_id})

        assert pid in locked
        assert missing_id not in locked
