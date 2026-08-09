"""Unit tests — hangar_service.py (Carrier ship-hangar: dock consent flow,
undock/disembark, ride-along, jettison).

No test file existed for this service. All HangarService methods are plain
(non-async) `def`s, so no asyncio marker is needed. DB-free: a `_FakeDb`
keyed per-model-class (a FIFO queue popped on each `.query(Model)` call,
mirroring the call order the source code issues them in) stands in for the
session. Real (unattached) Ship/Player/ShipSpecification model instances are
used throughout — dock/accept/undock/etc. call flag_modified() on the
carrier's `hangar` JSONB column. Relationship attributes (`Ship.owner`) are
never auto-loaded on an unattached instance, so tests that exercise a path
reading `carrier.owner` set it explicitly.

Local-import collaborators (`docking_service.release`, `contraband_service.
scan_in_transit_best_effort`, `ship_service.sync_current_pilot`) are owned by
other modules and monkeypatched to recording stand-ins — the local
`from X import Y` inside each method body re-resolves off the live module
object at call time, so patching the module's attribute directly works.
`_schedule_region_hop`'s `asyncio.get_running_loop()` call is unreached in a
sync test context (no running loop) and is caught by its own best-effort
try/except -- no test needs to touch it.

Sections:
  TestEmptyHangar / TestEnsureHangar — the canonical JSONB shape + legacy heal.
  TestUsedUnits — sums DOCKED entries only, PENDING excluded.
  TestShipSize — the NULL/CAPITAL/finite size resolution.
  TestEntryForShip — the docked-list lookup helper.
  TestIsShipHangaredAndFindCarrier — the cross-carrier scan.
  TestEligibleSizeUnits — the WO-AD NULL-then-CAPITAL-then-finite contract.
  TestRequireCarrier — the CAPITAL-only gate.
  TestRequestDock — the consent-flow request stage: every rejection branch,
    the PENDING entry shape, and the against-committed-total capacity check.
  TestAcceptDock — capacity commit, live re-validation race guards, passenger
    state transition, the port-slip release hook.
  TestCancelRequest — PENDING-only removal.
  TestUndock — no-consent resume, sector/status reset.
  TestDisembarkToPort — the Carrier-docked-at-station gate, 0-turn disembark.
  TestCarryHangaredShips — RIDE-ALONG: PENDING skipped, destroyed skipped,
    pilot-follows-only-when-piloting, contraband scan hook.
  TestJettisonAll — intact jettison, escape-pod ejection, hangar clear.
  TestCarrierRegion — owner-based region resolution.
"""

import uuid
from datetime import datetime, timezone

import pytest

import src.services.contraband_service as contraband_service_module
import src.services.docking_service as docking_service_module
import src.services.ship_service as ship_service_module
from src.models.player import Player
from src.models.ship import Ship, ShipSize, ShipSpecification, ShipStatus
from src.services.hangar_service import (
    HANGAR_CAPACITY_UNITS,
    REQUEST_DOCKED,
    REQUEST_PENDING,
    HangarError,
    HangarService,
)


class _FakeQuery:
    def __init__(self, value):
        self._value = value

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._value

    def all(self):
        return self._value if self._value is not None else []


class _FakeDb:
    def __init__(self, results=None):
        self._queues = {k: list(v) for k, v in (results or {}).items()}

    def query(self, model):
        queue = self._queues.get(model, [])
        value = queue.pop(0) if queue else None
        return _FakeQuery(value)


def _spec(ship_type="scout", ship_size=ShipSize.TINY):
    spec = ShipSpecification()
    spec.type = ship_type
    spec.ship_size = ship_size
    return spec


def _ship(**kwargs):
    s = Ship()
    s.id = kwargs.pop("id", uuid.uuid4())
    s.type = kwargs.pop("type", "scout")
    s.owner_id = kwargs.pop("owner_id", None)
    s.sector_id = kwargs.pop("sector_id", 1)
    s.status = kwargs.pop("status", ShipStatus.IN_SPACE)
    s.hangar = kwargs.pop("hangar", None)
    s.tow_state = kwargs.pop("tow_state", None)
    s.is_destroyed = kwargs.pop("is_destroyed", False)
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def _player(**kwargs):
    p = Player()
    p.id = kwargs.pop("id", uuid.uuid4())
    p.user_id = kwargs.pop("user_id", uuid.uuid4())
    p.current_sector_id = kwargs.pop("current_sector_id", 1)
    p.current_region_id = kwargs.pop("current_region_id", None)
    p.current_ship_id = kwargs.pop("current_ship_id", None)
    p.is_docked = kwargs.pop("is_docked", False)
    p.is_landed = kwargs.pop("is_landed", False)
    p.current_port_id = kwargs.pop("current_port_id", None)
    p.current_planet_id = kwargs.pop("current_planet_id", None)
    for k, v in kwargs.items():
        setattr(p, k, v)
    return p


