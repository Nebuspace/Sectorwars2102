"""Regression pin for WO-MONEY-STRAGGLER-NAIVE site 2/3 (movement_service --
the PRIORITY site of the 3-site set: unconditional, fires on EVERY player
move, unlike the other two sites which are conditionally reached).

PREMISE: before a move commits, `_check_nexus_subscription_gate`
(movement_service.py ~:1054) does an UNLOCKED, full-ORM read of the
destination `Sector` by `sector_id` -- loading it into the SQLAlchemy
Session's identity map (keyed on (mapped class, primary key)). Later in the
SAME request/session, `_update_player_presence` (movement_service.py ~:2400)
re-reads the SAME sector_id under `.with_for_update()` to do the
`players_present` JSONB read-modify-write its own docstring (:2401-2409)
says protects against concurrent NPC-mover writes to the same array.
Without `.populate_existing()`, SQLAlchemy's `Query.with_for_update()`
acquires the real row lock but does NOT refresh the already-mapped Python
object's attributes from the fresh row -- a second query for the SAME PK in
the SAME session just returns the cached object, silently defeating the RMW
protection the docstring promises.

Fix (this WO): `.populate_existing()` immediately before `.with_for_update()`
in `_update_player_presence`'s Sector lock loop (movement_service.py ~:2417).
No antecedent-mutation footgun here (unlike the FLUSHFIRST sites) --
`players_present` is mutated only AFTER the lock is acquired, so a bare
`.populate_existing()` cannot discard a pending pre-lock write.

Two halves, matching the WO's fallback structure:

A) Real-SQLAlchemy identity-map staleness repro, generalized against a
   minimal mirror class. Real `Sector.players_present` is Postgres-only
   JSONB (`Sector.__table__.create()` fails on SQLite, same blocker
   documented in test_storage_deposit_prelock_identity_map.py for Player's
   JSONB/ARRAY columns). The identity-map REFRESH semantics under test are a
   property of SQLAlchemy's own Session/Query machinery, not of Sector's
   specific columns, so this generalizes cleanly -- same technique, same
   precedent, same codebase.

B) Structural + behavioral proof against the REAL, unmocked
   `_update_player_presence` method: a MagicMock session correlates each
   `.filter(Sector.sector_id == sid)` call to the right sector-under-lock (by
   reading the real BinaryExpression's bound literal off `expr.right.value`),
   proving (1) the query chain actually calls `.populate_existing()` before
   `.with_for_update()` in production code for BOTH locked sids, and (2) the
   players_present add/remove RMW the method performs is still correct
   through that exact chain.

FOLLOW-UP (mack's adversarial gate, CONFIRMED CRITICAL against the fix
above): `.populate_existing()` alone was not naive-safe after all --
`_detonate_sector_mines` (movement_service.py ~:2072, called at ~:2188)
mutates `destination_sector.defenses` (mine count, `mine_owner_id`)
IN-MEMORY, on a session opened `autoflush=False` (core/database.py:19),
BEFORE `_update_player_presence` runs at ~:2378. The method's only
`self.db.flush()` sits inside the ARIA-exploration-map hook's first-visit-
only branch (~:2314) -- on a REPEAT visit to a mined sector (the normal
case) nothing flushes the mine mutation, so `_update_player_presence`'s
`.populate_existing()` re-read of the SAME Sector row DISCARDS it (mines
3->2 in memory reverts to 3 on read-back). Fix: `self.db.flush()`
immediately before the `_update_player_presence` call (~:2375-2378) --
persists any pending pre-lock Sector mutation (mine detonation and any
future hook of the same shape) first, so the `populate_existing()` re-read
observes it instead of reverting it. Mirrors bounty_service.collect_bounty's
WO-BOUNTY-COLLECT-FLUSH precedent for the identical class of bug.

C) Real-SQLAlchemy mechanism proof, calling the REAL, unmocked
   `_detonate_sector_mines` + `_update_player_presence` bound methods in the
   exact order `_execute_movement` calls them (a "minimal slice", not the
   full method -- test_movement_core_pins.py documents why driving
   `_execute_movement` fully is impractical: it fans out into a long chain
   of best-effort service hooks). `movement_service.Sector` is monkeypatched
   to a mirror class carrying `defenses` (real `Sector.defenses` is
   Postgres-only JSONB, same SQLite blocker as Part A).

D) Structural AST pin proving `_execute_movement`'s actual top-level
   statement order has `self.db.flush()` as the statement immediately
   preceding `self._update_player_presence(...)` -- so a regression that
   removes or reorders the fix (not just breaks the abstract mechanism) is
   caught even though Part C's mirror-based mechanism proof, by itself,
   doesn't call `_execute_movement` at all.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import Column, Integer, JSON, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

import src.services.movement_service as movement_service_module
from src.models.sector import Sector
from src.models.ship import Ship, ShipType
from src.services.movement_service import MovementService

_Base = declarative_base()


class _MirrorSector(_Base):
    """Minimal stand-in for Sector -- id + sector_id + one mutable JSON-ish
    column, just enough to exercise the identity-map mechanics under test
    (real Sector.players_present is Postgres-only JSONB; SQLite's generic
    JSON type reproduces the same identity-map refresh semantics)."""
    __tablename__ = "mirror_sectors"
    id = Column(Integer, primary_key=True)
    sector_id = Column(Integer, nullable=False, unique=True)
    players_present = Column(JSON, nullable=False, default=list)


def _make_session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    _Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.mark.unit
class TestPresenceLockIdentityMapStaleness:
    """Part A -- generalized real-SQLAlchemy staleness repro."""

    def test_bare_with_for_update_after_unlocked_destination_read_stays_stale(self) -> None:
        """Reproduces the bug: an unlocked full-ORM read of the destination
        sector (mirrors _check_nexus_subscription_gate's ~:1054 read),
        followed by a bare `.with_for_update()` re-read of the SAME sid in
        the SAME session, returns the SAME cached object -- stale, even
        though a different session genuinely committed a players_present
        change in between. Kept as a permanent regression marker (if this
        ever starts failing, SQLAlchemy's own identity-map semantics changed
        underneath this codebase -- worth knowing)."""
        session_factory = _make_session_factory()

        setup = session_factory()
        setup.add(_MirrorSector(id=1, sector_id=1301, players_present=[]))
        setup.commit()
        setup.close()

        move_session = session_factory()
        # Mirrors the unlocked destination-sector read every move already does.
        unlocked_read = (
            move_session.query(_MirrorSector).filter(_MirrorSector.sector_id == 1301).first()
        )
        assert unlocked_read.players_present == []

        # A concurrent request (another player's move, or an NPC mover --
        # see the docstring at movement_service.py:2401-2409) commits a real
        # players_present change to the SAME row.
        other_session = session_factory()
        other_row = (
            other_session.query(_MirrorSector).filter(_MirrorSector.sector_id == 1301).first()
        )
        other_row.players_present = [{"player_id": "concurrent-mover"}]
        other_session.commit()
        other_session.close()

        # The BROKEN shape: bare with_for_update, no populate_existing.
        locked = (
            move_session.query(_MirrorSector)
            .filter(_MirrorSector.sector_id == 1301)
            .with_for_update()
            .first()
        )

        assert locked.players_present == []  # STALE -- the bug, not the real value
        assert locked is unlocked_read  # literally the same cached Python object

    def test_populate_existing_with_for_update_after_unlocked_destination_read_sees_fresh(
        self,
    ) -> None:
        """THE FIX, same setup: `.populate_existing()` immediately before
        `.with_for_update()` -- matches _update_player_presence's exact
        chain shape -- forces a real refresh from the row the lock just
        acquired, so the RMW genuinely observes the concurrent commit."""
        session_factory = _make_session_factory()

        setup = session_factory()
        setup.add(_MirrorSector(id=1, sector_id=1301, players_present=[]))
        setup.commit()
        setup.close()

        move_session = session_factory()
        unlocked_read = (
            move_session.query(_MirrorSector).filter(_MirrorSector.sector_id == 1301).first()
        )
        assert unlocked_read.players_present == []

        other_session = session_factory()
        other_row = (
            other_session.query(_MirrorSector).filter(_MirrorSector.sector_id == 1301).first()
        )
        other_row.players_present = [{"player_id": "concurrent-mover"}]
        other_session.commit()
        other_session.close()

        locked = (
            move_session.query(_MirrorSector)
            .filter(_MirrorSector.sector_id == 1301)
            .populate_existing()
            .with_for_update()
            .first()
        )

        assert locked.players_present == [{"player_id": "concurrent-mover"}]  # FRESH
        assert locked is unlocked_read  # same identity-map slot, refreshed in place


def _make_player(player_id: uuid.UUID) -> SimpleNamespace:
    """Lightweight stand-in carrying only the attributes
    _update_player_presence reads off `player` (see movement_service.py
    :2439-2446). Cannot use a bare object() -- SimpleNamespace matches the
    established mock-session style of sibling movement tests
    (test_movement_region_sync.py)."""
    return SimpleNamespace(
        id=player_id,
        username="mover",
        current_ship_id=None,
        current_ship=None,
        team_id=None,
    )


@pytest.mark.unit
class TestUpdatePlayerPresenceRealMethod:
    """Part B -- structural + behavioral proof against the REAL, unmocked
    `_update_player_presence`. Uses real `Sector` ORM instances (unpersisted
    -- flag_modified operates on any mapped instance's InstanceState,
    session membership not required) because the method calls
    `flag_modified(sector, 'players_present')`, which requires a genuinely
    mapped object -- a SimpleNamespace stand-in would raise there.
    """

    def test_query_chain_calls_populate_existing_before_with_for_update_both_sides(
        self,
    ) -> None:
        """Fix-presence + presence-RMW-correctness in one shot: correlates
        each `.filter(Sector.sector_id == sid)` call (via the real
        BinaryExpression's bound literal) to the sector under lock, so the
        SAME mock proves both (1) the chain shape used in production and
        (2) that the resulting add/remove RMW is still correct."""
        mover_id = uuid.uuid4()
        old_sector = Sector(sector_id=1001, players_present=[{"player_id": str(mover_id)}])
        new_sector = Sector(sector_id=1301, players_present=[])
        by_sid = {1001: old_sector, 1301: new_sector}

        seen_branches: list[tuple[int, MagicMock]] = []

        def filter_side_effect(expr):
            sid = expr.right.value
            branch = MagicMock()
            branch.populate_existing.return_value.with_for_update.return_value.first.return_value = (
                by_sid[sid]
            )
            seen_branches.append((sid, branch))
            return branch

        mock_db = MagicMock()
        mock_db.query.return_value.filter.side_effect = filter_side_effect

        service = MovementService(mock_db)
        service._update_player_presence(_make_player(mover_id), 1001, 1301)

        # Behavioral: RMW still correct through the fixed chain.
        assert old_sector.players_present == []
        assert len(new_sector.players_present) == 1
        assert new_sector.players_present[0]["player_id"] == str(mover_id)
        assert new_sector.players_present[0]["username"] == "mover"

        # Structural: both locked sids went through populate_existing
        # BEFORE with_for_update -- not a direct bare with_for_update call.
        assert {sid for sid, _ in seen_branches} == {1001, 1301}
        for sid, branch in seen_branches:
            call_names = [call[0] for call in branch.mock_calls]
            assert call_names[0] == "populate_existing", (sid, call_names)
            assert "populate_existing().with_for_update" in call_names, (sid, call_names)
            # The bypass shape (calling with_for_update directly on the
            # filter result, skipping populate_existing) is never exercised.
            assert branch.with_for_update.called is False, (sid, call_names)

    def test_single_sector_move_only_adds_no_stale_removal_branch(self) -> None:
        """Sanity: when old_sector_id == new_sector_id (e.g. a same-sector
        no-op path reaching this method), sorted(set) collapses to one lock,
        and that lock still goes through populate_existing -- no branch is
        silently skipped."""
        mover_id = uuid.uuid4()
        sector = Sector(sector_id=1001, players_present=[])
        seen_branches: list[tuple[int, MagicMock]] = []

        def filter_side_effect(expr):
            sid = expr.right.value
            branch = MagicMock()
            branch.populate_existing.return_value.with_for_update.return_value.first.return_value = (
                sector
            )
            seen_branches.append((sid, branch))
            return branch

        mock_db = MagicMock()
        mock_db.query.return_value.filter.side_effect = filter_side_effect

        service = MovementService(mock_db)
        service._update_player_presence(_make_player(mover_id), 1001, 1001)

        assert len(seen_branches) == 1
        _, branch = seen_branches[0]
        call_names = [call[0] for call in branch.mock_calls]
        assert call_names[0] == "populate_existing"
        # old-sector removal then new-sector add both applied to the same row.
        assert len(sector.players_present) == 1
        assert sector.players_present[0]["player_id"] == str(mover_id)


class _MirrorSectorWithDefenses(_Base):
    """Mirror stand-in carrying `defenses` too (real Sector.defenses is
    Postgres-only JSONB -- same SQLite blocker as `_MirrorSector` above).
    A separate class/table from `_MirrorSector`: declarative registries
    reject two classes sharing one `__tablename__` in the same `_Base`."""
    __tablename__ = "mirror_sectors_with_defenses"
    id = Column(Integer, primary_key=True)
    sector_id = Column(Integer, nullable=False, unique=True)
    players_present = Column(JSON, nullable=False, default=list)
    defenses = Column(JSON, nullable=False, default=dict)


def _make_mover(mover_id: uuid.UUID) -> SimpleNamespace:
    """Player stand-in carrying a REAL, unpersisted `Ship` -- required
    because `_detonate_sector_mines` calls
    `flag_modified(player.current_ship, "combat")`, which needs a genuinely
    mapped object (a SimpleNamespace ship would raise there). The combat
    dict is pre-seeded with every key `_ensure_combat_state` checks for, so
    it never queries `ShipSpecification` (out of scope for this proof --
    mirrors test_money_nolock_rmw_mack.py's own "minimal reproduction, not
    the full combat-resolution scaffolding" argument)."""
    ship = Ship(
        type=ShipType.SCOUT_SHIP,
        is_destroyed=False,
        combat={"shields": 0.0, "max_shields": 0.0, "hull": 10.0, "max_hull": 10.0},
    )
    return SimpleNamespace(
        id=mover_id,
        username="mover",
        team_id=None,
        current_ship_id=uuid.uuid4(),
        current_ship=ship,
    )


def _mine_session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    _Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)


@pytest.mark.unit
class TestMineDetonationSurvivesPresenceLock:
    """Part C -- mack's CRITICAL: the REAL `_detonate_sector_mines` +
    `_update_player_presence` bound methods, called in production order,
    against a real SQLite session (`movement_service.Sector` monkeypatched
    to `_MirrorSectorWithDefenses`)."""

    def _seed(self, session_factory, *, mines, owner_id):
        seed = session_factory()
        seed.add(_MirrorSectorWithDefenses(
            id=1, sector_id=1301, players_present=[],
            defenses={"mines": mines, "mine_owner_id": str(owner_id), "mine_team_id": None},
        ))
        seed.commit()
        seed.close()

    def test_bare_populate_existing_without_flush_discards_mine_detonation(
        self, monkeypatch,
    ) -> None:
        """Non-vacuous companion / bug reproduction: the PRE-FIX shape
        (populate_existing, no flush before it) reverts the real mine
        decrement mack's repro exposed -- proving the harness (not luck)
        is what makes the fixed test below pass."""
        owner_id = uuid.uuid4()
        session_factory = _mine_session_factory()
        self._seed(session_factory, mines=3, owner_id=owner_id)

        monkeypatch.setattr(movement_service_module, "Sector", _MirrorSectorWithDefenses)

        S = session_factory()
        destination_sector = (
            S.query(_MirrorSectorWithDefenses).filter(_MirrorSectorWithDefenses.sector_id == 1301).first()
        )
        assert destination_sector.defenses["mines"] == 3

        player = _make_mover(uuid.uuid4())
        service = MovementService(S)

        # THE REAL mine-detonation mutation -- in-memory, unflushed.
        service._detonate_sector_mines(player, destination_sector)
        assert destination_sector.defenses["mines"] == 2

        # NO flush (the pre-fix shape). THE REAL presence-lock re-read.
        service._update_player_presence(player, 1301, 1301)

        assert destination_sector.defenses["mines"] == 3, (
            "harness sanity check failed -- without the flush, "
            "populate_existing() must revert the in-memory mine decrement "
            "back to the persisted value 3, proving the bug this WO fixes"
        )
        S.close()

    def test_flush_before_populate_existing_preserves_mine_detonation(
        self, monkeypatch,
    ) -> None:
        """THE FIX: flushing immediately before `_update_player_presence`
        (mirroring the `self.db.flush()` now at movement_service.py ~:2375)
        persists the mine decrement first, so the populate_existing()
        re-read observes it instead of reverting it."""
        owner_id = uuid.uuid4()
        session_factory = _mine_session_factory()
        self._seed(session_factory, mines=3, owner_id=owner_id)

        monkeypatch.setattr(movement_service_module, "Sector", _MirrorSectorWithDefenses)

        S = session_factory()
        destination_sector = (
            S.query(_MirrorSectorWithDefenses).filter(_MirrorSectorWithDefenses.sector_id == 1301).first()
        )
        player = _make_mover(uuid.uuid4())
        service = MovementService(S)

        service._detonate_sector_mines(player, destination_sector)
        assert destination_sector.defenses["mines"] == 2

        S.flush()  # THE FIX
        service._update_player_presence(player, 1301, 1301)

        assert destination_sector.defenses["mines"] == 2  # PRESERVED
        assert destination_sector.defenses["mine_owner_id"] == str(owner_id)
        S.close()

    def test_flush_before_populate_existing_preserves_depletion_clearing(
        self, monkeypatch,
    ) -> None:
        """The same fix, at the depletion boundary: the LAST mine detonating
        also clears `mine_owner_id`/`mine_team_id` (movement_service.py
        :2103-2106) -- that clear must survive the presence lock too, not
        just the bare mine-count decrement."""
        owner_id = uuid.uuid4()
        session_factory = _mine_session_factory()
        self._seed(session_factory, mines=1, owner_id=owner_id)

        monkeypatch.setattr(movement_service_module, "Sector", _MirrorSectorWithDefenses)

        S = session_factory()
        destination_sector = (
            S.query(_MirrorSectorWithDefenses).filter(_MirrorSectorWithDefenses.sector_id == 1301).first()
        )
        player = _make_mover(uuid.uuid4())
        service = MovementService(S)

        service._detonate_sector_mines(player, destination_sector)
        assert destination_sector.defenses["mines"] == 0
        assert destination_sector.defenses["mine_owner_id"] is None

        S.flush()  # THE FIX
        service._update_player_presence(player, 1301, 1301)

        assert destination_sector.defenses["mines"] == 0
        assert destination_sector.defenses["mine_owner_id"] is None
        assert destination_sector.defenses["mine_team_id"] is None
        S.close()


@pytest.mark.unit
class TestExecuteMovementFlushesImmediatelyBeforePresenceLock:
    """Part D -- structural AST pin: proves the FIX IS WIRED into
    `_execute_movement` at the right place, independent of Part C's
    mechanism proof (which calls the two methods directly and would not
    notice if the flush were removed from -- or moved elsewhere in --
    `_execute_movement` itself). Mirrors test_movement_core_pins.py's own
    `inspect.getsource` + `ast` idiom for this file's sibling method."""

    def test_flush_is_the_statement_immediately_before_update_player_presence(self) -> None:
        source = textwrap.dedent(inspect.getsource(MovementService._execute_movement))
        tree = ast.parse(source)
        func = tree.body[0]
        assert isinstance(func, ast.FunctionDef)
        assert func.name == "_execute_movement"

        def dotted(node: ast.AST) -> str:
            parts: list[str] = []
            while isinstance(node, ast.Attribute):
                parts.append(node.attr)
                node = node.value
            if isinstance(node, ast.Name):
                parts.append(node.id)
            return ".".join(reversed(parts))

        def top_level_call_name(stmt: ast.stmt) -> str | None:
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                return dotted(stmt.value.func)
            return None

        call_names = [top_level_call_name(stmt) for stmt in func.body]
        assert call_names.count("self._update_player_presence") == 1
        presence_idx = call_names.index("self._update_player_presence")

        assert call_names[presence_idx - 1] == "self.db.flush", (
            "the top-level statement immediately before "
            "_update_player_presence(...) must be a bare self.db.flush() -- "
            "it persists any pending pre-lock Sector mutation (e.g. mine "
            "detonation) so the presence lock's populate_existing() "
            "re-read observes it instead of reverting it. Got: "
            f"{call_names[max(0, presence_idx - 1)]!r}"
        )

        # Sanity: this must be a DIFFERENT flush than the ARIA-exploration-
        # map hook's first-visit-only one (~:2314) -- that one is nested
        # inside a try/except, not a top-level statement, so it never
        # appears in call_names at all; a regression collapsing back to
        # relying on it alone would show presence_idx - 1 pointing at
        # something else entirely (caught by the assertion above).
        assert call_names.count("self.db.flush") == 1
