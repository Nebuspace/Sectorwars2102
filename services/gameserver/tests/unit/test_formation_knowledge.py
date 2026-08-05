"""WO-GWQ-FORMATION-KNOWLEDGE — per-player special-formation discovery
(ADR-0045), replacing ``SpecialFormation.is_discovered`` as the ONLY
discovery gate with a ``player_formation_knowledge`` table so one player's
visit no longer reveals a formation's identity to every other player.

DB-free: hand-built fakes, no real DB/app (mirrors test_warp_gate_toll.py /
test_bounty_service_nh2.py / medal_service's race pattern). ``find_formations
_for_sector`` (an unchanged, pre-existing lookup outside this WO's diff) is
monkeypatched to return a controlled formation set — the real target under
test is ``flip_formation_discovery``, ``is_formation_known_to_player``, and
``investigate_formation``'s per-player gate. ``db.query(PlayerFormationKnowl
edge)`` is a genuine fake-query-filter-interpreter: it walks the real
SQLAlchemy ``BinaryExpression`` objects the service builds
(``PlayerFormationKnowledge.player_id == player_id`` etc.) and evaluates them
against a live in-memory row list, so the read path is actually exercised,
not canned-returned.

Acceptance-criteria map (WO-GWQ-FORMATION-KNOWLEDGE, 9 total):
  1  TestFirstVisitRecordsKnowledge::test_a_visits_creates_one_row_and_flips_global
  2  TestPerPlayerReadPath::test_b_view_reads_undiscovered_before_b_visits
  3  TestInvestigatePrePlayerVisit::test_b_investigate_pre_visit_exact_error_string
  4  TestIndependentPerPlayerRows::test_b_visits_a_untouched_both_read_independently
  5  TestIdempotentRevisit::test_same_player_revisit_one_row_zero_new
  6  TestConcurrentRace::test_concurrent_double_visit_one_row_survives_no_integrity_error
  7  TestMigrationAdditiveOnly (module-level, file-text/AST based)
  8  TestNameBackfill::test_name_backfilled_from_properties_on_first_discovery
  9  TestLegacyConsumersRegressionPinned (grep/AST based, incl. investigate's
     legacy 404 path + confirms no admin route reads SpecialFormation at all)
"""
from __future__ import annotations

import ast
import pathlib
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import flag_modified

from src.models.special_formation import (
    SpecialFormation,
    SpecialFormationType,
    PlayerFormationKnowledge,
    FormationRevealedVia,
)
from src.services import special_formation_service as sfs
from src.services.special_formation_service import (
    flip_formation_discovery,
    is_formation_known_to_player,
    investigate_formation,
    is_formation_investigated,
    FormationNotDiscoveredError,
    FormationAlreadyInvestigatedError,
)


# --- shared fakes -------------------------------------------------------- #


class _FakeFormationQuery:
    """Stands in for ``db.query(SpecialFormation)`` inside
    ``investigate_formation``'s single by-id lookup
    (``.filter(...).populate_existing().with_for_update().first()``). The
    test controls the scenario by presetting ``first``; filter/lock calls are
    no-ops (mirrors test_warp_gate_toll.py's ``_FakeQuery`` convention)."""

    def __init__(self, first: Optional[SpecialFormation] = None) -> None:
        self._first = first

    def filter(self, *a: Any, **k: Any) -> "_FakeFormationQuery":
        return self

    def populate_existing(self) -> "_FakeFormationQuery":
        return self

    def with_for_update(self, *a: Any, **k: Any) -> "_FakeFormationQuery":
        return self

    def first(self) -> Optional[SpecialFormation]:
        return self._first


