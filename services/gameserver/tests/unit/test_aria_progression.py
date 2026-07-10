"""Unit coverage for the canonical consciousness + relationship promotion
helper (WO-ARIA-PROGRESSION): ``update_consciousness_and_relationship_sync``
and the pure core it shares with the async
``update_consciousness_and_relationship`` -- both now the SINGLE source of
truth for the four call sites (movement_service.py, combat_service.py,
trading.py buy + sell) that used to each carry their own copy-pasted,
interactions-ONLY (no memory-diversity gate, no relationship increment at
all) inline threshold block.

No live DB is used. Per the codebase's mock-only unit-test convention (see
test_aria_observation_log.py's FakeObsSession), ``FakeProgressionSession``
interprets the REAL SQLAlchemy query()/filter()/first()/scalar() clauses the
SUT builds against a live, mutable in-memory row store -- built from REAL
``Player`` / ``ARIAPersonalMemory`` ORM instances.

Canon: sw2102-docs/FEATURES/gameplay/aria-companion.md:118-128 (both
thresholds required to advance) and :139-144 (relationship +1 per
significant interaction, capped 100). The threshold NUMBERS (10/30/75/150
memories, 50/150/400/1000 interactions, 1.1/1.2/1.35/1.5 multipliers) are
UNCHANGED from before this WO -- only pinned here, never asserted-different.

FLAGGED (not resolved) design note this file also documents: canon's memory
gate is "unique-type memory diversity", but only THREE ARIAPersonalMemory.
memory_type values are ever written anywhere in this codebase (combat /
market / exploration -- "social" is named in a docstring, never
instantiated). A literal distinct-type-count implementation would make
EVERY tier above Dormant (even level 2's threshold of 10) permanently
unreachable. The SUT therefore uses raw total memory count -- see
TestMemoryDiversityInterpretation for the concrete falsifying proof.
"""
from __future__ import annotations

import inspect
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy.sql import operators

from src.models.aria_personal_intelligence import ARIAPersonalMemory
from src.models.player import Player
from src.services.aria_personal_intelligence_service import ARIAPersonalIntelligenceService

# --------------------------------------------------------------------------- #
# In-memory fake session -- interprets the SUT's real SQLAlchemy clauses
# --------------------------------------------------------------------------- #

def _condition_matches(row, condition) -> bool:
    left, op = condition.left, condition.operator
    actual = getattr(row, left.key)
    if op is operators.eq:
        return actual == condition.right.value
    raise AssertionError(f"unhandled operator {op!r} on column {left.key!r}")


class _FakePlayerQuery:
    def __init__(self, store, session):
        self._store = store
        self._session = session
        self._conditions: tuple = ()

    def filter(self, *conditions):
        self._conditions = self._conditions + conditions
        return self

    def first(self):
        self._session.queries += 1
        matching = [r for r in self._store if all(_condition_matches(r, c) for c in self._conditions)]
        return matching[0] if matching else None


class _FakeMemoryCountQuery:
    """db.query(func.count(ARIAPersonalMemory.id)).filter(...).scalar()."""

    def __init__(self, store, session):
        self._store = store
        self._session = session
        self._conditions: tuple = ()

    def filter(self, *conditions):
        self._conditions = self._conditions + conditions
        return self

    def scalar(self):
        self._session.queries += 1
        matching = [r for r in self._store if all(_condition_matches(r, c) for c in self._conditions)]
        return len(matching)


class FakeProgressionSession:
    """Minimal sync db double: db.query(Player) -> whole-row filter+first;
    anything else (func.count(ARIAPersonalMemory.id)) -> the memory-count
    query -- the only two shapes update_consciousness_and_relationship_sync
    uses."""

    def __init__(self, players=(), memories=()):
        self.players = list(players)
        self.memories = list(memories)
        self.queries = 0

    def query(self, *cols):
        if cols[0] is Player:
            return _FakePlayerQuery(self.players, self)
        return _FakeMemoryCountQuery(self.memories, self)


