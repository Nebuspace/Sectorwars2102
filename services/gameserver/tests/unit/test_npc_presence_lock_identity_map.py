"""Regression pin for WO-NPC-PRESENCE-TWIN (npc_movement_service.
_locked_sectors) -- the NPC-side twin of WO-MONEY-STRAGGLER-NAIVE's player-
side fix at movement_service._update_player_presence (see
test_movement_presence_lock_identity_map.py).

PREMISE: ``move_npc`` (npc_movement_service.py ~:240) loads origin/dest
``Sector`` UNLOCKED earlier via ``hop_cost`` -> ``MovementService.
_check_direct_warp``'s plain ``db.query(Sector).filter(...).first()`` reads,
loading them into the SQLAlchemy Session's identity map. Later in the SAME
call, ``_locked_sectors`` (~:185) re-reads the SAME sector_ids under
``.with_for_update()`` to do the ``players_present`` JSONB read-modify-write.
Without ``.populate_existing()``, ``Query.with_for_update()`` acquires the
real row lock but does NOT refresh the already-mapped Python object's
attributes -- a concurrent player move's committed ``players_present``
write (movement_service._update_player_presence, already fixed) is
invisible to the NPC hop's re-lock: a race window that can silently clobber
a presence entry (evading ambush/PvP detection).

VERIFY-FIRST finding (this WO's own doctrine -- "no naive .populate_
existing() guard survives without a whole-row + caller-reentrancy trace"):
naive-only is NOT safe. ``_locked_sectors`` has a THIRD caller besides
``move_npc``/``_relocate_npc`` -- ``npc_engagement_service._place_squad``
calls it ONCE PER SQUAD OFFICER inside a for-loop, all sharing the SAME
``dest_sector_id`` (the offender's current sector) and often the same
``old_sector_id`` too, with NO flush between officers. On a session opened
autoflush=False (core/database.py:19), officer N's ``add_npc_presence``
write to the shared dest sector is still pending when officer N+1's call
re-locks that SAME row -- a bare ``.populate_existing()`` would DISCARD
officer N's presence write. Fix: ``self.db.flush()`` as the first statement
in ``_locked_sectors``, immediately before the lock loop -- persists any
pending pre-lock Sector mutation (this call's own or an earlier
same-session caller's) first, so the populate_existing() re-read observes
it instead of reverting it. Mirrors movement_service.py's WO-MONEY-
STRAGGLER-NAIVE precedent for the identical class of bug. ``move_npc``
itself has no pre-lock Sector mutation of its own (confirmed: everything
between the unlocked ``hop_cost`` read and the lock is read-only cost/
pacing checks; ``move_npc`` never detonates mines / touches ``defenses``
like the player path does) -- the flush is needed for the SHARED helper's
third caller, not for ``move_npc``'s own body.

Three parts, mirroring test_movement_presence_lock_identity_map.py /
test_siege_skim_flush_first_straggler.py's own structure:

A) Generalized real-SQLAlchemy identity-map staleness/fix mechanism proof
   against a minimal mirror class (real ``Sector.players_present`` is
   Postgres-only JSONB; ``Sector.__table__.create()`` fails on SQLite, same
   blocker documented in those two files). Permanent regression marker.

B) The REAL, unmodified ``npc_movement_service._locked_sectors`` function,
   ``Sector`` monkeypatched to the mirror class: (1) proves the core WO ask
   -- a concurrent player move's committed write is visible to the NPC
   hop's re-lock; (2) proves the FLUSH-FIRST half -- a squad-loop-shaped
   sequence of same-session ``_locked_sectors`` calls on an overlapping
   dest sector does not discard an earlier officer's pending presence
   write; (3) composes both in one scenario.

C) Structural AST pin: ``db.flush()`` is the first statement in
   ``_locked_sectors`` and ``.populate_existing()`` precedes
   ``.with_for_update()`` in the query chain -- independent of Part B's
   mechanism proof, which would not notice a regression that reorders or
   removes the flush without changing this test's specific inputs.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import Column, Integer, JSON, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

import src.services.npc_movement_service as npc_movement_service_module
from src.models.ship import ShipType
from src.services.npc_movement_service import (
    _locked_sectors,
    add_npc_presence,
)

_Base = declarative_base()


class _MirrorSector(_Base):
    """Minimal stand-in for Sector -- id + sector_id + one mutable JSON-ish
    column, just enough to exercise the identity-map mechanics under test
    (real Sector.players_present is Postgres-only JSONB; SQLite's generic
    JSON type reproduces the same identity-map refresh semantics -- same
    technique as test_movement_presence_lock_identity_map.py's own
    ``_MirrorSector``)."""
    __tablename__ = "mirror_sectors_npc_presence"
    id = Column(Integer, primary_key=True)
    sector_id = Column(Integer, nullable=False, unique=True)
    players_present = Column(JSON, nullable=False, default=list)


def _session_factory() -> sessionmaker:
    # autoflush=False deliberately matches the real app Session
    # (core/database.py:19) -- the bug (and its squad-loop reentrancy
    # variant) only exists because autoflush is off.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    _Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)


def _npc(npc_id: uuid.UUID, name: str = "Marshal Vance") -> SimpleNamespace:
    """Lightweight stand-in carrying only what add_npc_presence's
    _presence_entry() reads off an NPCCharacter (npc_spawn_service.py
    :441-460). No Session membership needed -- never flag_modified'd."""
    return SimpleNamespace(
        id=npc_id,
        display_name=name,
        archetype=SimpleNamespace(name="LAW_ENFORCEMENT"),
        notoriety=None,
    )