class _FakeKnowledgeQuery:
    """Stands in for ``db.query(PlayerFormationKnowledge)``. Interprets the
    REAL two-clause filter (``player_id ==``, ``formation_id ==``) the
    service builds, by walking each ``BinaryExpression``'s
    ``.left.key`` / ``.right.value`` — genuinely exercises
    ``is_formation_known_to_player``'s read logic against a live row list
    rather than a canned return (fake-query-filter-interpreter pattern)."""

    def __init__(self, rows: List[PlayerFormationKnowledge]) -> None:
        self._rows = rows
        self._player_id = None
        self._formation_id = None

    def filter(self, *conds: Any) -> "_FakeKnowledgeQuery":
        for cond in conds:
            left = getattr(cond, "left", None)
            right = getattr(cond, "right", None)
            key = getattr(left, "key", None)
            val = getattr(right, "value", None)
            if key == "player_id":
                self._player_id = val
            elif key == "formation_id":
                self._formation_id = val
        return self

    def first(self) -> Optional[PlayerFormationKnowledge]:
        for row in self._rows:
            if row.player_id == self._player_id and row.formation_id == self._formation_id:
                return row
        return None


class _FakeSession:
    """Minimal in-memory Session stand-in.

    ``rows`` is the durable "committed" PlayerFormationKnowledge store read
    by ``_FakeKnowledgeQuery``. ``add()``/``flush()`` mirror real SQLAlchemy:
    ``add`` only stages; ``flush`` is what would issue the INSERT and is
    where a UNIQUE violation actually surfaces. ``race_on_formation_id``, when
    set, simulates a concurrent session's INSERT for that exact
    (player, formation) landing between our pre-check and our own flush: the
    first flush for that formation raises IntegrityError (fires once) AND
    makes the "winner"'s row visible to subsequent reads — proving the loser
    never double-inserts and the winner's row survives.
    """

    def __init__(self, *, race_on_formation_id: Any = None, formation_query_first: Optional[SpecialFormation] = None) -> None:
        self.rows: List[PlayerFormationKnowledge] = []
        self._pending: List[Any] = []
        self._race_on_formation_id = race_on_formation_id
        self.formation_query_first = formation_query_first
        self.flush_count = 0
        self.committed = False
        self.refreshed: List[Any] = []

    def query(self, model: Any) -> Any:
        if model is PlayerFormationKnowledge:
            return _FakeKnowledgeQuery(self.rows)
        if model is SpecialFormation:
            return _FakeFormationQuery(self.formation_query_first)
        raise AssertionError(f"unexpected query for {model!r}")

    def add(self, obj: Any) -> None:
        self._pending.append(obj)

    def flush(self) -> None:
        self.flush_count += 1
        still_pending = []
        for obj in self._pending:
            if isinstance(obj, PlayerFormationKnowledge):
                if obj.formation_id == self._race_on_formation_id:
                    # Simulate the concurrent winner's commit becoming visible,
                    # then our own INSERT colliding with it.
                    self._race_on_formation_id = None  # only races once
                    winner = PlayerFormationKnowledge(
                        player_id=obj.player_id,
                        formation_id=obj.formation_id,
                        revealed_via=FormationRevealedVia.VISIT,
                    )
                    self.rows.append(winner)
                    self._pending = []
                    raise IntegrityError("INSERT", {}, Exception("duplicate key value violates unique constraint"))
                dup = any(
                    r.player_id == obj.player_id and r.formation_id == obj.formation_id
                    for r in self.rows
                )
                if dup:
                    self._pending = []
                    raise IntegrityError("INSERT", {}, Exception("duplicate key value violates unique constraint"))
                self.rows.append(obj)
            else:
                still_pending.append(obj)
        self._pending = still_pending

    @contextmanager
    def begin_nested(self):
        yield

    def commit(self) -> None:
        self.committed = True

    def refresh(self, obj: Any) -> None:
        self.refreshed.append(obj)


def make_formation(*, is_discovered: bool = False, name: Optional[str] = None, props: Optional[dict] = None) -> SpecialFormation:
    return SpecialFormation(
        id=uuid.uuid4(),
        region_id=uuid.uuid4(),
        type=SpecialFormationType.BUBBLE,
        anchor_sector_id=uuid.uuid4(),
        interior_sector_ids=[],
        properties=props if props is not None else {},
        is_discovered=is_discovered,
        name=name,
    )


