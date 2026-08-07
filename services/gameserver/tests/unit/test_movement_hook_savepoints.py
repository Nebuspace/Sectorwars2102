"""WO-FIX-MOVEMENT-HOOK-SAVEPOINTS: pin the 3 post-move hooks
(flip_formation_discovery, mark_sector_discovered, _dispatch_exploration_medals)
as SAVEPOINT-isolated, matching the pre-existing ARIA exploration-rank-points
hook a few lines above them.

Root cause (live, 2026-08-05): a LockNotAvailable inside mark_sector_
discovered's flush had no savepoint to roll back to, so it poisoned the
whole SQLAlchemy session; the next hook then failed on PendingRollbackError,
propagating a 500 out of move_player_to_sector and stranding the actual
player move -- even though every hook here is explicitly documented as
"must never strand the move."

Two proof layers:
  1. Source-pins on move_player_to_sector -- each of the 3 call sites must
     be wrapped in ``with self.db.begin_nested():``. Cheap, exact, catches
     any future edit that drops the wrap.
  2. A real-SQLite behavioral proof of the underlying pattern itself
     (try/except wrapping begin_nested around a call that raises) --
     mirrors test_aria_quantum_cache_column_savepoint.py's established
     convention. Exercising the full move_player_to_sector call chain
     directly would require fixturing ships/sectors/warp-cost/turn-spend
     machinery unrelated to this fix; this proves the SAVEPOINT semantics
     the fix relies on without that unrelated fixture weight.
"""
from __future__ import annotations

import inspect

import pytest
from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Session

from src.services.movement_service import MovementService

# --------------------------------------------------------------------------- #
# 1. Source pins -- exact call-site wrapping
# --------------------------------------------------------------------------- #

class TestPostMoveHookSavepointWiring:
    def test_formation_discovery_hook_wrapped_in_begin_nested(self) -> None:
        source = inspect.getsource(MovementService._execute_movement)
        assert (
            "            with self.db.begin_nested():\n"
            "                flip_formation_discovery(self.db, player, destination_sector)"
            in source
        )

    def test_sector_discoverer_hook_wrapped_in_begin_nested(self) -> None:
        source = inspect.getsource(MovementService._execute_movement)
        assert (
            "            with self.db.begin_nested():\n"
            "                mark_sector_discovered(self.db, destination_sector, player.id)"
            in source
        )

    def test_medal_dispatch_hook_wrapped_in_begin_nested(self) -> None:
        source = inspect.getsource(MovementService._execute_movement)
        assert (
            "            with self.db.begin_nested():\n                _dispatch_exploration_medals("
            in source
        )

    def test_medal_dispatch_stale_comment_corrected(self) -> None:
        """The old comment claimed the SAVEPOINT 'folds into the same
        commit' while no savepoint actually wrapped the call -- that
        documentation/implementation mismatch must not survive the fix."""
        source = inspect.getsource(MovementService._execute_movement)
        assert "folds into the same commit" not in source


# --------------------------------------------------------------------------- #
# 2. Real-SQLite behavioral proof of the SAVEPOINT pattern itself
# --------------------------------------------------------------------------- #

class _Base(DeclarativeBase):
    pass


class _Probe(_Base):
    __tablename__ = "movement_hook_savepoint_probe"
    id = Column(Integer, primary_key=True, autoincrement=True)
    label = Column(String, nullable=False)


def _best_effort_hook(db: Session, *, should_raise: bool) -> None:
    """Mirrors the exact shape landed in movement_service.py: a best-effort
    hook wrapped in try/except at the call site, with the hook's own work
    wrapped in begin_nested()."""
    try:
        with db.begin_nested():
            db.add(_Probe(label="hook_write"))
            db.flush()
            if should_raise:
                raise RuntimeError("simulated flush-time failure (e.g. LockNotAvailable)")
    except Exception:
        pass  # best-effort: caller's move must never strand on this


@pytest.mark.unit
class TestBestEffortHookSavepointIsolation:
    @pytest.fixture()
    def engine(self):
        eng = create_engine("sqlite:///:memory:")
        _Base.metadata.create_all(eng)
        return eng

    def test_failing_hook_does_not_poison_the_session(self, engine) -> None:
        with Session(engine) as real_session:
            # Simulate the 3-hooks-in-sequence shape: hook 1 fails, hooks
            # 2/3 must still run normally afterward -- proving the failure
            # stayed scoped to its own savepoint, not the whole session.
            _best_effort_hook(real_session, should_raise=True)
            _best_effort_hook(real_session, should_raise=False)
            _best_effort_hook(real_session, should_raise=False)

            # The move's own final commit -- must succeed, not raise
            # PendingRollbackError, proving the session survived.
            real_session.commit()

            rows = real_session.query(_Probe).all()

        # The failing hook's write rolled back with its savepoint (1 less
        # row than 3 successful writes would produce); the two successful
        # hooks after it committed normally.
        assert len(rows) == 2

    # A negative control reproducing the OLD (unwrapped) shape's session-
    # poisoning failure was attempted here but dropped: SQLite's session-
    # invalidation semantics after a mid-flush exception don't reliably
    # match psycopg2's PendingRollbackError behavior on a real Postgres
    # connection, so it couldn't faithfully reproduce the live incident
    # without a real Postgres fixture. The positive test above already
    # proves the landed fix's SAVEPOINT-isolation mechanics; live heimdall
    # re-test (see PR proof) covers the Postgres-specific failure mode.