def _ship(ship_id: uuid.UUID, name: str = "FSS Vigil") -> SimpleNamespace:
    return SimpleNamespace(id=ship_id, name=name, type=ShipType.SCOUT_SHIP)


# --------------------------------------------------------------------------- #
# Part A -- generalized real-SQLAlchemy staleness/fix mechanism proof.
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestSectorLockIdentityMapMechanism:
    """SQLAlchemy's own identity-map refresh semantics -- not
    npc_movement_service code. Permanent regression marker: if this ever
    starts failing, SQLAlchemy's own semantics changed underneath the
    codebase, worth knowing independent of the production-code tests below.
    """

    def test_bare_with_for_update_after_unlocked_read_stays_stale(self) -> None:
        SessionFactory = _session_factory()
        seed = SessionFactory()
        seed.add(_MirrorSector(id=1, sector_id=2101, players_present=[]))
        seed.commit()
        seed.close()

        S = SessionFactory()
        unlocked = S.query(_MirrorSector).filter(_MirrorSector.sector_id == 2101).first()
        assert unlocked.players_present == []

        # A concurrent mover (player or another NPC) commits a real write
        # to the SAME row from a DIFFERENT session.
        other = SessionFactory()
        other_row = other.query(_MirrorSector).filter(_MirrorSector.sector_id == 2101).first()
        other_row.players_present = [{"player_id": "concurrent-mover"}]
        other.commit()
        other.close()

        # THE BROKEN shape: bare with_for_update, no populate_existing.
        locked = (
            S.query(_MirrorSector).filter(_MirrorSector.sector_id == 2101)
            .with_for_update().first()
        )
        assert locked.players_present == []  # STALE -- the bug, not the real value
        assert locked is unlocked  # literally the same cached Python object
        S.close()

    def test_populate_existing_after_unlocked_read_sees_fresh(self) -> None:
        SessionFactory = _session_factory()
        seed = SessionFactory()
        seed.add(_MirrorSector(id=1, sector_id=2101, players_present=[]))
        seed.commit()
        seed.close()

        S = SessionFactory()
        unlocked = S.query(_MirrorSector).filter(_MirrorSector.sector_id == 2101).first()

        other = SessionFactory()
        other_row = other.query(_MirrorSector).filter(_MirrorSector.sector_id == 2101).first()
        other_row.players_present = [{"player_id": "concurrent-mover"}]
        other.commit()
        other.close()

        locked = (
            S.query(_MirrorSector).filter(_MirrorSector.sector_id == 2101)
            .populate_existing().with_for_update().first()
        )
        assert locked.players_present == [{"player_id": "concurrent-mover"}]  # FRESH
        assert locked is unlocked  # same identity-map slot, refreshed in place
        S.close()


