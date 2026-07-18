"""QUEUE-REGISTRY-PILOT-WIRING (2026-07-16): ships.current_pilot_id was
NULL on every actively-piloted live ship -- the Ship Registry foundation
(WO-P10-green-ship-registry-schema) shipped the column but zero write
sites ever set it. Invariant (SYSTEMS/ship-registry.md #4, the canonical
eject/board pair): ``Ship.current_pilot_id == player.id`` iff
``Player.current_ship_id == ship.id``.

Fix: ``ship_service.sync_current_pilot`` is the ONE function that
maintains this invariant, called from every ``player.current_ship_id =
...`` write site in the codebase (grep-confirmed exhaustive at fix time --
see the structural sweep test below, which trips if a FUTURE write site
forgets to pair the call, per this codebase's rename-sweep-readers
discipline applied to write sites instead of readers)."""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.services.ship_service import sync_current_pilot

_SRC = Path(__file__).resolve().parents[2] / "src"


def _ship(pid=None):
    return SimpleNamespace(id=uuid.uuid4(), current_pilot_id=pid)


@pytest.mark.unit
class TestSyncCurrentPilotBehavior:
    def test_no_old_ship_sets_new_ships_pointer(self) -> None:
        """Brand-new player / first ship ever -- oauth.py, ship_upgrades.py
        purchase branch, first_login_service.py."""
        player = SimpleNamespace(id=uuid.uuid4())
        new_ship = _ship()

        sync_current_pilot(player, new_ship)

        assert new_ship.current_pilot_id == player.id

    def test_switching_away_clears_the_old_ship_and_sets_the_new_one(self) -> None:
        """The core switch-ship case -- ship_upgrades.py's /set-active
        endpoint, hangar_service.py jettison, escape_pod_service.py,
        ship_service.destroy_ship."""
        player = SimpleNamespace(id=uuid.uuid4())
        old_ship = _ship(pid=player.id)
        new_ship = _ship()

        sync_current_pilot(player, new_ship, old_ship=old_ship)

        assert old_ship.current_pilot_id is None
        assert new_ship.current_pilot_id == player.id

    def test_pure_eject_clears_old_ship_with_no_replacement(self) -> None:
        """new_ship=None -- a pure eject to no hull at all."""
        player = SimpleNamespace(id=uuid.uuid4())
        old_ship = _ship(pid=player.id)

        sync_current_pilot(player, None, old_ship=old_ship)

        assert old_ship.current_pilot_id is None

    def test_same_ship_reassignment_is_a_safe_noop_not_a_clobber(self) -> None:
        """old_ship is new_ship (idempotent re-assignment) -- must NOT wipe
        the pointer it's about to set right back to it (a naive
        clear-then-set implementation would transiently null it, or --
        worse -- a caller passing old_ship=new_ship by mistake must not
        de-pilot the ship it's simultaneously piloting)."""
        player = SimpleNamespace(id=uuid.uuid4())
        ship = _ship(pid=player.id)

        sync_current_pilot(player, ship, old_ship=ship)

        assert ship.current_pilot_id == player.id

    def test_admin_delete_clears_the_pointer_before_the_row_is_dropped(self) -> None:
        """admin_comprehensive.py's delete_ship route -- new_ship=None,
        old_ship=the ship about to be db.delete()d. Moot on a real DB
        (deletion removes the row) but the in-memory attribute must still
        clear correctly for this function's own contract."""
        owner = SimpleNamespace(id=uuid.uuid4())
        ship = _ship(pid=owner.id)

        sync_current_pilot(owner, None, old_ship=ship)

        assert ship.current_pilot_id is None

    def test_neither_ship_provided_is_a_total_noop(self) -> None:
        player = SimpleNamespace(id=uuid.uuid4())
        sync_current_pilot(player, None, old_ship=None)  # must not raise


@pytest.mark.unit
class TestEveryCurrentShipIdWriteSitePairsWithSyncCurrentPilot:
    """Structural sweep (2026-07-16): every ``player.current_ship_id =
    <value>`` assignment in src/ must be paired with a ``sync_current_
    pilot(`` call in the SAME file, in EQUAL count -- a future write site
    that forgets the pairing trips this test instead of shipping the
    invariant silently broken again. Per-file counts (not just a global
    total) so an unrelated write+call pair in one file can't mask a
    missing pair in another."""

    # {relative path under src/: expected (write_count, sync_call_count)}
    _EXPECTED = {
        "auth/oauth.py": (1, 1),
        "api/routes/admin_comprehensive.py": (1, 1),
        "services/hangar_service.py": (1, 1),
        "api/routes/ship_upgrades.py": (2, 2),
        "services/station_security_service.py": (1, 1),
        "services/escape_pod_service.py": (1, 1),
        "services/first_login_service.py": (1, 1),
        # ship_service.py DEFINES sync_current_pilot -- the def line itself
        # matches the sync-call regex, so 1 real call there = 2 raw matches.
        "services/ship_service.py": (1, 2),
    }

    _WRITE_RE = re.compile(r"\.current_ship_id\s*=(?!=)")
    _SYNC_RE = re.compile(r"sync_current_pilot\(")

    def test_known_write_sites_all_pair_with_a_sync_call(self) -> None:
        for rel_path, (expected_writes, expected_syncs) in self._EXPECTED.items():
            text = (_SRC / rel_path).read_text()
            writes = len(self._WRITE_RE.findall(text))
            syncs = len(self._SYNC_RE.findall(text))
            assert writes == expected_writes, f"{rel_path}: expected {expected_writes} current_ship_id writes, found {writes}"
            assert syncs == expected_syncs, f"{rel_path}: expected {expected_syncs} sync_current_pilot calls, found {syncs}"

    def test_no_current_ship_id_write_sites_outside_the_known_set(self) -> None:
        """Whole-tree sweep: every file containing a `.current_ship_id =`
        write assignment must be in the known-set above. A NEW write site
        in an unlisted file trips this immediately -- forcing a deliberate
        decision (pair it + add it here) instead of a silent gap."""
        known_files = {_SRC / p for p in self._EXPECTED}
        hits = []
        for py_file in _SRC.rglob("*.py"):
            text = py_file.read_text()
            if self._WRITE_RE.search(text):
                hits.append(py_file)
        unexpected = [f for f in hits if f not in known_files]
        assert unexpected == [], f"current_ship_id write site(s) outside the known/paired set: {unexpected}"
