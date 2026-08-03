"""WO-K2b — the four relocation routes that bypass ``move_player_to_sector``
must be scanned too, and must be scanned WITHOUT owning the transaction.

WHY THIS EXISTS. WO-K2 wired a contraband customs scan into all three
``move_player_to_sector`` success branches. Four other services relocate a player
without going through it, each a clean unscanned border crossing for a loaded
smuggler:

* ``quantum_service.jump`` and ``slipdrive_service.complete_charge`` — teleports
  that, per their own comments, mirror ``_execute_movement``'s state sync
  "WITHOUT the adjacency requirement";
* ``hangar_service.carry_hangared_ships`` — dock in a friendly Carrier, cross,
  undock;
* ``tow_service.carry_towed_ship`` — get towed across at 0 turns.

(``distress_service`` / ``escape_pod_service`` also relocate but stay exempt:
those are involuntary rescues.)

THE TRANSACTION SHAPE IS THE RISK, AND IT IS THE OPPOSITE OF K2's.
In the movement hook, ``_execute_movement`` has already committed, so that hook
must own its transaction. All four sites here are reached with the host
transaction still OPEN — ``carry_hangared_ships`` and ``carry_towed_ship`` say so
in their docstrings ("Does NOT commit"), ``complete_charge`` flushes and lets the
route commit, and the quantum arrival is written before ``jump``'s own commit. So
a ``commit()`` at any of these sites would publish a HALF-FINISHED relocation, and
a ``rollback()`` would discard the relocation outright.

These tests pin that contract mechanically, because it is invisible by reading:
the scan must be flush-only, savepoint-scoped, and unable to raise.
"""
import inspect
import re

import pytest

from src.services import contraband_service
from src.services.contraband_service import scan_in_transit_best_effort

WIRED_SITES = [
    ("hangar_service", "carry_hangared_ships"),
    ("tow_service", "carry_towed_ship"),
    ("quantum_service", "jump"),
    ("slipdrive_service", "complete_charge"),
]


def _source(module_name, func_name):
    mod = __import__(f"src.services.{module_name}", fromlist=[module_name])
    obj = getattr(mod, func_name, None)
    if obj is None:  # method on the module's service class
        for _, cls in vars(mod).items():
            if inspect.isclass(cls) and hasattr(cls, func_name):
                obj = getattr(cls, func_name)
                break
    assert obj is not None, f"{module_name}.{func_name} not found"
    return inspect.getsource(obj)


class RecordingSession:
    """Records transaction-control calls; begin_nested is a working no-op CM."""

    def __init__(self):
        self.calls = []

    def commit(self):
        self.calls.append("commit")

    def rollback(self):
        self.calls.append("rollback")

    def begin_nested(self):
        self.calls.append("begin_nested")
        session = self

        class _CM:
            def __enter__(self):
                return None

            def __exit__(self, *exc):
                session.calls.append("savepoint_exit")
                return False

        return _CM()


class TestAllFourRoutesAreWired:
    @pytest.mark.parametrize("module_name,func_name", WIRED_SITES)
    def test_route_calls_the_scan(self, module_name, func_name):
        """A smuggler must not be able to pick a relocation mechanism that skips
        customs. Source-level so deleting a call site fails here."""
        assert "scan_in_transit_best_effort(" in _source(module_name, func_name), (
            f"{module_name}.{func_name} no longer runs a transit scan — that "
            f"reopens a contraband bypass route"
        )

    @pytest.mark.parametrize(
        "module_name,func_name",
        [s for s in WIRED_SITES if s[0] != "quantum_service"],
    )
    def test_flush_only_route_never_commits_at_all(self, module_name, func_name):
        """THE core K2b contract for the three flush-only routes. Each is reached
        with a transaction someone else owns still open, so a commit here
        publishes a half-finished relocation and a rollback discards it
        outright."""
        src = _source(module_name, func_name)
        assert "commit()" not in src, (
            f"{func_name} is flush-only by contract (its own docstring says so) "
            f"— adding a commit would publish a half-finished relocation"
        )
        assert "rollback()" not in src, (
            f"{func_name} must not roll back a transaction it does not own"
        )

    def test_quantum_commits_exactly_once_and_only_after_the_scan(self):
        """``jump()`` is the one site that owns its transaction. Its commit is
        pre-existing and correct — but it must come AFTER the scan, which is what
        makes a bust atomic with the jump that caused it. If the scan ever moved
        below the commit, a busted smuggler would keep the cargo on a crash."""
        src = _source("quantum_service", "jump")
        commits = [m.start() for m in re.finditer(r"\bdb\.commit\(\)", src)]
        assert len(commits) == 1, f"expected jump()'s single pre-existing commit, got {len(commits)}"
        assert "rollback()" not in src
        assert src.index("scan_in_transit_best_effort(") < commits[0], (
            "the scan must run BEFORE jump()'s commit so the bust is atomic "
            "with the jump"
        )

    @pytest.mark.parametrize(
        "module_name,func_name", [s for s in WIRED_SITES if s[0].endswith(("hangar_service", "tow_service"))]
    )
    def test_ride_along_snapshots_origin_before_overwriting_it(self, module_name, func_name):
        """Both ride-alongs assign ``pilot.current_sector_id = destination`` inline.
        The origin must be captured ABOVE that line or the scan compares a sector
        against itself and silently never fires."""
        src = _source(module_name, func_name)
        snap = src.index("pilot_origin_sector_id = pilot.current_sector_id")
        overwrite = src.index("pilot.current_sector_id = destination_sector_id")
        assert snap < overwrite, (
            f"{func_name}: origin snapshot must precede the overwrite"
        )

    def test_hangar_scans_each_passenger_not_once_per_carrier(self):
        """A Carrier full of smugglers is not one scan. The call must sit inside
        the per-passenger loop, or the hangar becomes the cheap way to run
        contraband."""
        src = _source("hangar_service", "carry_hangared_ships")
        loop = src.index('for entry in hangar["docked"]')
        call = src.index("scan_in_transit_best_effort(")
        assert loop < call, "the scan must be inside the per-passenger loop"

    def test_involuntary_rescue_routes_stay_exempt(self):
        """distress/escape-pod relocate a player too, but being rescued is not a
        smuggling run. Pinned so a later sweep doesn't 'helpfully' add them."""
        for module_name in ("distress_service", "escape_pod_service"):
            mod = __import__(f"src.services.{module_name}", fromlist=[module_name])
            assert "scan_in_transit" not in inspect.getsource(mod), (
                f"{module_name} must stay exempt — involuntary rescue is not a "
                f"border run"
            )