# --------------------------------------------------------------------------- #
# Part B -- REAL, unmodified npc_movement_service._locked_sectors, Sector
# monkeypatched to the mirror class.
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def _mirror_sector(monkeypatch):
    monkeypatch.setattr(npc_movement_service_module, "Sector", _MirrorSector)


@pytest.mark.unit
class TestLockedSectorsSeesFreshConcurrentMove:
    """The core WO ask: an NPC hop's re-lock must see a concurrent player
    move's committed players_present write, not the stale unlocked-read
    snapshot move_npc's own hop_cost created earlier."""

    def test_locked_sectors_sees_concurrent_committed_presence_write(self) -> None:
        SessionFactory = _session_factory()
        seed = SessionFactory()
        seed.add(_MirrorSector(id=1, sector_id=3001, players_present=[]))
        seed.add(_MirrorSector(id=2, sector_id=3002, players_present=[]))
        seed.commit()
        seed.close()

        S = SessionFactory()
        # Mirrors hop_cost's own unlocked Sector reads (npc_movement_
        # service.py :118-119) -- the read that seeds the stale
        # identity-map entry before move_npc ever reaches the lock.
        unlocked_dest = (
            S.query(_MirrorSector).filter(_MirrorSector.sector_id == 3002).first()
        )
        assert unlocked_dest.players_present == []

        # A concurrent PLAYER move (movement_service._update_player_
        # presence, already fixed) commits a real write to the SAME dest
        # sector from a DIFFERENT session.
        other = SessionFactory()
        other_dest = other.query(_MirrorSector).filter(_MirrorSector.sector_id == 3002).first()
        other_dest.players_present = [{"player_id": "player-A", "is_npc": False}]
        other.commit()
        other.close()

        locked = _locked_sectors(S, [3001, 3002])
        assert locked[3002].players_present == [{"player_id": "player-A", "is_npc": False}]
        assert locked[3002] is unlocked_dest  # same identity-map slot, refreshed in place
        S.close()


@pytest.mark.unit
class TestLockedSectorsSquadLoopReentrancy:
    """npc_engagement_service._place_squad's real shape: several officers of
    the SAME squad each call _locked_sectors in sequence, sharing
    dest_sector_id (and often old_sector_id too), with NO flush between
    officers. Proves the FLUSH-FIRST half of the fix -- without it, officer
    N+1's populate_existing() re-lock would discard officer N's still-
    pending presence write on the shared dest sector."""

    def test_second_officer_lock_does_not_discard_first_officers_pending_presence(
        self,
    ) -> None:
        SessionFactory = _session_factory()
        seed = SessionFactory()
        seed.add(_MirrorSector(id=1, sector_id=4001, players_present=[]))  # dest (offender's sector)
        seed.add(_MirrorSector(id=2, sector_id=4002, players_present=[]))  # officer 1's home sector
        seed.add(_MirrorSector(id=3, sector_id=4003, players_present=[]))  # officer 2's home sector
        seed.commit()
        seed.close()

        S = SessionFactory()
        officer1, officer2 = uuid.uuid4(), uuid.uuid4()

        # Officer 1's placement (mirrors _place_squad's first loop iteration).
        locked1 = _locked_sectors(S, [4002, 4001])
        add_npc_presence(locked1[4001], _npc(officer1, "Marshal Vance"), _ship(uuid.uuid4()))
        # NO flush -- _place_squad never flushes between officers.

        # Officer 2's placement -- SAME dest sector (4001), different home.
        locked2 = _locked_sectors(S, [4003, 4001])
        assert locked2[4001] is locked1[4001]  # same identity-map slot

        present_ids = {p["player_id"] for p in locked2[4001].players_present}
        assert str(officer1) in present_ids, (
            "officer 1's pending presence write was discarded by officer 2's "
            "populate_existing() re-lock -- the flush-first fix is missing"
        )

        add_npc_presence(locked2[4001], _npc(officer2, "Captain Reyes"), _ship(uuid.uuid4()))
        present_ids = {p["player_id"] for p in locked2[4001].players_present}
        assert present_ids == {str(officer1), str(officer2)}
        S.close()

    def test_composed_with_a_genuinely_concurrent_commit_on_a_different_sector(
        self,
    ) -> None:
        """Both properties at once: officer 1's pending same-session write on
        the dest sector survives officer 2's re-lock, AND a genuinely
        concurrent, already-committed change from a DIFFERENT session on
        officer 2's OWN home sector is still picked up fresh -- proving this
        is a real refresh, not just "the flush made the discard moot"."""
        SessionFactory = _session_factory()
        seed = SessionFactory()
        seed.add(_MirrorSector(id=1, sector_id=5001, players_present=[]))  # dest
        seed.add(_MirrorSector(id=2, sector_id=5002, players_present=[]))  # officer 2's home
        seed.commit()
        seed.close()

        S = SessionFactory()
        officer1 = uuid.uuid4()

        locked1 = _locked_sectors(S, [5001])
        add_npc_presence(locked1[5001], _npc(officer1), _ship(uuid.uuid4()))
        # NO flush.

        # A genuinely concurrent, different-session commit on officer 2's
        # home sector (5002) -- a column this session's own pending state
        # never touches.
        other = SessionFactory()
        other_row = other.query(_MirrorSector).filter(_MirrorSector.sector_id == 5002).first()
        other_row.players_present = [{"player_id": "concurrent-npc"}]
        other.commit()
        other.close()

        locked2 = _locked_sectors(S, [5002, 5001])

        # (1) officer 1's pending dest-sector write PRESERVED.
        present_ids = {p["player_id"] for p in locked2[5001].players_present}
        assert str(officer1) in present_ids
        # (2) fresh concurrent commit on the OTHER sector SEEN.
        assert locked2[5002].players_present == [{"player_id": "concurrent-npc"}]
        S.close()