# --------------------------------------------------------------------------- #
# Fixture data
# --------------------------------------------------------------------------- #

PLAYER = uuid.uuid4()


def _player(*, interactions=0, level=1, relationship=25, multiplier=1.0) -> Player:
    return Player(
        id=str(PLAYER),
        aria_total_interactions=interactions,
        aria_consciousness_level=level,
        aria_relationship_score=relationship,
        aria_bonus_multiplier=multiplier,
    )


def _memories(n: int) -> list:
    types = ["combat", "market", "exploration"]
    return [
        ARIAPersonalMemory(
            id=uuid.uuid4(), player_id=str(PLAYER), memory_type=types[i % 3],
            memory_content={}, memory_hash=f"hash-{i}",
        )
        for i in range(n)
    ]


@pytest.fixture()
def service() -> ARIAPersonalIntelligenceService:
    return ARIAPersonalIntelligenceService()


# --------------------------------------------------------------------------- #
# Sync entry point
# --------------------------------------------------------------------------- #

class TestIsGenuinelySync:
    def test_update_consciousness_and_relationship_sync_is_not_a_coroutine(self, service):
        assert not inspect.iscoroutinefunction(service.update_consciousness_and_relationship_sync)


# --------------------------------------------------------------------------- #
# Both-thresholds promotion gate -- the core fix. The old inline blocks
# checked interactions ALONE; these tests prove the new gate genuinely
# requires both.
# --------------------------------------------------------------------------- #

class TestBothThresholdsRequired:
    def test_enough_interactions_insufficient_memories_stays_at_level_1(self, service):
        """60 interactions, 3 memories (well under level 2's threshold of
        10) -- must NOT promote. This is the literal WO acceptance case."""
        db = FakeProgressionSession(players=[_player(interactions=59)], memories=_memories(3))

        for _ in range(60):
            service.update_consciousness_and_relationship_sync(str(PLAYER), db)

        player = db.players[0]
        assert player.aria_total_interactions == 119  # 59 + 60 calls, bookkeeping unchanged
        assert player.aria_consciousness_level == 1
        assert player.aria_bonus_multiplier == 1.0

    def test_enough_interactions_and_enough_memories_promotes_to_level_2(self, service):
        """Same 60 interactions, but 12 memories (>= level 2's threshold
        of 10) -- MUST promote."""
        db = FakeProgressionSession(players=[_player(interactions=0)], memories=_memories(12))

        for _ in range(60):
            service.update_consciousness_and_relationship_sync(str(PLAYER), db)

        player = db.players[0]
        assert player.aria_consciousness_level == 2
        assert player.aria_bonus_multiplier == pytest.approx(1.1)

    def test_enough_memories_insufficient_interactions_stays_at_level_1(self, service):
        """The inverse case -- plenty of memories, too few interactions."""
        db = FakeProgressionSession(players=[_player(interactions=0)], memories=_memories(50))

        for _ in range(10):  # only 10 interactions, well under 50
            service.update_consciousness_and_relationship_sync(str(PLAYER), db)

        assert db.players[0].aria_consciousness_level == 1

    def test_promotion_walks_multiple_levels_in_one_call_if_qualified(self, service):
        """A player already at 1000 interactions / 150 memories jumps
        straight to level 5 on their first tracked call, not stepped."""
        db = FakeProgressionSession(players=[_player(interactions=999, level=1)], memories=_memories(150))

        service.update_consciousness_and_relationship_sync(str(PLAYER), db)

        player = db.players[0]
        assert player.aria_total_interactions == 1000
        assert player.aria_consciousness_level == 5
        assert player.aria_bonus_multiplier == pytest.approx(1.5)

    def test_never_demotes(self, service):
        """A player already at a high level with a low memory count (e.g.
        memories were somehow pruned) never loses their tier."""
        db = FakeProgressionSession(
            players=[_player(interactions=1000, level=4, multiplier=1.35)], memories=_memories(0),
        )

        service.update_consciousness_and_relationship_sync(str(PLAYER), db)

        assert db.players[0].aria_consciousness_level == 4