def make_player(credits: int = 1000) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), credits=credits)


def make_sector() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), sector_id=42)


@pytest.fixture(autouse=True)
def _noop_flag_modified(monkeypatch):
    """SpecialFormation instances here are real mapped ORM objects (not
    session-attached), so flag_modified is safe to call for real — but
    monkeypatched to a no-op anyway to match the established sibling
    convention (test_bounty_service_nh2.py) and keep these tests independent
    of SQLAlchemy's instrumentation internals."""
    monkeypatch.setattr(sfs, "flag_modified", lambda *a, **k: None)


@pytest.fixture
def one_formation(monkeypatch):
    """Seeds find_formations_for_sector to return exactly one formation —
    isolates flip_formation_discovery's own logic from the pre-existing,
    unchanged anchor/interior lookup."""
    formation = make_formation()

    def _fake_find(db, sector):
        return [formation]

    monkeypatch.setattr(sfs, "find_formations_for_sector", _fake_find)
    return formation


# --- Accept #1: first visit records knowledge + flips global aggregate --- #


class TestFirstVisitRecordsKnowledge:
    def test_a_visits_creates_one_row_and_flips_global(self, one_formation):
        formation = one_formation
        assert formation.is_discovered is False

        db = _FakeSession()
        player_a = make_player()
        sector = make_sector()

        newly = flip_formation_discovery(db, player_a, sector)

        assert newly == 1
        assert len(db.rows) == 1
        row = db.rows[0]
        assert row.player_id == player_a.id
        assert row.formation_id == formation.id
        assert row.revealed_via == FormationRevealedVia.VISIT
        # Global aggregate still flips on first-ever discovery.
        assert formation.is_discovered is True
        assert db.flush_count >= 1


# --- Accept #2: per-player read path — B's view answers from B's row ---- #


class TestPerPlayerReadPath:
    def test_b_view_reads_undiscovered_before_b_visits(self, one_formation):
        formation = one_formation
        db = _FakeSession()
        player_a = make_player()
        player_b = make_player()
        sector = make_sector()

        flip_formation_discovery(db, player_a, sector)

        # Global aggregate is True (A discovered it), but B's own read must
        # be False — this is the cross-player leak WO-GWQ-FORMATION-KNOWLEDGE
        # closes: the global flag alone would have wrongly disclosed it to B.
        assert formation.is_discovered is True
        assert is_formation_known_to_player(db, player_a.id, formation.id) is True
        assert is_formation_known_to_player(db, player_b.id, formation.id) is False


# --- Accept #3: investigate pre-visit — exact existing error string ----- #


class TestInvestigatePrePlayerVisit:
    def test_b_investigate_pre_visit_exact_error_string(self, one_formation):
        formation = one_formation
        db = _FakeSession()
        player_a = make_player()
        player_b = make_player()
        sector = make_sector()

        # A discovers it globally; B never visits.
        flip_formation_discovery(db, player_a, sector)
        db.formation_query_first = formation

        with pytest.raises(FormationNotDiscoveredError) as exc_info:
            investigate_formation(db, player_b, formation.id)

        # Exact string pinned — the route maps this to 404 verbatim.
        assert str(exc_info.value) == "Formation not found or not yet discovered."
        # No credits granted, no commit — the gate rejected before any mutation.
        assert player_b.credits == 1000
        assert db.committed is False


# --- Accept #4: independent per-player rows ------------------------------ #


class TestIndependentPerPlayerRows:
    def test_b_visits_a_untouched_both_read_independently(self, one_formation):
        formation = one_formation
        db = _FakeSession()
        player_a = make_player()
        player_b = make_player()
        sector = make_sector()

        flip_formation_discovery(db, player_a, sector)
        newly_for_b = flip_formation_discovery(db, player_b, sector)

        assert newly_for_b == 1
        assert len(db.rows) == 2
        a_row = next(r for r in db.rows if r.player_id == player_a.id)
        b_row = next(r for r in db.rows if r.player_id == player_b.id)
        assert a_row.formation_id == formation.id
        assert b_row.formation_id == formation.id
        assert a_row.player_id != b_row.player_id
        # Both now read independently as known.
        assert is_formation_known_to_player(db, player_a.id, formation.id) is True
        assert is_formation_known_to_player(db, player_b.id, formation.id) is True