def _docked_entry(ship, state=REQUEST_DOCKED, size_units=1):
    return {
        "ship_id": str(ship.id),
        "owner_id": str(ship.owner_id) if ship.owner_id else None,
        "size": ShipSize.TINY.value,
        "size_units": size_units,
        "docked_at": datetime.now(timezone.utc).isoformat() if state == REQUEST_DOCKED else None,
        "request_state": state,
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest.fixture(autouse=True)
def _stub_local_import_collaborators(monkeypatch):
    """Neutralize the three cross-module hooks called via local imports so
    every test stays scoped to hangar_service's own logic."""
    released = []
    scanned = []
    synced = []
    monkeypatch.setattr(
        docking_service_module, "release", lambda db, x, pilot: released.append((x, pilot))
    )
    monkeypatch.setattr(
        contraband_service_module,
        "scan_in_transit_best_effort",
        lambda db, **kwargs: scanned.append(kwargs),
    )
    monkeypatch.setattr(
        ship_service_module,
        "sync_current_pilot",
        lambda pilot, new_ship, old_ship=None, db=None: synced.append((pilot, new_ship)),
    )
    return {"released": released, "scanned": scanned, "synced": synced}


# ---------------------------------------------------------------------------
# empty_hangar / _ensure_hangar
# ---------------------------------------------------------------------------


class TestEmptyHangar:
    def test_shape(self):
        assert HangarService.empty_hangar() == {
            "capacity_units": HANGAR_CAPACITY_UNITS,
            "docked": [],
        }


class TestEnsureHangar:
    def test_initializes_a_null_hangar(self):
        carrier = _ship(hangar=None)
        svc = HangarService(_FakeDb())
        hangar = svc._ensure_hangar(carrier)
        assert hangar == {"capacity_units": HANGAR_CAPACITY_UNITS, "docked": []}
        assert carrier.hangar is hangar

    def test_heals_a_legacy_dict_missing_capacity_units(self):
        carrier = _ship(hangar={"docked": []})
        svc = HangarService(_FakeDb())
        hangar = svc._ensure_hangar(carrier)
        assert hangar["capacity_units"] == HANGAR_CAPACITY_UNITS

    def test_heals_a_legacy_dict_with_null_docked(self):
        carrier = _ship(hangar={"capacity_units": HANGAR_CAPACITY_UNITS, "docked": None})
        svc = HangarService(_FakeDb())
        hangar = svc._ensure_hangar(carrier)
        assert hangar["docked"] == []

    def test_leaves_a_well_shaped_hangar_untouched(self):
        existing = {"capacity_units": HANGAR_CAPACITY_UNITS, "docked": [{"x": 1}]}
        carrier = _ship(hangar=existing)
        svc = HangarService(_FakeDb())
        hangar = svc._ensure_hangar(carrier)
        assert hangar is existing


# ---------------------------------------------------------------------------
# used_units
# ---------------------------------------------------------------------------


class TestUsedUnits:
    def test_empty_hangar_is_zero(self):
        assert HangarService.used_units(HangarService.empty_hangar()) == 0

    def test_sums_only_docked_entries(self):
        s1, s2, s3 = _ship(), _ship(), _ship()
        hangar = {
            "docked": [
                _docked_entry(s1, state=REQUEST_DOCKED, size_units=2),
                _docked_entry(s2, state=REQUEST_PENDING, size_units=8),
                _docked_entry(s3, state=REQUEST_DOCKED, size_units=4),
            ]
        }
        assert HangarService.used_units(hangar) == 6


# ---------------------------------------------------------------------------
# _ship_size
# ---------------------------------------------------------------------------


class TestShipSize:
    def test_missing_spec_returns_none(self):
        ship = _ship(type="unspecced")
        db = _FakeDb(results={ShipSpecification: [None]})
        svc = HangarService(db)
        assert svc._ship_size(ship) is None

    def test_null_ship_size_on_spec_returns_none(self):
        ship = _ship(type="npc_only")
        db = _FakeDb(results={ShipSpecification: [_spec(ship_size=None)]})
        svc = HangarService(db)
        assert svc._ship_size(ship) is None

    def test_returns_the_specs_size(self):
        ship = _ship(type="freighter")
        db = _FakeDb(results={ShipSpecification: [_spec(ship_size=ShipSize.LARGE)]})
        svc = HangarService(db)
        assert svc._ship_size(ship) == ShipSize.LARGE


# ---------------------------------------------------------------------------
# _entry_for_ship
# ---------------------------------------------------------------------------


class TestEntryForShip:
    def test_finds_by_ship_id_any_state(self):
        ship = _ship()
        entry = _docked_entry(ship, state=REQUEST_PENDING)
        hangar = {"docked": [entry]}
        svc = HangarService(_FakeDb())
        assert svc._entry_for_ship(hangar, ship.id) is entry

    def test_state_filter_excludes_non_matching(self):
        ship = _ship()
        entry = _docked_entry(ship, state=REQUEST_PENDING)
        hangar = {"docked": [entry]}
        svc = HangarService(_FakeDb())
        assert svc._entry_for_ship(hangar, ship.id, REQUEST_DOCKED) is None

    def test_missing_ship_returns_none(self):
        svc = HangarService(_FakeDb())
        assert svc._entry_for_ship({"docked": []}, uuid.uuid4()) is None


# ---------------------------------------------------------------------------
# is_ship_hangared / find_carrier_for_docked_ship
# ---------------------------------------------------------------------------


class TestIsShipHangaredAndFindCarrier:
    def test_not_hangared_anywhere(self):
        db = _FakeDb(results={Ship: [[]]})
        svc = HangarService(db)
        assert svc.is_ship_hangared(uuid.uuid4()) is False

    def test_found_as_a_docked_passenger(self):
        passenger = _ship()
        carrier = _ship(hangar={"docked": [_docked_entry(passenger, state=REQUEST_DOCKED)]})
        db = _FakeDb(results={Ship: [[carrier]]})
        svc = HangarService(db)
        assert svc.find_carrier_for_docked_ship(passenger.id) is carrier

    def test_pending_only_entry_is_not_hangared(self):
        passenger = _ship()
        carrier = _ship(hangar={"docked": [_docked_entry(passenger, state=REQUEST_PENDING)]})
        db = _FakeDb(results={Ship: [[carrier]]})
        svc = HangarService(db)
        assert svc.is_ship_hangared(passenger.id) is False


# ---------------------------------------------------------------------------
# _eligible_size_units
# ---------------------------------------------------------------------------


class TestEligibleSizeUnits:
    def test_null_size_is_ineligible(self):
        ship = _ship()
        db = _FakeDb(results={ShipSpecification: [_spec(ship_size=None)]})
        svc = HangarService(db)
        with pytest.raises(HangarError, match="no canonical size"):
            svc._eligible_size_units(ship)

    def test_capital_size_is_not_dockable(self):
        ship = _ship()
        db = _FakeDb(results={ShipSpecification: [_spec(ship_size=ShipSize.CAPITAL)]})
        svc = HangarService(db)
        with pytest.raises(HangarError, match="Capital-size"):
            svc._eligible_size_units(ship)

    def test_finite_size_returns_its_units(self):
        ship = _ship()
        db = _FakeDb(results={ShipSpecification: [_spec(ship_size=ShipSize.MEDIUM)]})
        svc = HangarService(db)
        assert svc._eligible_size_units(ship) == 4


# ---------------------------------------------------------------------------
# _require_carrier
# ---------------------------------------------------------------------------


class TestRequireCarrier:
    def test_non_capital_raises(self):
        ship = _ship()
        db = _FakeDb(results={ShipSpecification: [_spec(ship_size=ShipSize.LARGE)]})
        svc = HangarService(db)
        with pytest.raises(HangarError, match="Only a Carrier"):
            svc._require_carrier(ship)

    def test_capital_passes(self):
        ship = _ship()
        db = _FakeDb(results={ShipSpecification: [_spec(ship_size=ShipSize.CAPITAL)]})
        svc = HangarService(db)
        svc._require_carrier(ship)  # must not raise


# ---------------------------------------------------------------------------
# request_dock
# ---------------------------------------------------------------------------


def _dock_request_db(carrier_size=ShipSize.CAPITAL, docking_size=ShipSize.TINY, other_carriers=None):
    return _FakeDb(
        results={
            ShipSpecification: [
                _spec(ship_size=carrier_size),
                _spec(ship_size=docking_size),
                _spec(ship_size=docking_size),
            ],
            Ship: [other_carriers if other_carriers is not None else []],
        }
    )


class TestRequestDock:
    def test_cannot_dock_into_self(self):
        carrier = _ship()
        svc = HangarService(_FakeDb())
        with pytest.raises(HangarError, match="dock into itself"):
            svc.request_dock(carrier, carrier)

    def test_destroyed_carrier_rejected(self):
        carrier = _ship(is_destroyed=True)
        docking = _ship()
        svc = HangarService(_FakeDb())
        with pytest.raises(HangarError, match="Destroyed ships"):
            svc.request_dock(docking, carrier)

    def test_destroyed_docking_ship_rejected(self):
        carrier = _ship()
        docking = _ship(is_destroyed=True)
        svc = HangarService(_FakeDb())
        with pytest.raises(HangarError, match="Destroyed ships"):
            svc.request_dock(docking, carrier)

    def test_non_carrier_rejected(self):
        carrier = _ship()
        docking = _ship()
        db = _FakeDb(results={ShipSpecification: [_spec(ship_size=ShipSize.LARGE)]})
        svc = HangarService(db)
        with pytest.raises(HangarError, match="Only a Carrier"):
            svc.request_dock(docking, carrier)

    def test_different_sector_rejected(self):
        carrier = _ship(sector_id=1)
        docking = _ship(sector_id=2)
        db = _dock_request_db()
        svc = HangarService(db)
        with pytest.raises(HangarError, match="must be in your sector"):
            svc.request_dock(docking, carrier)

    def test_carrier_in_combat_rejected(self):
        carrier = _ship(status=ShipStatus.IN_COMBAT)
        docking = _ship()
        db = _FakeDb(results={ShipSpecification: [_spec(ship_size=ShipSize.CAPITAL)]})
        svc = HangarService(db)
        with pytest.raises(HangarError, match="in combat"):
            svc.request_dock(docking, carrier)

    def test_docking_ship_in_combat_rejected(self):
        carrier = _ship()
        docking = _ship(status=ShipStatus.IN_COMBAT)
        db = _FakeDb(results={ShipSpecification: [_spec(ship_size=ShipSize.CAPITAL)]})
        svc = HangarService(db)
        with pytest.raises(HangarError, match="in combat"):
            svc.request_dock(docking, carrier)

    def test_harmonizing_docking_ship_rejected(self):
        carrier = _ship()
        docking = _ship(status=ShipStatus.HARMONIZING)
        db = _FakeDb(results={ShipSpecification: [_spec(ship_size=ShipSize.CAPITAL)]})
        svc = HangarService(db)
        with pytest.raises(HangarError, match="harmonizing"):
            svc.request_dock(docking, carrier)

    def test_already_hangared_elsewhere_rejected(self):
        carrier = _ship()
        docking = _ship()
        other_carrier = _ship(hangar={"docked": [_docked_entry(docking, state=REQUEST_DOCKED)]})
        db = _FakeDb(
            results={
                ShipSpecification: [_spec(ship_size=ShipSize.CAPITAL)],
                Ship: [[other_carrier]],
            }
        )
        svc = HangarService(db)
        with pytest.raises(HangarError, match="already docked"):
            svc.request_dock(docking, carrier)

    def test_being_towed_rejected(self):
        carrier = _ship()
        docking = _ship(tow_state={"towed_by": str(uuid.uuid4())})
        db = _FakeDb(
            results={ShipSpecification: [_spec(ship_size=ShipSize.CAPITAL)], Ship: [[]]}
        )
        svc = HangarService(db)
        with pytest.raises(HangarError, match="being towed"):
            svc.request_dock(docking, carrier)

    def test_ineligible_size_rejected(self):
        carrier = _ship()
        docking = _ship()
        db = _FakeDb(
            results={
                ShipSpecification: [_spec(ship_size=ShipSize.CAPITAL), _spec(ship_size=None)],
                Ship: [[]],
            }
        )
        svc = HangarService(db)
        with pytest.raises(HangarError, match="no canonical size"):
            svc.request_dock(docking, carrier)

    def test_duplicate_request_rejected(self):
        carrier = _ship()
        docking = _ship()
        carrier.hangar = {
            "capacity_units": HANGAR_CAPACITY_UNITS,
            "docked": [_docked_entry(docking, state=REQUEST_PENDING)],
        }
        db = _dock_request_db()
        svc = HangarService(db)
        with pytest.raises(HangarError, match="pending or active dock"):
            svc.request_dock(docking, carrier)

    def test_insufficient_capacity_rejected(self):
        carrier = _ship()
        already = _ship()
        carrier.hangar = {
            "capacity_units": HANGAR_CAPACITY_UNITS,
            "docked": [_docked_entry(already, state=REQUEST_DOCKED, size_units=8)],
        }
        docking = _ship()
        db = _dock_request_db(docking_size=ShipSize.TINY)
        svc = HangarService(db)
        with pytest.raises(HangarError, match="Not enough hangar capacity"):
            svc.request_dock(docking, carrier)

    def test_successful_request_adds_a_pending_entry(self):
        carrier = _ship()
        docking = _ship(owner_id=uuid.uuid4())
        db = _dock_request_db(docking_size=ShipSize.SMALL)
        svc = HangarService(db)
        result = svc.request_dock(docking, carrier)
        assert result == {"status": REQUEST_PENDING, "ship_id": str(docking.id), "size_units": 2}
        assert len(carrier.hangar["docked"]) == 1
        entry = carrier.hangar["docked"][0]
        assert entry["request_state"] == REQUEST_PENDING
        assert entry["docked_at"] is None
        assert entry["size_units"] == 2
        assert entry["owner_id"] == str(docking.owner_id)


# ---------------------------------------------------------------------------
# accept_dock
# ---------------------------------------------------------------------------


class TestAcceptDock:
    def _pending_carrier(self, docking):
        carrier = _ship()
        carrier.hangar = {
            "capacity_units": HANGAR_CAPACITY_UNITS,
            "docked": [_docked_entry(docking, state=REQUEST_PENDING, size_units=2)],
        }
        return carrier

    def test_no_pending_request_rejected(self):
        carrier = _ship(hangar=HangarService.empty_hangar())
        docking = _ship()
        pilot = _player()
        db = _FakeDb(
            results={ShipSpecification: [_spec(ship_size=ShipSize.CAPITAL)], Ship: [[]]}
        )
        svc = HangarService(db)
        with pytest.raises(HangarError, match="No pending dock request"):
            svc.accept_dock(carrier, docking, pilot)

    def test_destroyed_docking_ship_rejected_at_accept(self):
        docking = _ship(is_destroyed=True)
        carrier = self._pending_carrier(docking)
        pilot = _player()
        db = _FakeDb(
            results={ShipSpecification: [_spec(ship_size=ShipSize.CAPITAL)], Ship: [[]]}
        )
        svc = HangarService(db)
        with pytest.raises(HangarError, match="Destroyed ships"):
            svc.accept_dock(carrier, docking, pilot)

    def test_moved_out_of_sector_rejected_at_accept(self):
        docking = _ship(sector_id=2)
        carrier = self._pending_carrier(docking)
        carrier.sector_id = 1
        pilot = _player()
        db = _FakeDb(
            results={ShipSpecification: [_spec(ship_size=ShipSize.CAPITAL)], Ship: [[]]}
        )
        svc = HangarService(db)
        with pytest.raises(HangarError, match="no longer in the Carrier's sector"):
            svc.accept_dock(carrier, docking, pilot)

    def test_now_hangared_elsewhere_rejected_at_accept(self):
        docking = _ship()
        carrier = self._pending_carrier(docking)
        other_carrier = _ship(hangar={"docked": [_docked_entry(docking, state=REQUEST_DOCKED)]})
        pilot = _player()
        db = _FakeDb(
            results={
                ShipSpecification: [_spec(ship_size=ShipSize.CAPITAL)],
                Ship: [[other_carrier]],
            }
        )
        svc = HangarService(db)
        with pytest.raises(HangarError, match="already docked"):
            svc.accept_dock(carrier, docking, pilot)

    def test_capacity_filled_since_request_drops_the_entry_and_rejects(self):
        docking = _ship()
        carrier = _ship()
        already = _ship()
        carrier.hangar = {
            "capacity_units": HANGAR_CAPACITY_UNITS,
            "docked": [
                _docked_entry(already, state=REQUEST_DOCKED, size_units=8),
                _docked_entry(docking, state=REQUEST_PENDING, size_units=2),
            ],
        }
        pilot = _player()
        db = _FakeDb(
            results={ShipSpecification: [_spec(ship_size=ShipSize.CAPITAL)], Ship: [[]]}
        )
        svc = HangarService(db)
        with pytest.raises(HangarError, match="filled up"):
            svc.accept_dock(carrier, docking, pilot)
        # the stale PENDING request was dropped, not left dangling
        assert svc._entry_for_ship(carrier.hangar, docking.id) is None

    def test_successful_accept_flips_to_docked_and_charges_one_turn(
        self, _stub_local_import_collaborators
    ):
        docking = _ship(sector_id=7)
        carrier = self._pending_carrier(docking)
        carrier.sector_id = 7
        pilot = _player(current_sector_id=1, current_port_id=uuid.uuid4(), is_docked=True)
        db = _FakeDb(
            results={ShipSpecification: [_spec(ship_size=ShipSize.CAPITAL)], Ship: [[]]}
        )
        svc = HangarService(db)
        result, turn_cost = svc.accept_dock(carrier, docking, pilot)
        assert turn_cost == 1
        assert result["status"] == REQUEST_DOCKED
        assert result["used_units"] == 2
        assert docking.sector_id == 7
        assert docking.status == ShipStatus.DOCKED
        assert pilot.current_sector_id == 7
        assert pilot.is_docked is False
        assert pilot.is_landed is False
        assert pilot.current_port_id is None
        assert pilot.current_planet_id is None
        entry = svc._entry_for_ship(carrier.hangar, docking.id, REQUEST_DOCKED)
        assert entry is not None
        assert entry["docked_at"] is not None
        assert _stub_local_import_collaborators["released"] == [(None, pilot)]


# ---------------------------------------------------------------------------
# cancel_request
# ---------------------------------------------------------------------------


class TestCancelRequest:
    def test_no_pending_request_rejected(self):
        carrier = _ship(hangar=HangarService.empty_hangar())
        svc = HangarService(_FakeDb())
        with pytest.raises(HangarError, match="No pending dock request"):
            svc.cancel_request(carrier, uuid.uuid4())

    def test_removes_the_pending_entry(self):
        docking = _ship()
        carrier = _ship(
            hangar={
                "capacity_units": HANGAR_CAPACITY_UNITS,
                "docked": [_docked_entry(docking, state=REQUEST_PENDING)],
            }
        )
        svc = HangarService(_FakeDb())
        result = svc.cancel_request(carrier, docking.id)
        assert result == {"status": "CANCELLED", "ship_id": str(docking.id)}
        assert carrier.hangar["docked"] == []

    def test_does_not_cancel_an_already_docked_entry(self):
        docking = _ship()
        carrier = _ship(
            hangar={
                "capacity_units": HANGAR_CAPACITY_UNITS,
                "docked": [_docked_entry(docking, state=REQUEST_DOCKED)],
            }
        )
        svc = HangarService(_FakeDb())
        with pytest.raises(HangarError, match="No pending dock request"):
            svc.cancel_request(carrier, docking.id)


# ---------------------------------------------------------------------------
# undock
# ---------------------------------------------------------------------------


class TestUndock:
    def test_not_docked_anywhere_rejected(self):
        db = _FakeDb(results={Ship: [[]]})
        svc = HangarService(db)
        with pytest.raises(HangarError, match="not docked inside a Carrier"):
            svc.undock(_ship(), _player())

    def test_resumes_in_the_carriers_current_sector_and_removes_entry(self):
        docked_ship = _ship()
        carrier = _ship(
            sector_id=9,
            hangar={
                "capacity_units": HANGAR_CAPACITY_UNITS,
                "docked": [_docked_entry(docked_ship, state=REQUEST_DOCKED)],
            },
        )
        pilot = _player()
        db = _FakeDb(results={Ship: [[carrier]]})
        svc = HangarService(db)
        result, turn_cost = svc.undock(docked_ship, pilot)
        assert turn_cost == 1
        assert result == {"status": "UNDOCKED", "sector_id": 9}
        assert docked_ship.sector_id == 9
        assert docked_ship.status == ShipStatus.IN_SPACE
        assert pilot.current_sector_id == 9
        assert pilot.is_docked is False
        assert pilot.is_landed is False
        assert carrier.hangar["docked"] == []


# ---------------------------------------------------------------------------
# disembark_to_port
# ---------------------------------------------------------------------------


class TestDisembarkToPort:
    def test_not_docked_anywhere_rejected(self):
        db = _FakeDb(results={Ship: [[]]})
        svc = HangarService(db)
        with pytest.raises(HangarError, match="not docked inside a Carrier"):
            svc.disembark_to_port(_ship(), _player())

    def test_carrier_not_at_a_station_rejected(self):
        docked_ship = _ship()
        carrier = _ship(
            hangar={
                "capacity_units": HANGAR_CAPACITY_UNITS,
                "docked": [_docked_entry(docked_ship, state=REQUEST_DOCKED)],
            }
        )
        carrier.owner = _player(is_docked=False)
        db = _FakeDb(results={Ship: [[carrier]]})
        svc = HangarService(db)
        with pytest.raises(HangarError, match="not docked at a station"):
            svc.disembark_to_port(docked_ship, _player())

    def test_carrier_with_no_owner_rejected(self):
        docked_ship = _ship()
        carrier = _ship(
            hangar={
                "capacity_units": HANGAR_CAPACITY_UNITS,
                "docked": [_docked_entry(docked_ship, state=REQUEST_DOCKED)],
            }
        )
        db = _FakeDb(results={Ship: [[carrier]]})
        svc = HangarService(db)
        with pytest.raises(HangarError, match="not docked at a station"):
            svc.disembark_to_port(docked_ship, _player())

    def test_successful_disembark_at_zero_turns(self):
        docked_ship = _ship()
        carrier = _ship(
            sector_id=5,
            hangar={
                "capacity_units": HANGAR_CAPACITY_UNITS,
                "docked": [_docked_entry(docked_ship, state=REQUEST_DOCKED)],
            },
        )
        port_id = uuid.uuid4()
        carrier_owner = _player(is_docked=True, current_port_id=port_id)
        carrier.owner = carrier_owner
        pilot = _player()
        db = _FakeDb(results={Ship: [[carrier]]})
        svc = HangarService(db)
        result, turn_cost = svc.disembark_to_port(docked_ship, pilot)
        assert turn_cost == 0
        assert result == {"status": "DISEMBARKED", "port_id": str(port_id)}
        assert docked_ship.sector_id == 5
        assert docked_ship.status == ShipStatus.DOCKED
        assert pilot.is_docked is True
        assert pilot.current_port_id == port_id
        assert carrier.hangar["docked"] == []


# ---------------------------------------------------------------------------
# carry_hangared_ships
# ---------------------------------------------------------------------------


class TestCarryHangaredShips:
    def test_no_hangar_is_a_noop(self):
        carrier = _ship(hangar=None)
        svc = HangarService(_FakeDb())
        assert svc.carry_hangared_ships(carrier, 42) == 0

    def test_empty_docked_list_is_a_noop(self):
        carrier = _ship(hangar=HangarService.empty_hangar())
        svc = HangarService(_FakeDb())
        assert svc.carry_hangared_ships(carrier, 42) == 0

    def test_pending_entries_do_not_ride_along(self):
        pending_ship = _ship()
        carrier = _ship(
            hangar={
                "capacity_units": HANGAR_CAPACITY_UNITS,
                "docked": [_docked_entry(pending_ship, state=REQUEST_PENDING)],
            }
        )
        svc = HangarService(_FakeDb())
        assert svc.carry_hangared_ships(carrier, 42) == 0

    def test_destroyed_passenger_is_skipped(self):
        destroyed = _ship(is_destroyed=True)
        carrier = _ship(
            hangar={
                "capacity_units": HANGAR_CAPACITY_UNITS,
                "docked": [_docked_entry(destroyed, state=REQUEST_DOCKED)],
            }
        )
        db = _FakeDb(results={Ship: [destroyed]})
        svc = HangarService(db)
        assert svc.carry_hangared_ships(carrier, 42) == 0

    def test_owned_passenger_ship_moves_with_no_owning_pilot(self):
        passenger = _ship(owner_id=None)
        carrier = _ship(
            hangar={
                "capacity_units": HANGAR_CAPACITY_UNITS,
                "docked": [_docked_entry(passenger, state=REQUEST_DOCKED)],
            }
        )
        db = _FakeDb(results={Ship: [passenger]})
        svc = HangarService(db)
        carried = svc.carry_hangared_ships(carrier, 99)
        assert carried == 1
        assert passenger.sector_id == 99

    def test_piloting_owner_follows_and_triggers_contraband_scan(
        self, _stub_local_import_collaborators
    ):
        owner = _player()
        passenger = _ship(owner_id=owner.id)
        owner.current_ship_id = passenger.id
        owner.current_sector_id = 5
        carrier = _ship(
            hangar={
                "capacity_units": HANGAR_CAPACITY_UNITS,
                "docked": [_docked_entry(passenger, state=REQUEST_DOCKED)],
            }
        )
        db = _FakeDb(results={Ship: [passenger], Player: [owner]})
        svc = HangarService(db)
        carried = svc.carry_hangared_ships(carrier, 100)
        assert carried == 1
        assert owner.current_sector_id == 100
        assert len(_stub_local_import_collaborators["scanned"]) == 1
        scan_kwargs = _stub_local_import_collaborators["scanned"][0]
        assert scan_kwargs["origin_sector_id"] == 5
        assert scan_kwargs["destination_sector_id"] == 100

    def test_owner_not_currently_piloting_this_hull_does_not_move(self):
        owner = _player(current_sector_id=1)
        passenger = _ship(owner_id=owner.id)
        owner.current_ship_id = uuid.uuid4()  # piloting a DIFFERENT ship
        carrier = _ship(
            hangar={
                "capacity_units": HANGAR_CAPACITY_UNITS,
                "docked": [_docked_entry(passenger, state=REQUEST_DOCKED)],
            }
        )
        db = _FakeDb(results={Ship: [passenger], Player: [owner]})
        svc = HangarService(db)
        carried = svc.carry_hangared_ships(carrier, 100)
        assert carried == 1
        assert owner.current_sector_id == 1  # unchanged


# ---------------------------------------------------------------------------
# jettison_all
# ---------------------------------------------------------------------------


class _FakeShipService:
    def __init__(self, escape_pod):
        self.escape_pod = escape_pod
        self.calls = []

    def _ensure_escape_pod(self, pilot, destruction_sector_id):
        self.calls.append((pilot, destruction_sector_id))
        return self.escape_pod


class TestJettisonAll:
    def test_no_hangar_returns_empty_list(self):
        carrier = _ship(hangar=None)
        svc = HangarService(_FakeDb())
        assert svc.jettison_all(carrier, 13, _FakeShipService(_ship())) == []

    def test_pending_entries_are_not_jettisoned(self):
        pending_ship = _ship()
        carrier = _ship(
            hangar={
                "capacity_units": HANGAR_CAPACITY_UNITS,
                "docked": [_docked_entry(pending_ship, state=REQUEST_PENDING)],
            }
        )
        svc = HangarService(_FakeDb())
        result = svc.jettison_all(carrier, 13, _FakeShipService(_ship()))
        assert result == []
        # a PENDING entry is left as-is (not cleared by the "empty the hangar" step,
        # since it was never iterated as jettisoned) -- but the hangar clear at the
        # end unconditionally empties docked[], so confirm that invariant too.
        assert carrier.hangar["docked"] == []

    def test_jettisons_intact_without_destroying_the_ship(self, _stub_local_import_collaborators):
        passenger = _ship(owner_id=None, status=ShipStatus.DOCKED)
        carrier = _ship(
            hangar={
                "capacity_units": HANGAR_CAPACITY_UNITS,
                "docked": [_docked_entry(passenger, state=REQUEST_DOCKED)],
            }
        )
        db = _FakeDb(results={Ship: [passenger]})
        svc = HangarService(db)
        jettisoned = svc.jettison_all(carrier, 13, _FakeShipService(_ship()))
        assert jettisoned == [passenger.id]
        assert passenger.is_destroyed is False
        assert passenger.sector_id == 13
        assert passenger.status == ShipStatus.IN_SPACE
        assert carrier.hangar["docked"] == []

    def test_piloting_owner_ejects_to_an_escape_pod(self, _stub_local_import_collaborators):
        owner = _player()
        passenger = _ship(owner_id=owner.id)
        owner.current_ship_id = passenger.id
        escape_pod = _ship()
        carrier = _ship(
            hangar={
                "capacity_units": HANGAR_CAPACITY_UNITS,
                "docked": [_docked_entry(passenger, state=REQUEST_DOCKED)],
            }
        )
        db = _FakeDb(results={Ship: [passenger], Player: [owner]})
        svc = HangarService(db)
        ship_service = _FakeShipService(escape_pod)
        svc.jettison_all(carrier, 13, ship_service)
        assert ship_service.calls == [(owner, 13)]
        assert _stub_local_import_collaborators["synced"] == [(owner, escape_pod)]
        assert owner.current_sector_id == 13
        assert owner.is_docked is False
        assert owner.is_landed is False

    def test_destroyed_passenger_is_skipped(self):
        destroyed = _ship(is_destroyed=True)
        carrier = _ship(
            hangar={
                "capacity_units": HANGAR_CAPACITY_UNITS,
                "docked": [_docked_entry(destroyed, state=REQUEST_DOCKED)],
            }
        )
        db = _FakeDb(results={Ship: [destroyed]})
        svc = HangarService(db)
        result = svc.jettison_all(carrier, 13, _FakeShipService(_ship()))
        assert result == []


# ---------------------------------------------------------------------------
# _carrier_region
# ---------------------------------------------------------------------------


class TestCarrierRegion:
    def test_no_owner_returns_none(self):
        carrier = _ship()
        assert HangarService._carrier_region(carrier) is None

    def test_returns_the_owners_region(self):
        region_id = uuid.uuid4()
        carrier = _ship()
        carrier.owner = _player(current_region_id=region_id)
        assert HangarService._carrier_region(carrier) == region_id