class TestBestEffortWrapperContract:
    def test_uses_a_savepoint(self, monkeypatch):
        """Without begin_nested, a raise partway through a bust would leave a
        half-applied confiscation in the caller's session for its owner to commit
        unknowingly."""
        session = RecordingSession()
        monkeypatch.setattr(
            contraband_service.ContrabandService, "scan_in_transit",
            lambda self, **kw: {"scanned": False, "locked": False, "reason": "no_contraband"},
        )
        scan_in_transit_best_effort(session, player=object(), ship_id=None,
                                    origin_sector_id=1, destination_sector_id=2)
        assert "begin_nested" in session.calls
        assert "commit" not in session.calls
        assert "rollback" not in session.calls

    def test_never_raises_into_the_relocation(self, monkeypatch):
        """A contraband roll must not be able to strand a jump or a carry."""
        session = RecordingSession()

        def _boom(self, **kw):
            raise RuntimeError("simulated scan failure")

        monkeypatch.setattr(
            contraband_service.ContrabandService, "scan_in_transit", _boom
        )
        result = scan_in_transit_best_effort(
            session, player=object(), ship_id=None,
            origin_sector_id=1, destination_sector_id=2,
        )
        assert result is None
        assert "commit" not in session.calls
        assert "rollback" not in session.calls, (
            "a failed scan must unwind via its SAVEPOINT, never by rolling back "
            "the caller's transaction"
        )

    def test_passes_the_outcome_through(self, monkeypatch):
        session = RecordingSession()
        outcome = {"scanned": True, "detected": True, "fine": 900}
        monkeypatch.setattr(
            contraband_service.ContrabandService, "scan_in_transit",
            lambda self, **kw: outcome,
        )
        assert scan_in_transit_best_effort(
            session, player=object(), ship_id=None,
            origin_sector_id=1, destination_sector_id=2,
        ) == outcome


def test_no_bypass_route_was_missed():
    """Every service that assigns a player's sector is either wired or on the
    documented exempt list. Catches a NEW relocation path added later, which is
    exactly how this gap appeared in the first place."""
    import pathlib

    wired = {"hangar_service", "tow_service", "quantum_service", "slipdrive_service"}
    exempt = {
        "movement_service",      # the original K2 hook lives here
        "distress_service",      # involuntary rescue
        "escape_pod_service",    # involuntary rescue
        "npc_engagement_service", "npc_spawn_service",  # NPCs, not players
        "npc_tick_loops",
    }
    services = pathlib.Path(__file__).resolve().parents[2] / "src" / "services"
    offenders = []
    for path in list(services.rglob("*.py")):
        if path.stem in wired or path.stem in exempt:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"^\s*(?!#).*\bplayer\.current_sector_id\s*=\s*(?!=)", text, re.M):
            offenders.append(path.stem)
    assert not offenders, (
        f"these services relocate a player but are neither scanned nor listed "
        f"exempt: {sorted(set(offenders))} — wire them or add them to `exempt` "
        f"with a reason"
    )