# --- Accept #5: idempotent same-player revisit --------------------------- #


class TestIdempotentRevisit:
    def test_same_player_revisit_one_row_zero_new(self, one_formation):
        formation = one_formation
        db = _FakeSession()
        player_a = make_player()
        sector = make_sector()

        first = flip_formation_discovery(db, player_a, sector)
        second = flip_formation_discovery(db, player_a, sector)

        assert first == 1
        assert second == 0
        assert len(db.rows) == 1


# --- Accept #6: concurrent double-visit — no IntegrityError escapes ------ #


class TestConcurrentRace:
    def test_concurrent_double_visit_one_row_survives_no_integrity_error(self, one_formation):
        formation = one_formation
        player_a = make_player()
        sector = make_sector()

        db = _FakeSession(race_on_formation_id=formation.id)

        # Must not raise — the SAVEPOINT-scoped insert catches the
        # IntegrityError and treats it as an already-known no-op.
        newly = flip_formation_discovery(db, player_a, sector)

        assert newly == 0  # lost the race; not counted as a new discovery
        assert len(db.rows) == 1  # exactly the "winner"'s row survives
        assert db.rows[0].player_id == player_a.id
        assert db.rows[0].formation_id == formation.id
        # The formation is correctly known post-race despite our own loss.
        assert is_formation_known_to_player(db, player_a.id, formation.id) is True


# --- Accept #8: name back-fill byte-identical on first discovery --------- #


class TestNameBackfill:
    def test_name_backfilled_from_properties_on_first_discovery(self, monkeypatch):
        formation = make_formation(is_discovered=False, name=None, props={"name": "Bubble of the Lost Star"})

        def _fake_find(db, sector):
            return [formation]

        monkeypatch.setattr(sfs, "find_formations_for_sector", _fake_find)

        db = _FakeSession()
        player_a = make_player()
        sector = make_sector()

        flip_formation_discovery(db, player_a, sector)

        assert formation.name == "Bubble of the Lost Star"
        assert formation.is_discovered is True

    def test_name_not_overwritten_if_already_set(self, monkeypatch):
        formation = make_formation(is_discovered=False, name="Existing Name", props={"name": "Different JSONB Name"})

        def _fake_find(db, sector):
            return [formation]

        monkeypatch.setattr(sfs, "find_formations_for_sector", _fake_find)

        db = _FakeSession()
        player_a = make_player()
        sector = make_sector()

        flip_formation_discovery(db, player_a, sector)

        assert formation.name == "Existing Name"


# --- Investigate happy path (supporting coverage, not its own numbered AC) #


class TestInvestigateHappyPath:
    def test_player_who_discovered_can_investigate(self, one_formation):
        formation = one_formation
        db = _FakeSession()
        player_a = make_player(credits=1000)
        sector = make_sector()

        flip_formation_discovery(db, player_a, sector)
        db.formation_query_first = formation

        payload = investigate_formation(db, player_a, formation.id)

        assert payload["formation"]["is_discovered"] is True
        assert payload["formation"]["is_investigated"] is True
        assert payload["reward"]["credits"] > 0
        assert player_a.credits == 1000 + payload["reward"]["credits"]
        assert db.committed is True
        assert is_formation_investigated(formation) is True

    def test_repeat_investigate_by_same_player_conflicts(self, one_formation):
        formation = one_formation
        db = _FakeSession()
        player_a = make_player()
        sector = make_sector()

        flip_formation_discovery(db, player_a, sector)
        db.formation_query_first = formation

        investigate_formation(db, player_a, formation.id)

        with pytest.raises(FormationAlreadyInvestigatedError):
            investigate_formation(db, player_a, formation.id)