class TestFalsifiabilityOldSingleThresholdBehavior:
    def test_old_inline_logic_would_have_wrongly_promoted_the_same_fixture(self):
        """Companion tripwire: reproduces the EXACT deleted inline logic
        (movement_service.py / combat_service.py / trading.py, pre-WO) --
        not the SUT -- against the same 60-interactions/3-memories fixture
        used above, and confirms IT would have promoted to level 2 (since
        it never looked at memory count at all). This proves the assertion
        in test_enough_interactions_insufficient_memories_stays_at_level_1
        is genuinely falsifying old behavior, not vacuously true."""
        interactions = 60
        consciousness_level = 1
        thresholds = {50: (2, 1.1), 150: (3, 1.2), 400: (4, 1.35), 1000: (5, 1.5)}
        old_level = consciousness_level
        for threshold, (level, _multiplier) in thresholds.items():
            if interactions >= threshold and consciousness_level < level:
                old_level = level
        assert old_level == 2  # the bug was real


# --------------------------------------------------------------------------- #
# Relationship score -- +1 per hook, capped at 100 (aria-companion.md:139).
# --------------------------------------------------------------------------- #

class TestRelationshipScore:
    def test_climbs_by_exactly_one_per_call(self, service):
        db = FakeProgressionSession(players=[_player(relationship=25)])

        service.update_consciousness_and_relationship_sync(str(PLAYER), db)
        assert db.players[0].aria_relationship_score == 26

        service.update_consciousness_and_relationship_sync(str(PLAYER), db)
        assert db.players[0].aria_relationship_score == 27

    def test_caps_at_100_and_never_overflows(self, service):
        db = FakeProgressionSession(players=[_player(relationship=99)])

        service.update_consciousness_and_relationship_sync(str(PLAYER), db)
        assert db.players[0].aria_relationship_score == 100

        service.update_consciousness_and_relationship_sync(str(PLAYER), db)
        assert db.players[0].aria_relationship_score == 100  # capped, not 101

    def test_reaches_warm_and_bonded_bands_given_enough_hooks(self, service):
        """The WO's own stated goal: warm (50-75) and bonded (75-100) must
        become reachable via ordinary repeated play."""
        db = FakeProgressionSession(players=[_player(relationship=25)])

        for _ in range(30):  # 25 -> 55, into the "warm" band
            service.update_consciousness_and_relationship_sync(str(PLAYER), db)
        assert 50 <= db.players[0].aria_relationship_score < 75

        for _ in range(25):  # 55 -> 80, into the "bonded" band
            service.update_consciousness_and_relationship_sync(str(PLAYER), db)
        assert db.players[0].aria_relationship_score >= 75


# --------------------------------------------------------------------------- #
# Memory-diversity interpretation -- the flagged, not silently resolved,
# design decision.
# --------------------------------------------------------------------------- #

class TestMemoryDiversityInterpretation:
    def test_a_literal_distinct_type_count_would_be_permanently_unreachable(self):
        """Concrete falsifying proof for the dispatch report's flagged
        finding: only 3 memory_type values are ever written anywhere in
        this codebase, so a literal "count of distinct types" reading of
        canon's "unique-type memory diversity" can NEVER reach even the
        lowest promotion threshold (10). Not exercising the SUT -- this
        proves the REJECTED interpretation is non-viable, independent of
        implementation."""
        real_memory_types_ever_written = {"combat", "market", "exploration"}
        lowest_promotion_threshold = ARIAPersonalIntelligenceService.CONSCIOUSNESS_THRESHOLDS[2]["memories"]
        assert len(real_memory_types_ever_written) < lowest_promotion_threshold

    def test_sut_uses_raw_total_count_not_distinct_type_count(self, service):
        """12 memories of the SAME single type must still promote (raw
        count), proving the SUT does not gate on distinct-type cardinality."""
        db = FakeProgressionSession(
            players=[_player(interactions=60)],
            memories=[
                ARIAPersonalMemory(
                    id=uuid.uuid4(), player_id=str(PLAYER), memory_type="combat",
                    memory_content={}, memory_hash=f"hash-{i}",
                )
                for i in range(12)
            ],
        )

        service.update_consciousness_and_relationship_sync(str(PLAYER), db)

        assert db.players[0].aria_consciousness_level == 2


