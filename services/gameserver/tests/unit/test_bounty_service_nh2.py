"""Unit tests for WO-INTEGRITY-PAIR NH2 — bounty-collusion faucet close-the-loop.

Before this fix, ``BountyService.collect_bounty`` / ``collect_bounty_share``
paid + zeroed the SYSTEM bounty pot but never touched the TARGET's own
reputation. A criminal pinned at a deep-negative score (e.g. two colluding
players, one always the "wanted" accomplice) stayed ``is_criminal() == True``
forever, so the npc_scheduler accrual sweep kept re-filling their pot on
schedule after every collection — a slow-but-permanent faucet requiring zero
further "crime" after the initial rep tank.

These tests exercise the exploit + fix path directly against the real service
logic. No real DB: a tiny in-memory fake Session routes ``query(Player)``
lookups by id (mirroring the pattern in test_research_service.py), and
``flag_modified`` is no-op'd since the player stand-ins are plain
SimpleNamespace objects, not SQLAlchemy-mapped instances.
"""
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services import bounty_service as bs
from src.services import personal_reputation_service as prs


@pytest.fixture(autouse=True)
def _noop_flag_modified(monkeypatch):
    """The SimpleNamespace player stand-ins below aren't SQLAlchemy-mapped, so
    the real ``flag_modified`` raises. Matches the pattern already established
    in test_research_service.py — the JSONB-dirty-flag is irrelevant to the
    logic under test (the code also reassigns the attribute directly)."""
    monkeypatch.setattr(bs, "flag_modified", lambda *a, **k: None)


def make_player(rep=-1000, credits=0, nickname="p", bounties=None, pot=0, pot_period=None):
    settings = {}
    if bounties is not None:
        settings["bounties"] = bounties
    if pot:
        settings[bs.SYSTEM_BOUNTY_POT_KEY] = pot
    if pot_period is not None:
        settings[bs.SYSTEM_BOUNTY_POT_PERIOD_KEY] = pot_period
    return SimpleNamespace(
        id=uuid4(),
        credits=credits,
        nickname=nickname,
        personal_reputation=rep,
        reputation_tier="Villain",
        name_color="#FF0000",
        settings=settings,
    )


class _FakeQuery:
    """Routes a ``Player.id == <literal>`` filter to the matching seeded row.

    Ignores the queried model (only Player is ever queried by bounty_service /
    personal_reputation_service) and any ordering/locking calls — ``.filter``,
    ``.with_for_update`` are captured/no-op'd exactly like the real API shape.
    """
    def __init__(self, players):
        self._players = players
        self._match_id = None

    def filter(self, cond):
        rhs = getattr(cond, "right", None)
        val = getattr(rhs, "value", None)
        self._match_id = val
        return self

    def with_for_update(self, *a, **k):
        return self

    def populate_existing(self):
        # WO-BOUNTY-COLLECT-FLUSH: _load_two_players_for_update's lock
        # queries now chain .populate_existing().with_for_update() (mirrors
        # test_bounty_dual_lock_order.py's own _FakeQuery fix for the same
        # chained-call shape, added there for place_bounty under WO-MONEY-
        # NOLOCK-RMW). This fake has no identity map to refresh — pure
        # passthrough so the chained call doesn't AttributeError.
        return self

    def first(self):
        return self._players.get(self._match_id)


class _FakeSession:
    """Keyed-by-id Player store; records flush/added claims; no-op transactions."""

    def __init__(self, *players):
        self._players = {p.id: p for p in players}
        self.flushed = False
        self.added = []

    def query(self, model):
        return _FakeQuery(self._players)

    def flush(self):
        self.flushed = True

    def add(self, obj):
        self.added.append(obj)

    @contextmanager
    def begin_nested(self):
        yield


# --------------------------------------------------------------------------- #
# Verify-first: is_criminal / accrual gating still key off the -500 threshold
# --------------------------------------------------------------------------- #

def test_verify_first_deep_negative_rep_is_criminal_and_accrues():
    """Confirms the exploit's precondition still holds pre-fix: a colluding
    accomplice sitting at a deep-negative score is a perpetual criminal and
    the pot keeps accruing period over period with nothing else changing."""
    target = make_player(rep=-1000)
    assert bs.BountyService.is_criminal(target) is True
    added_day1 = bs.BountyService.accrue_system_bounty_pot(target, period=1)
    assert added_day1 > 0
    added_day2 = bs.BountyService.accrue_system_bounty_pot(target, period=2)
    assert added_day2 > 0
    # Still a criminal after two days of nothing but sitting at -1000 —
    # confirms accrual alone never rehabilitates the target.
    assert bs.BountyService.is_criminal(target) is True


