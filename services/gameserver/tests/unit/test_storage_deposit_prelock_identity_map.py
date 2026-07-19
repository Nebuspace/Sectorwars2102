"""WO-STORE-EXPIRY-CLAIMABLE Q2 mitigation-a: regression pin for mack's
CONFIRMED CRITICAL against the first version of the pre-lock deposit
guard (money-RMW identity-map poisoning).

PREMISE: an earlier draft of `storage_service._prelock_deposit_guard`
did an UNLOCKED full-ORM read of the Locker and the Player before the
real, locked flow ran. That loads each into the SQLAlchemy Session's
identity map (keyed on (mapped class, primary key)). SQLAlchemy's
`Query.with_for_update()` acquires the real DB row lock but does NOT
refresh an already-mapped object's attribute values from the fresh row
unless `.populate_existing()` is also called -- a SECOND query for the
SAME PK within the SAME session simply returns the cached Python object,
untouched. Since the pre-lock guard's own Locker belongs to the
depositing player (`locker.owner_player_id == player_id`), this reached
`settle_fee`'s own `owner.credits -= charge` -- a genuine lost-update on
player.credits against any concurrently-committed change in the window
between the pre-check and the "authoritative" locked re-read.

Fix: the pre-check reads ONLY columns (`db.query(Model.col, ...)` --
tuples/scalars, never a mapped entity), which structurally cannot touch
the identity map at all -- SQLAlchemy's column-only Query code path is
categorically different from its entity-returning path. This is a
property of SQLAlchemy's OWN Session/Query machinery, not a Player- or
StorageLocker-specific implementation detail, so this file proves it
against a minimal, self-contained mapped class rather than fighting
Player's several Postgres-only JSONB/ARRAY columns (which block
`Player.__table__.create()` on SQLite entirely -- confirmed: `CompileError:
... can't render element of type JSONB`) -- the SAME identity-map
mechanics apply identically regardless of which mapped class or which
columns are involved, so a minimal reproduction generalizes cleanly to
the real `_prelock_deposit_guard` / `settle_fee` call chain.

Uses a REAL SQLAlchemy engine + Session (SQLite, `StaticPool` +
`check_same_thread=False` so multiple `Session()` calls share ONE
underlying in-memory database, precisely so a second, independently-
committing session can simulate "a concurrent transaction changed this
row in between") -- NOT this test file's usual hand-rolled `_FakeSession`
pattern, which has no identity map at all and therefore cannot
distinguish the broken design from the fixed one on this specific
property. `with_for_update()` is a documented no-op on SQLite (no row-
level locking support) -- irrelevant here, since this test is entirely
about identity-map REFRESH semantics, not real lock acquisition (that
stays the live-Postgres two-connection CI leg's job, same as every other
genuine-contention property in this WO)."""
from __future__ import annotations

import pytest
from sqlalchemy import Column, Integer, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

_Base = declarative_base()


class _MirrorRow(_Base):
    """A minimal stand-in for Player/StorageLocker -- one mutable column,
    just enough to exercise the identity-map mechanics under test."""
    __tablename__ = "mirror_rows"
    id = Column(Integer, primary_key=True)
    value = Column(Integer, nullable=False)


def _make_session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    _Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.mark.unit
class TestIdentityMapScalarPrelockPattern:
    def test_full_orm_prelock_poisons_the_map_stale_read_repro(self) -> None:
        """The BROKEN shape mack found, reproduced directly: an unlocked
        FULL-ORM read followed by a 'locked' re-read of the SAME PK, in
        the SAME session, returns the SAME cached Python object -- stale,
        even though a DIFFERENT session genuinely committed a change to
        the row in between. Kept as a permanent regression marker (if
        this ever starts failing, SQLAlchemy's own identity-map
        semantics changed underneath this codebase -- worth knowing)."""
        session_factory = _make_session_factory()

        setup = session_factory()
        setup.add(_MirrorRow(id=1, value=100))
        setup.commit()
        setup.close()

        poisoned_session = session_factory()
        # Mirrors the REJECTED earlier draft's shape: a full-ORM
        # unlocked pre-check.
        pre_check = poisoned_session.query(_MirrorRow).filter(_MirrorRow.id == 1).first()
        assert pre_check.value == 100

        # A different session commits a real, concurrent change.
        other_session = session_factory()
        other_row = other_session.query(_MirrorRow).filter(_MirrorRow.id == 1).first()
        other_row.value = 999
        other_session.commit()
        other_session.close()

        # The "authoritative" locked re-read -- SAME session, SAME PK.
        locked = (
            poisoned_session.query(_MirrorRow).filter(_MirrorRow.id == 1).with_for_update().first()
        )

        assert locked.value == 100  # STALE -- the bug, not the real 999
        assert locked is pre_check  # literally the same cached Python object
        poisoned_session.close()

    def test_scalar_only_prelock_never_poisons_the_map_fresh_read(self) -> None:
        """THE FIX: a scalar (column-only) unlocked pre-check never adds
        anything to the identity map, so the LATER full-ORM locked read
        is genuinely the FIRST load of that PK this session has ever
        done -- fresh, real, not cached. Same setup as the repro above,
        only the pre-check's query shape differs."""
        session_factory = _make_session_factory()

        setup = session_factory()
        setup.add(_MirrorRow(id=1, value=100))
        setup.commit()
        setup.close()

        fresh_session = session_factory()
        # Mirrors _prelock_deposit_guard's ACTUAL shape: db.query(Model.col).scalar().
        pre_check_value = fresh_session.query(_MirrorRow.value).filter(_MirrorRow.id == 1).scalar()
        assert pre_check_value == 100

        other_session = session_factory()
        other_row = other_session.query(_MirrorRow).filter(_MirrorRow.id == 1).first()
        other_row.value = 999
        other_session.commit()
        other_session.close()

        locked = fresh_session.query(_MirrorRow).filter(_MirrorRow.id == 1).with_for_update().first()

        assert locked.value == 999  # FRESH -- sees the real, concurrent commit
        fresh_session.close()
