"""Regression coverage for WO-FIX-UNDOCK-DELETES-PAID-MOORING-SLIP.

Bug: docking_service.release() and the defensive pre-grant clear inside
docking_service.acquire() deleted ANY DockingSlipOccupancy row for the
player with no `slip_class` filter, even though a paid long-term mooring
slip (slip_class='long_term', acquired via acquire_long_term() /
released only via release_long_term() -- see docking-slips.md canon)
lives in the SAME table. A player holding a paid mooring slip who then
did a completely ordinary dock/undock cycle at ANY station -- including
the very station they moored at -- silently lost the slip (and the
credits already spent on it), because it shared the player_id-unique
row space with their transient slip.

FakeDB/FakeQuery here mirror test_npc_trader_docking_slips.py's
established DB-free convention for this service (generic SQLAlchemy
filter-criteria interpretation, not hand-parsed call sites), extended
with a `.delete(synchronize_session=False)` FakeQuery method since
acquire()'s defensive clear and bump()'s queue-consumption both use the
bulk-delete form, which the sibling suite didn't need.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, List

from sqlalchemy.sql import operators as sa_operators

from src.models.docking import DockingQueueEntry, DockingSlipOccupancy
from src.models.ship import Ship
from src.models.station import Station, StationClass
from src.services import docking_service


class _FakeQuery:
    def __init__(self, rows: List[Any], all_rows: List[Any], filters=None):
        self._rows = rows
        self._all_rows = all_rows  # the live backing list -- delete() mutates this
        self._filters = filters or []

    def filter(self, *criteria):
        return _FakeQuery(self._rows, self._all_rows, self._filters + list(criteria))

    def with_for_update(self):
        return self

    def populate_existing(self):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def _row_matches(self, row) -> bool:
        for crit in self._filters:
            key = crit.left.key
            if crit.operator is sa_operators.is_not:
                if getattr(row, key, None) is None:
                    return False
                continue
            if crit.operator is sa_operators.eq:
                if getattr(row, key, None) != crit.right.value:
                    return False
                continue
            raise AssertionError(f"FakeQuery: unrecognized operator {crit.operator} on {key}")
        return True

    def first(self):
        for row in self._rows:
            if self._row_matches(row):
                return row
        return None

    def all(self):
        return [row for row in self._rows if self._row_matches(row)]

    def count(self):
        return len(self.all())

    def delete(self, synchronize_session=False):
        matched = [row for row in list(self._all_rows) if self._row_matches(row)]
        for row in matched:
            self._all_rows.remove(row)
        return len(matched)


class FakeDB:
    def __init__(self):
        self.stations: List[Any] = []
        self.occupancies: List[DockingSlipOccupancy] = []
        self.queue_entries: List[Any] = []
        self.ships: List[Any] = []

    def query(self, model):
        if model is Station:
            return _FakeQuery(self.stations, self.stations)
        if model is DockingSlipOccupancy:
            return _FakeQuery(self.occupancies, self.occupancies)
        if model is DockingQueueEntry:
            return _FakeQuery(self.queue_entries, self.queue_entries)
        if model is Ship:
            return _FakeQuery(self.ships, self.ships)
        raise AssertionError(f"FakeDB: unexpected model queried: {model}")

    def add(self, obj):
        if isinstance(obj, DockingSlipOccupancy):
            self.occupancies.append(obj)
        else:
            raise AssertionError(f"FakeDB: unexpected add(): {obj!r}")

    def delete(self, obj):
        if obj in self.occupancies:
            self.occupancies.remove(obj)

    def flush(self):
        pass


def make_station(station_id=None, capacity_class=StationClass.CLASS_3, **overrides):
    station = SimpleNamespace(
        id=station_id or uuid.uuid4(),
        station_class=capacity_class,
        is_spacedock=False,
        tradedock_tier=None,
        reputation_threshold=0,
        name="Test Station",
        ownership={},
        owner_id=None,
    )
    for k, v in overrides.items():
        setattr(station, k, v)
    return station


def make_player(player_id=None, ship_id=None):
    return SimpleNamespace(id=player_id or uuid.uuid4(), current_ship_id=ship_id)


class TestReleaseScopedToTransient:
    def test_releases_a_transient_slip(self):
        db = FakeDB()
        player = make_player()
        db.occupancies.append(DockingSlipOccupancy(
            station_id=uuid.uuid4(), player_id=player.id, slip_class="transient",
        ))

        released = docking_service.release(db, None, player)

        assert released is True
        assert db.occupancies == []

    def test_does_not_delete_a_paid_long_term_mooring_slip(self):
        """The core regression: undocking normally elsewhere must never
        touch a paid long-term mooring row."""
        db = FakeDB()
        player = make_player()
        mooring = DockingSlipOccupancy(
            station_id=uuid.uuid4(), player_id=player.id, slip_class="long_term",
            fee_paid=6000,
        )
        db.occupancies.append(mooring)

        released = docking_service.release(db, None, player)

        assert released is False
        assert db.occupancies == [mooring]

    def test_returns_false_when_no_row_exists(self):
        db = FakeDB()
        player = make_player()
        assert docking_service.release(db, None, player) is False


class TestAcquireDefensiveClearScopedToTransient:
    def test_stale_transient_row_is_cleared_before_granting(self):
        """Pre-existing WO-DOCK-500 Leg 2 behavior: an orphan transient row
        (e.g. a failed warp-undock) is swept before granting a fresh one."""
        db = FakeDB()
        player = make_player()
        station = make_station()
        db.stations.append(station)
        db.occupancies.append(DockingSlipOccupancy(
            station_id=uuid.uuid4(), player_id=player.id, slip_class="transient",
        ))

        result = docking_service.acquire(db, station, player, ship_id=uuid.uuid4())

        assert result["status"] == "granted"
        assert len(db.occupancies) == 1
        assert db.occupancies[0].station_id == station.id

    def test_does_not_delete_a_paid_long_term_mooring_slip_while_docking_elsewhere(self):
        """The core regression, other direction: a normal dock at station B
        must not sweep a paid long-term mooring slip held at station A."""
        db = FakeDB()
        player = make_player()
        home_station = make_station()
        other_station = make_station()
        db.stations.append(other_station)
        mooring = DockingSlipOccupancy(
            station_id=home_station.id, player_id=player.id, slip_class="long_term",
            fee_paid=6000,
        )
        db.occupancies.append(mooring)

        result = docking_service.acquire(db, other_station, player, ship_id=uuid.uuid4())

        assert result["status"] == "granted"
        # Both rows now present: the untouched long-term mooring, plus the
        # freshly granted transient slip at the other station.
        assert mooring in db.occupancies
        assert mooring.slip_class == "long_term"
        assert len(db.occupancies) == 2
        transient_rows = [o for o in db.occupancies if o.slip_class == "transient"]
        assert len(transient_rows) == 1
        assert transient_rows[0].station_id == other_station.id


class TestLongTermReleaseStillWorksViaItsOwnPath:
    def test_release_long_term_removes_only_the_long_term_row(self):
        db = FakeDB()
        player = make_player()
        # player_id is unique in the real schema (at most one row); this
        # suite exercises release_long_term's own filter in isolation by
        # giving it only the long-term row, matching real single-row state.
        mooring = DockingSlipOccupancy(
            station_id=uuid.uuid4(), player_id=player.id, slip_class="long_term",
            fee_paid=6000,
        )
        db.occupancies.append(mooring)

        released = docking_service.release_long_term(db, None, player)

        assert released is True
        assert db.occupancies == []

    def test_generic_release_never_touches_long_term_even_though_release_long_term_exists(self):
        """Sanity: the ordinary undock path (release()) and the dedicated
        mooring path (release_long_term()) are genuinely independent --
        calling release() must never be a backdoor way to drop a mooring
        slip."""
        db = FakeDB()
        player = make_player()
        mooring = DockingSlipOccupancy(
            station_id=uuid.uuid4(), player_id=player.id, slip_class="long_term",
            fee_paid=6000,
        )
        db.occupancies.append(mooring)

        docking_service.release(db, None, player)
        assert db.occupancies == [mooring]

        released = docking_service.release_long_term(db, None, player)
        assert released is True
        assert db.occupancies == []