# --------------------------------------------------------------------------- #
# Part C -- structural AST pin.
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestLockedSectorsStructuralWiring:
    def test_flush_is_the_first_statement_before_the_lock_loop(self) -> None:
        source = textwrap.dedent(inspect.getsource(npc_movement_service_module._locked_sectors))
        tree = ast.parse(source)
        func = tree.body[0]
        assert isinstance(func, ast.FunctionDef)
        assert func.name == "_locked_sectors"

        def dotted(node: ast.AST) -> str:
            parts: list[str] = []
            while isinstance(node, ast.Attribute):
                parts.append(node.attr)
                node = node.value
            if isinstance(node, ast.Name):
                parts.append(node.id)
            return ".".join(reversed(parts))

        def top_level_call_name(stmt: ast.stmt):
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                return dotted(stmt.value.func)
            return None

        body = func.body
        if ast.get_docstring(func) is not None:
            body = body[1:]  # drop the docstring node itself

        call_names = [top_level_call_name(stmt) for stmt in body]
        assert call_names[0] == "db.flush", (
            "the first executable statement in _locked_sectors must be a "
            "bare db.flush() -- it persists any pending pre-lock Sector "
            "mutation (this call's own or an earlier same-session caller's, "
            "e.g. npc_engagement_service._place_squad's squad loop) so the "
            f"populate_existing() re-read below observes it. Got: {call_names!r}"
        )

    def test_populate_existing_precedes_with_for_update_in_the_query_chain(self) -> None:
        # Strip the docstring first -- it prose-mentions both
        # ``.with_for_update()`` and ``.populate_existing()`` (explaining the
        # bug/fix), which would otherwise pollute a raw substring search on
        # the full source with the wrong (docstring) occurrence.
        source = inspect.getsource(npc_movement_service_module._locked_sectors)
        tree = ast.parse(textwrap.dedent(source))
        func = tree.body[0]
        body_without_docstring = func.body[1:] if ast.get_docstring(func) is not None else func.body
        code_only = "\n".join(ast.unparse(stmt) for stmt in body_without_docstring)

        assert ".populate_existing()" in code_only
        assert ".with_for_update()" in code_only
        assert code_only.index(".populate_existing()") < code_only.index(".with_for_update()")