# --------------------------------------------------------------------------- #
# The fix: system-bounty collection rehabilitates the target
# --------------------------------------------------------------------------- #

def test_collect_bounty_system_payout_closes_the_loop():
    """Exploit repro: collusion pair, target pinned at -1000 (Villain, deepest
    tier), pot accrued and collected once. Post-fix: the target's rep is
    raised to just above the criminal threshold, so a subsequent accrual
    sweep call adds NOTHING — the faucet is closed until the target commits a
    fresh crime to push back below -500."""
    target = make_player(rep=-1000)
    collector = make_player(rep=0, credits=0)
    db = _FakeSession(target, collector)
    service = bs.BountyService(db)

    # Simulate the accrual sweep having grown the pot before the kill.
    bs.BountyService.accrue_system_bounty_pot(target, period=1)
    assert bs.BountyService.get_system_bounty_pot(target) > 0

    result = service.collect_bounty(collector.id, target.id)
    assert result["success"] is True
    assert result["system_bounties_collected"] > 0
    assert collector.credits == result["total_collected"]

    # The pot is drained (pre-existing anti-double-collect behavior).
    assert bs.BountyService.get_system_bounty_pot(target) == 0
    # NH2 fix: target rep raised to just clear of the criminal threshold.
    assert target.personal_reputation == bs.SYSTEM_BOUNTY_CRIMINAL_THRESHOLD + 1
    assert bs.BountyService.is_criminal(target) is False

    # The exploit path now fails: a same-day-or-later re-run of the accrual
    # sweep adds NOTHING, because the target is no longer a criminal.
    added_after = bs.BountyService.accrue_system_bounty_pot(target, period=2)
    assert added_after == 0
    assert bs.BountyService.get_system_bounty_pot(target) == 0


def test_collect_bounty_rehab_is_monotonic_never_lowers_rep():
    """Defensive: if the target is somehow already clear of the threshold at
    collection time, the restore is a strict no-op (never lowers rep, never
    double-applies)."""
    target = make_player(rep=200)  # already well clear of -500
    starting_rep = target.personal_reputation
    db = _FakeSession(target)
    service = bs.BountyService(db)
    service._restore_target_rep_after_system_payout(target)
    assert target.personal_reputation == starting_rep


def test_collect_bounty_share_fleet_path_also_closes_the_loop():
    """The fleet-kill path (collect_bounty_share) reaches the same pot-zero
    codepath via the designated last member — confirm it rehabilitates the
    target exactly like the solo path."""
    target = make_player(rep=-1000)
    hunters = [make_player(rep=0, credits=0) for _ in range(3)]
    db = _FakeSession(target, *hunters)
    service = bs.BountyService(db)

    bs.BountyService.accrue_system_bounty_pot(target, period=1)
    assert bs.BountyService.get_system_bounty_pot(target) > 0

    n = len(hunters)
    for idx, hunter in enumerate(hunters):
        service.collect_bounty_share(
            hunter_id=hunter.id,
            target_id=target.id,
            num_participants=n,
            claim_player_pot=(idx == n - 1),  # designated member goes last
        )

    assert bs.BountyService.get_system_bounty_pot(target) == 0
    assert target.personal_reputation == bs.SYSTEM_BOUNTY_CRIMINAL_THRESHOLD + 1
    assert bs.BountyService.is_criminal(target) is False


# --------------------------------------------------------------------------- #
# Legit path unchanged: player-placed-only collection never touches rep
# --------------------------------------------------------------------------- #

def test_collect_bounty_player_placed_only_does_not_touch_target_rep():
    """A target with ONLY a player-placed bounty (no system pot — e.g. a
    Neutral-rep target with a price on their head from a rival) is unaffected
    by the NH2 fix: their reputation is untouched, exactly as before."""
    target = make_player(rep=0, bounties=[{
        "id": "b1", "placed_by": str(uuid4()), "amount": 5000, "type": "player",
    }])
    collector = make_player(rep=0, credits=0)
    db = _FakeSession(target, collector)
    service = bs.BountyService(db)

    result = service.collect_bounty(collector.id, target.id)
    assert result["success"] is True
    assert result["player_bounties_collected"] == 5000
    assert result["system_bounties_collected"] == 0
    assert collector.credits == 5000
    assert target.personal_reputation == 0  # untouched — no system payout fired