# --------------------------------------------------------------------------- #
# Source-scan pins -- the literal threshold dict appears exactly once, and
# the trading.py medal-dispatch ordering survived the replacement
# byte-intact.
# --------------------------------------------------------------------------- #

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


class TestSourceScanPins:
    def test_threshold_literal_appears_exactly_once_in_src(self):
        assert SRC_ROOT.is_dir(), f"expected src/ at {SRC_ROOT}"

        hits = []
        needle = "150: (3, 1.2)"
        for path in sorted(SRC_ROOT.rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if needle in line:
                    hits.append(f"{path.relative_to(SRC_ROOT)}:{lineno}")

        assert len(hits) == 1, f"expected exactly 1 hit, got: {hits}"
        assert hits[0].startswith("services/aria_personal_intelligence_service.py:")

    def test_trading_py_medal_dispatch_ordering_survives_both_replacements(self):
        """Structural pin (mirrors test_aria_trade_hooks.py's own source-pin
        convention): within each of the buy/sell ARIA-hook try blocks, the
        canonical helper call must be followed by db.flush() then
        _dispatch_trade_medals(db, current_player) -- in that order, inside
        the SAME try block -- exactly as before this WO's replacement."""
        text = (SRC_ROOT / "api" / "routes" / "trading.py").read_text(encoding="utf-8")

        pattern = re.compile(
            r"update_consciousness_and_relationship_sync\(\s*"
            r"str\(current_player\.id\),\s*db\s*\)\s*"
            r"[^\n]*\n(?:\s*#[^\n]*\n)*"  # optional trailing/leading comments
            r"\s*db\.flush\(\)\s*\n"
            r"\s*_dispatch_trade_medals\(db, current_player\)",
        )
        assert len(re.findall(r"update_consciousness_and_relationship_sync\(", text)) == 2, (
            "expected exactly 2 call sites (buy + sell)"
        )
        assert len(pattern.findall(text)) == 2, (
            "expected db.flush() + _dispatch_trade_medals to immediately follow "
            "the ARIA hook call at both the buy and sell sites"
        )

    def test_decay_path_file_was_never_touched_by_this_wo(self):
        """npc_scheduler_service.py's ARIA relationship decay
        (_apply_aria_decay_sync) is explicitly READ-ONLY for this WO --
        pin its presence/shape so a future change is visible, without
        exercising it (out of this WO's scope).

        WO-QUALITY-techdebt-scheduler-split relocated _apply_aria_decay_sync
        (verbatim, alongside _run_weekly_decay_sync) into the scheduler
        package's reputation_team_sweeps module -- retargeting the read path
        only; the literal assertions below are unchanged."""
        text = (
            SRC_ROOT / "services" / "scheduler" / "reputation_team_sweeps.py"
        ).read_text(encoding="utf-8")
        assert "def _apply_aria_decay_sync(" in text
        assert "player.aria_relationship_score = max(0, score - decay)" in text


# --------------------------------------------------------------------------- #
# Failure isolation -- never propagates (matches every other sync twin in
# this class).
# --------------------------------------------------------------------------- #

class TestFailureIsolation:
    def test_db_query_raising_does_not_propagate(self, service):
        class ExplodingSession(FakeProgressionSession):
            def query(self, *cols):
                raise RuntimeError("boom -- simulated query failure")

        boom_db = ExplodingSession()
        result = service.update_consciousness_and_relationship_sync(str(PLAYER), boom_db)
        assert result["success"] is False

    def test_unknown_player_returns_failure_without_raising(self, service):
        db = FakeProgressionSession(players=[], memories=[])
        result = service.update_consciousness_and_relationship_sync(str(PLAYER), db)
        assert result["success"] is False
        assert result["message"] == "Player not found"