# --- Accept #7: migration is additive-only -------------------------------- #

_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "fea17cc334a8_add_player_formation_knowledge.py"
)


@pytest.mark.unit
class TestMigrationAdditiveOnly:
    def test_migration_file_exists(self) -> None:
        assert _MIGRATION_PATH.is_file()

    def test_upgrade_only_creates_new_table_index_and_enum(self) -> None:
        source = _MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        upgrade_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade")
        upgrade_src = ast.get_source_segment(source, upgrade_fn) or ""
        assert upgrade_src.count("op.create_table(") == 1
        assert upgrade_src.count("op.create_index(") == 1
        for banned in ("op.alter_column", "op.drop_column", "op.add_column", "op.drop_table", "op.drop_index"):
            assert banned not in upgrade_src

    def test_down_revision_is_the_current_head(self) -> None:
        source = _MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        assigns = {
            n.targets[0].id: n.value.value
            for n in tree.body
            if isinstance(n, ast.Assign)
            and isinstance(n.targets[0], ast.Name)
            and isinstance(n.value, ast.Constant)
        }
        assert assigns.get("down_revision") == "a3f9e1c74b28"
        assert assigns.get("revision") == "fea17cc334a8"


# --- Accept #9: legacy/global consumers regression-pinned ----------------- #


@pytest.mark.unit
class TestLegacyConsumersRegressionPinned:
    """Structural regression guards: grep the disclosure-relevant readers of
    ``SpecialFormation.is_discovered`` and pin that they now route through
    the per-player gate, not the global flag."""

    _PLAYER_ROUTE_PATH = (
        pathlib.Path(__file__).resolve().parents[2]
        / "src" / "api" / "routes" / "player.py"
    )
    _SERVICE_PATH = (
        pathlib.Path(__file__).resolve().parents[2]
        / "src" / "services" / "special_formation_service.py"
    )

    def test_player_route_disclosure_no_longer_reads_global_flag(self) -> None:
        source = self._PLAYER_ROUTE_PATH.read_text()
        # The two disclosure call sites (current-sector view, neighbour
        # listing) must both route through the per-player gate.
        assert source.count("is_formation_known_to_player(db, player.id, f.id)") == 2
        # The old global-flag disclosure read must be gone.
        assert "bool(f.is_discovered)" not in source

    def test_investigate_gate_no_longer_reads_global_flag_directly(self) -> None:
        source = self._SERVICE_PATH.read_text()
        assert "is_formation_known_to_player(db, player.id, formation_id)" in source
        # The old investigate-gate condition (reading the global flag
        # directly) must be gone — narrowly scoped to that exact expression
        # so it doesn't false-positive on the still-legitimate global-flip
        # check inside flip_formation_discovery ("if not formation.is_discov
        # ered:", which flips the first-ever-discovery aggregate and is
        # intentionally unchanged).
        assert "formation is None or not formation.is_discovered:" not in source
        # The exact legacy error string is still pinned verbatim.
        assert '"Formation not found or not yet discovered."' in source

    def test_global_flag_write_and_investigate_response_field_still_present(self) -> None:
        """The global aggregate is still WRITTEN (first-ever-discovery flag)
        and still read back in investigate_formation's response payload
        (always True there by invariant — see module docstring) — this is
        the intentionally-unchanged legacy surface, not a regression."""
        source = self._SERVICE_PATH.read_text()
        assert "formation.is_discovered = True" in source
        assert '"is_discovered": bool(formation.is_discovered)' in source

    def test_no_admin_route_reads_special_formation(self) -> None:
        """No admin route currently touches SpecialFormation at all (grep-
        confirmed at WO authoring time) — pinned so a future admin formation
        view is written against the per-player table, not the global flag."""
        admin_dir = pathlib.Path(__file__).resolve().parents[2] / "src" / "api" / "routes"
        offenders = []
        for path in admin_dir.glob("admin*.py"):
            if "SpecialFormation" in path.read_text():
                offenders.append(path.name)
        assert offenders == []
