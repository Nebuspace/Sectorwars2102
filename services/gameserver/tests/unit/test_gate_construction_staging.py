"""WO-GWQ-GATE-STAGING — ADR-0078 gate_construction_site staged-materials
pipeline: a Warp Jumper's 200-unit hold can't fit a phase's 1,500 (Phase 1)
or 1,530 (Phase 3) unit material total in one trip, so each phase's bulk
ORE / EQUIPMENT / LUMEN_CRYSTALS accumulate in a GateConstructionSite across
partial deposits instead.

Exercised directly against warp_gate_service's new surface (stage_materials,
advance_construction, _lazy_advance_site_cure) and the reworked cancel-path
disposition (_refund_phase3_and_cancel, _dispose_beacon_construction_sites),
with hand-built fakes (no DB, no app) — mirrors test_lumen_supply_chain.py's
_FakeQuery/_FakeSession pattern, extended with `count`/`all`/`seq` support for
the additional query shapes this service uses.

deploy_beacon/anchor_focus's PRE-EXISTING region/sector/cap validation gauntlet
is untouched by this WO and out of its novel scope; Accept criterion #1's
"no code path requires >=1,000 units in-hold at once" is proven two ways:
a static AST check that no `_require_cargo` call (in fact the helper itself)
survives anywhere in the module, and a direct drive of stage_materials across
repeated <=200-unit deposits summing to both phases' full totals.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from src.core.game_time import scaled_deadline
from src.models.gate_construction_site import GateConstructionSite, GateConstructionSiteStatus
from src.models.player import Player
from src.models.ship import Ship, ShipStatus, ShipType
from src.models.warp_gate import WarpGate, WarpGateBeacon, WarpGateBeaconStatus, WarpGateStatus
from src.services import warp_gate_service
from src.services.warp_gate_service import WarpGateError

PHASE1_ORE = warp_gate_service.PHASE1_ORE
PHASE1_EQUIPMENT = warp_gate_service.PHASE1_EQUIPMENT
PHASE3_ORE = warp_gate_service.PHASE3_ORE
PHASE3_EQUIPMENT = warp_gate_service.PHASE3_EQUIPMENT
PHASE3_LUMEN = warp_gate_service.PHASE3_LUMEN_CRYSTALS
CONSTRUCTION_TURN_COST = warp_gate_service.CONSTRUCTION_TURN_COST
PHASE_CURE_HOURS = warp_gate_service.PHASE_CURE_HOURS


# --- shared fakes -----------------------------------------------------------


class _FakeQuery:
    """Stands in for a SQLAlchemy Query. filter()/join()/order_by()/
    populate_existing()/with_for_update() are no-ops returning self — the
    test already controls exactly what's in the fake session, so predicates
    never need real evaluation. `seq` supports a query shape hit MORE THAN
    ONCE per call with DIFFERENT wanted results, consumed in call order
    (used only where a single function issues two distinct queries against
    the same model in a known, deterministic sequence)."""

    def __init__(
        self,
        *,
        first: Any = None,
        count: int = 0,
        all: Optional[List[Any]] = None,
        seq: Optional[List[Any]] = None,
    ) -> None:
        self._first = first
        self._count = count
        self._all = all if all is not None else []
        self._seq = list(seq) if seq is not None else None

    def filter(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def join(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def order_by(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def populate_existing(self) -> "_FakeQuery":
        return self

    def with_for_update(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def first(self) -> Any:
        if self._seq is not None:
            return self._seq.pop(0) if self._seq else None
        return self._first

    def count(self) -> int:
        return self._count

    def all(self) -> List[Any]:
        return self._all


class _FakeSession:
    """Maps a model class to the fake query it should get. flush()/commit()/
    rollback() are no-ops (every function under test is flush-only — the
    route owns the commit). add()/delete() are tracked so tests can assert
    on what a call created/removed."""

    def __init__(self, specs: Dict[type, _FakeQuery]) -> None:
        self._specs = specs
        self.added: List[Any] = []
        self.deleted: List[Any] = []

    def query(self, model: type) -> _FakeQuery:
        assert model in self._specs, f"unexpected query for {model!r}"
        return self._specs[model]

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


def _fake_player(**overrides: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        turns=100,
        lifetime_turns_spent=0,
        credits=1_000_000,
        quantum_crystals=5,
        lumen_crystals=0,
        current_sector_id=42,
        current_ship_id=None,
        current_region_id=None,
        team_id=None,
        is_docked=False,
        is_landed=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _fake_ship(**overrides: Any) -> Ship:
    # A REAL ORM instance, not SimpleNamespace: _charge_cargo/_refund_cargo
    # call flag_modified(ship, "cargo"), which requires a mapped instance
    # (_sa_instance_state) — a bare SimpleNamespace raises AttributeError.
    defaults = dict(
        id=uuid.uuid4(),
        type=ShipType.WARP_JUMPER,
        owner_id=uuid.uuid4(),
        status=ShipStatus.IN_SPACE,
        cargo={"capacity": 200, "used": 0, "contents": {}},
        is_destroyed=False,
        harmonization_completes_at=None,
    )
    defaults.update(overrides)
    return Ship(**defaults)


def _fake_beacon(player_id: Any, **overrides: Any) -> WarpGateBeacon:
    defaults = dict(
        id=uuid.uuid4(),
        player_id=player_id,
        source_sector_id=42,
        destination_sector_id=999,
        status=WarpGateBeaconStatus.DEPLOYED,
        invulnerable_until=datetime.now(timezone.utc) + timedelta(hours=40),
        hp=5000,
    )
    defaults.update(overrides)
    return WarpGateBeacon(**defaults)


def _fake_site(beacon_id: Any, phase: int, **overrides: Any) -> GateConstructionSite:
    if phase == 1:
        required = dict(required_ore=PHASE1_ORE, required_equipment=PHASE1_EQUIPMENT, required_lumen=0)
    else:
        required = dict(required_ore=PHASE3_ORE, required_equipment=PHASE3_EQUIPMENT, required_lumen=PHASE3_LUMEN)
    defaults = dict(
        id=uuid.uuid4(),
        beacon_id=beacon_id,
        gate_id=None,
        phase=phase,
        staged_ore=0,
        staged_equipment=0,
        staged_lumen=0,
        turns_applied=0,
        cure_completes_at=None,
        status=GateConstructionSiteStatus.STAGING,
        **required,
    )
    defaults.update(overrides)
    return GateConstructionSite(**defaults)


# --- Accept #1: no code path can demand a phase's full total in-hold -------


@pytest.mark.unit
class TestNoBulkCargoGateRemains:
    def test_require_cargo_helper_no_longer_exists(self) -> None:
        """ADR-0078's whole point: the instant-full-payload cargo check is
        gone, not merely unreferenced."""
        assert not hasattr(warp_gate_service, "_require_cargo")

    def test_no_call_anywhere_references_the_phase_ore_totals(self) -> None:
        """AST (not substring) search: no Call node named `_require_cargo`
        anywhere in the module may have PHASE1_ORE/PHASE3_ORE among its
        argument names. With the helper removed this is vacuously true, but
        the check survives independently of that removal (e.g. if a future
        edit reintroduced a differently-named all-at-once cargo gate)."""
        tree = ast.parse(inspect.getsource(warp_gate_service))
        banned = {"PHASE1_ORE", "PHASE3_ORE"}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_require_cargo"
            ):
                names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                assert not (names & banned), f"_require_cargo call still references {names & banned}"

    def test_staging_completes_both_phase_totals_via_le_200_unit_deposits(self) -> None:
        """Drives stage_materials directly across repeated <=200-unit loads
        (a Warp Jumper's cargo cap) until BOTH Phase 1's 1,500 units and
        Phase 3's 1,530 units are fully staged — no single call exceeds 200."""
        player = _fake_player()
        ship = _fake_ship(owner_id=player.id, cargo={"capacity": 200, "used": 0, "contents": {}})
        player.current_ship_id = ship.id
        beacon = _fake_beacon(player.id)
        site1 = _fake_site(beacon.id, phase=1)

        def _stage(site: GateConstructionSite, ore: int = 0, equipment: int = 0, lumen: int = 0) -> None:
            db = _FakeSession({
                GateConstructionSite: _FakeQuery(first=site),
                WarpGateBeacon: _FakeQuery(first=beacon),
                Player: _FakeQuery(first=player),
                Ship: _FakeQuery(first=ship),
            })
            amounts: Dict[str, int] = {}
            if ore:
                amounts["ore"] = ore
            if equipment:
                amounts["equipment"] = equipment
            if lumen:
                amounts["lumen_crystals"] = lumen
            warp_gate_service.stage_materials(db, player, str(site.id), amounts)

        # Phase 1: 1,000 ore + 500 equipment == 1,500 units, no run > 200.
        assert PHASE1_ORE + PHASE1_EQUIPMENT == 1500
        for _ in range(5):
            ship.cargo["contents"]["ore"] = 200
            _stage(site1, ore=200)
        for _ in range(3):
            ship.cargo["contents"]["equipment"] = 167  # 3 x 167 = 501 -- clamp handles the last
            _stage(site1, equipment=min(167, PHASE1_EQUIPMENT - site1.staged_equipment))
        assert site1.staged_ore == PHASE1_ORE
        assert site1.staged_equipment == PHASE1_EQUIPMENT

        # Phase 3: 1,000 ore + 500 equipment + 30 Lumen == 1,530 units.
        assert PHASE3_ORE + PHASE3_EQUIPMENT + PHASE3_LUMEN == 1530
        site3 = _fake_site(beacon.id, phase=3)
        for _ in range(5):
            ship.cargo["contents"]["ore"] = 200
            _stage(site3, ore=200)
        for _ in range(3):
            need = min(167, PHASE3_EQUIPMENT - site3.staged_equipment)
            ship.cargo["contents"]["equipment"] = need
            _stage(site3, equipment=need)
        player.lumen_crystals = PHASE3_LUMEN
        _stage(site3, lumen=PHASE3_LUMEN)

        assert site3.staged_ore == PHASE3_ORE
        assert site3.staged_equipment == PHASE3_EQUIPMENT
        assert site3.staged_lumen == PHASE3_LUMEN


# --- Accept #2: stage-materials amount validation ---------------------------


@pytest.mark.unit
class TestStageMaterials:
    def _setup(self, phase: int = 1, **site_overrides: Any):
        player = _fake_player()
        ship = _fake_ship(owner_id=player.id)
        player.current_ship_id = ship.id
        beacon = _fake_beacon(player.id)
        site = _fake_site(beacon.id, phase=phase, **site_overrides)
        db = _FakeSession({
            GateConstructionSite: _FakeQuery(first=site),
            WarpGateBeacon: _FakeQuery(first=beacon),
            Player: _FakeQuery(first=player),
            Ship: _FakeQuery(first=ship),
        })
        return db, player, ship, beacon, site

    def test_full_200_unit_wj_load_stages_in_one_call(self) -> None:
        db, player, ship, beacon, site = self._setup()
        ship.cargo["contents"]["ore"] = 200
        result = warp_gate_service.stage_materials(db, player, str(site.id), {"ore": 200})
        assert site.staged_ore == 200
        assert ship.cargo["contents"].get("ore", 0) == 0
        assert result["staged"]["ore"] == 200

    def test_repeated_partial_loads_sum_exactly_never_over(self) -> None:
        db, player, ship, beacon, site = self._setup()
        for _ in range(5):
            ship.cargo["contents"]["ore"] = 200
            warp_gate_service.stage_materials(db, player, str(site.id), {"ore": 200})
        assert site.staged_ore == PHASE1_ORE
        ship.cargo["contents"]["ore"] = 200
        with pytest.raises(WarpGateError):
            warp_gate_service.stage_materials(db, player, str(site.id), {"ore": 1})
        assert site.staged_ore == PHASE1_ORE  # never exceeded

    def test_rejects_amount_exceeding_ship_cargo_contents(self) -> None:
        db, player, ship, beacon, site = self._setup()
        ship.cargo["contents"]["ore"] = 50
        with pytest.raises(WarpGateError, match="cargo holds only"):
            warp_gate_service.stage_materials(db, player, str(site.id), {"ore": 100})
        assert site.staged_ore == 0
        assert ship.cargo["contents"]["ore"] == 50

    def test_rejects_amount_exceeding_remaining_phase_requirement(self) -> None:
        db, player, ship, beacon, site = self._setup(staged_ore=950)
        ship.cargo["contents"]["ore"] = 200  # ship has plenty...
        with pytest.raises(WarpGateError, match="more ore is needed"):
            warp_gate_service.stage_materials(db, player, str(site.id), {"ore": 200})  # ...only 50 remain
        assert site.staged_ore == 950

    def test_lumen_draws_from_player_wallet_never_cargo(self) -> None:
        db, player, ship, beacon, site = self._setup(phase=3)
        player.lumen_crystals = 50
        result = warp_gate_service.stage_materials(db, player, str(site.id), {"lumen_crystals": 30})
        assert site.staged_lumen == 30
        assert player.lumen_crystals == 20
        assert ship.cargo["contents"] == {}  # untouched
        assert result["staged"]["lumen_crystals"] == 30

    def test_lumen_rejects_insufficient_wallet_balance(self) -> None:
        db, player, ship, beacon, site = self._setup(phase=3)
        player.lumen_crystals = 5
        with pytest.raises(WarpGateError, match="Lumen Crystals"):
            warp_gate_service.stage_materials(db, player, str(site.id), {"lumen_crystals": 10})
        assert site.staged_lumen == 0
        assert player.lumen_crystals == 5

    def test_lumen_rejected_on_a_phase1_site_where_none_is_required(self) -> None:
        db, player, ship, beacon, site = self._setup(phase=1)
        player.lumen_crystals = 100
        with pytest.raises(WarpGateError, match="Lumen Crystals are needed"):
            warp_gate_service.stage_materials(db, player, str(site.id), {"lumen_crystals": 1})
        assert player.lumen_crystals == 100

    def test_rejects_when_site_is_not_staging(self) -> None:
        db, player, ship, beacon, site = self._setup(status=GateConstructionSiteStatus.CURING)
        ship.cargo["contents"]["ore"] = 100
        with pytest.raises(WarpGateError, match="curing"):
            warp_gate_service.stage_materials(db, player, str(site.id), {"ore": 10})

    def test_rejects_player_outside_the_beacons_sector(self) -> None:
        db, player, ship, beacon, site = self._setup()
        player.current_sector_id = 1  # beacon.source_sector_id == 42
        ship.cargo["contents"]["ore"] = 100
        with pytest.raises(WarpGateError, match="sector"):
            warp_gate_service.stage_materials(db, player, str(site.id), {"ore": 100})

    def test_rejects_docked_player(self) -> None:
        db, player, ship, beacon, site = self._setup()
        player.is_docked = True
        ship.cargo["contents"]["ore"] = 100
        with pytest.raises(WarpGateError, match="open space"):
            warp_gate_service.stage_materials(db, player, str(site.id), {"ore": 10})


# --- Accept #3: advance-construction turn cost + cure gate ------------------


@pytest.mark.unit
class TestAdvanceConstruction:
    def _setup(self, phase: int = 1, **site_overrides: Any):
        player = _fake_player()
        beacon = _fake_beacon(player.id)
        site = _fake_site(beacon.id, phase=phase, **site_overrides)
        db = _FakeSession({
            GateConstructionSite: _FakeQuery(first=site),
            WarpGateBeacon: _FakeQuery(first=beacon),
            Player: _FakeQuery(first=player),
        })
        return db, player, beacon, site

    def test_rejects_when_not_fully_staged_no_turn_charge(self) -> None:
        db, player, beacon, site = self._setup(staged_ore=PHASE1_ORE - 1, staged_equipment=PHASE1_EQUIPMENT)
        before = player.turns
        with pytest.raises(WarpGateError, match="not fully staged"):
            warp_gate_service.advance_construction(db, player, str(site.id))
        assert player.turns == before
        assert site.status == GateConstructionSiteStatus.STAGING

    def test_fully_staged_charges_exactly_5_turns_and_starts_the_cure(self) -> None:
        db, player, beacon, site = self._setup(staged_ore=PHASE1_ORE, staged_equipment=PHASE1_EQUIPMENT)
        turns_before = player.turns
        before = datetime.now(timezone.utc)
        result = warp_gate_service.advance_construction(db, player, str(site.id))
        after = datetime.now(timezone.utc)

        assert player.turns == turns_before - CONSTRUCTION_TURN_COST
        assert site.turns_applied == CONSTRUCTION_TURN_COST
        assert site.status == GateConstructionSiteStatus.CURING
        assert scaled_deadline(PHASE_CURE_HOURS, before) <= site.cure_completes_at <= scaled_deadline(PHASE_CURE_HOURS, after)
        assert result["status"] == "CURING"
        assert result["turns_applied"] == CONSTRUCTION_TURN_COST

    def test_rejects_non_owner(self) -> None:
        db, player, beacon, site = self._setup(staged_ore=PHASE1_ORE, staged_equipment=PHASE1_EQUIPMENT)
        intruder = _fake_player()
        with pytest.raises(WarpGateError):
            warp_gate_service.advance_construction(db, intruder, str(site.id))
        assert site.status == GateConstructionSiteStatus.STAGING

    def test_second_call_while_curing_rejects_with_no_additional_charge(self) -> None:
        db, player, beacon, site = self._setup(staged_ore=PHASE1_ORE, staged_equipment=PHASE1_EQUIPMENT)
        warp_gate_service.advance_construction(db, player, str(site.id))
        turns_after_first = player.turns
        with pytest.raises(WarpGateError, match="[Cc]uring"):
            warp_gate_service.advance_construction(db, player, str(site.id))
        assert player.turns == turns_after_first
        assert site.turns_applied == CONSTRUCTION_TURN_COST  # unchanged by the 2nd call

    def test_cure_elapsed_lazily_flips_ready_and_opens_phase3_site(self) -> None:
        db, player, beacon, site = self._setup(
            staged_ore=PHASE1_ORE,
            staged_equipment=PHASE1_EQUIPMENT,
            status=GateConstructionSiteStatus.CURING,
            cure_completes_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        # GateConstructionSite is queried twice inside this one call:
        # _resolve_site wants the Phase-1 site; _lazy_advance_site_cure's
        # existing-Phase-3-site check wants None (nothing opened yet).
        db._specs[GateConstructionSite] = _FakeQuery(seq=[site, None])

        with pytest.raises(WarpGateError, match="already finished curing"):
            warp_gate_service.advance_construction(db, player, str(site.id))

        assert site.status == GateConstructionSiteStatus.READY
        opened = [o for o in db.added if isinstance(o, GateConstructionSite)]
        assert len(opened) == 1
        assert opened[0].phase == 3
        assert opened[0].beacon_id == site.beacon_id
        assert opened[0].required_ore == PHASE3_ORE
        assert opened[0].required_equipment == PHASE3_EQUIPMENT
        assert opened[0].required_lumen == PHASE3_LUMEN
        assert opened[0].status == GateConstructionSiteStatus.STAGING


# --- _lazy_advance_site_cure in isolation ------------------------------------


@pytest.mark.unit
class TestLazyAdvanceSiteCure:
    def test_noop_when_not_curing(self) -> None:
        site = _fake_site(uuid.uuid4(), phase=1, status=GateConstructionSiteStatus.STAGING)
        db = _FakeSession({GateConstructionSite: _FakeQuery(first=None)})
        warp_gate_service._lazy_advance_site_cure(db, site)
        assert site.status == GateConstructionSiteStatus.STAGING
        assert db.added == []

    def test_noop_before_the_cure_elapses(self) -> None:
        site = _fake_site(
            uuid.uuid4(), phase=1, status=GateConstructionSiteStatus.CURING,
            cure_completes_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db = _FakeSession({GateConstructionSite: _FakeQuery(first=None)})
        warp_gate_service._lazy_advance_site_cure(db, site)
        assert site.status == GateConstructionSiteStatus.CURING

    def test_flips_to_ready_and_opens_phase3_when_elapsed(self) -> None:
        beacon_id = uuid.uuid4()
        site = _fake_site(
            beacon_id, phase=1, status=GateConstructionSiteStatus.CURING,
            cure_completes_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db = _FakeSession({GateConstructionSite: _FakeQuery(first=None)})
        warp_gate_service._lazy_advance_site_cure(db, site)
        assert site.status == GateConstructionSiteStatus.READY
        added = [o for o in db.added if isinstance(o, GateConstructionSite)]
        assert len(added) == 1 and added[0].phase == 3 and added[0].beacon_id == beacon_id

    def test_idempotent_does_not_duplicate_the_phase3_site(self) -> None:
        beacon_id = uuid.uuid4()
        existing_phase3 = _fake_site(beacon_id, phase=3)
        site = _fake_site(
            beacon_id, phase=1, status=GateConstructionSiteStatus.CURING,
            cure_completes_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db = _FakeSession({GateConstructionSite: _FakeQuery(first=existing_phase3)})
        warp_gate_service._lazy_advance_site_cure(db, site)
        assert site.status == GateConstructionSiteStatus.READY
        assert db.added == []  # no duplicate opened

    def test_phase3_site_reaching_ready_opens_nothing_further(self) -> None:
        site = _fake_site(
            uuid.uuid4(), phase=3, status=GateConstructionSiteStatus.CURING,
            cure_completes_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db = _FakeSession({GateConstructionSite: _FakeQuery(first=None)})
        warp_gate_service._lazy_advance_site_cure(db, site)
        assert site.status == GateConstructionSiteStatus.READY
        assert db.added == []


# --- Accept #5 + #6: refund/cancel disposition -------------------------------


@pytest.mark.unit
class TestPhase3RefundToSite:
    def test_refund_refills_the_site_not_the_ship_or_wallet(self) -> None:
        player = _fake_player(turns=50, credits=0, lumen_crystals=0)
        ship = _fake_ship(owner_id=player.id, cargo={"capacity": 200, "used": 0, "contents": {"ore": 5}})
        beacon_id = uuid.uuid4()
        gate = SimpleNamespace(
            id=uuid.uuid4(), beacon_id=beacon_id, warp_tunnel_id=None,
            status=WarpGateStatus.HARMONIZING,
            harmonization_completes_at=datetime.now(timezone.utc),
        )
        site = _fake_site(
            beacon_id, phase=3,
            staged_ore=0, staged_equipment=0, staged_lumen=0,
            status=GateConstructionSiteStatus.CONSUMED, gate_id=gate.id,
        )
        db = _FakeSession({GateConstructionSite: _FakeQuery(first=site)})

        refund = warp_gate_service._refund_phase3_and_cancel(db, gate, ship, player)

        assert player.turns == 50 + warp_gate_service.PHASE3_TURNS
        assert player.credits == warp_gate_service.PHASE3_CREDITS
        assert player.lumen_crystals == 0  # lumen refunds to the SITE, not the wallet, on this path
        assert site.staged_ore == PHASE3_ORE
        assert site.staged_equipment == PHASE3_EQUIPMENT
        assert site.staged_lumen == PHASE3_LUMEN
        assert site.status == GateConstructionSiteStatus.READY
        assert site.gate_id is None
        # The ship's hold is untouched -- materials never left it under the
        # staging model (they left the SITE, at anchor_focus's commit).
        assert ship.cargo["contents"].get("ore", 0) == 5
        assert ship.status == ShipStatus.IN_SPACE
        assert ship.harmonization_completes_at is None
        assert gate.status == WarpGateStatus.CANCELLED
        assert refund["ore"] == PHASE3_ORE
        assert refund["equipment"] == PHASE3_EQUIPMENT
        assert refund["lumen_crystals"] == PHASE3_LUMEN


@pytest.mark.unit
class TestDisposeBeaconConstructionSites:
    def test_phase1_staged_materials_are_sunk_not_returned(self) -> None:
        player = _fake_player()
        beacon = _fake_beacon(player.id)
        phase1_site = _fake_site(beacon.id, phase=1, staged_ore=400, staged_equipment=100)
        db = _FakeSession({GateConstructionSite: _FakeQuery(all=[phase1_site])})

        returned = warp_gate_service._dispose_beacon_construction_sites(db, player, beacon)

        assert returned == {"ore": 0, "equipment": 0, "lumen_crystals": 0}
        assert phase1_site.status == GateConstructionSiteStatus.CANCELLED
        assert phase1_site.staged_ore == 400  # sunk, left as historical record

    def test_phase3_materials_return_to_ship_hold_within_capacity(self) -> None:
        player = _fake_player()
        ship = _fake_ship(owner_id=player.id, cargo={"capacity": 1000, "used": 700, "contents": {}})
        player.current_ship_id = ship.id
        beacon = _fake_beacon(player.id)
        phase3_site = _fake_site(beacon.id, phase=3, staged_ore=200, staged_equipment=50, staged_lumen=10)
        db = _FakeSession({
            GateConstructionSite: _FakeQuery(all=[phase3_site]),
            Ship: _FakeQuery(first=ship),
        })

        returned = warp_gate_service._dispose_beacon_construction_sites(db, player, beacon)

        assert returned == {"ore": 200, "equipment": 50, "lumen_crystals": 10}
        assert phase3_site.status == GateConstructionSiteStatus.CANCELLED
        assert phase3_site.staged_ore == 0
        assert phase3_site.staged_equipment == 0
        assert phase3_site.staged_lumen == 0
        assert ship.cargo["contents"]["ore"] == 200
        assert ship.cargo["contents"]["equipment"] == 50
        assert player.lumen_crystals == 10

    def test_phase3_materials_beyond_capacity_are_forfeited(self) -> None:
        player = _fake_player()
        ship = _fake_ship(owner_id=player.id, cargo={"capacity": 100, "used": 90, "contents": {}})
        player.current_ship_id = ship.id
        beacon = _fake_beacon(player.id)
        phase3_site = _fake_site(beacon.id, phase=3, staged_ore=200, staged_equipment=50, staged_lumen=10)
        db = _FakeSession({
            GateConstructionSite: _FakeQuery(all=[phase3_site]),
            Ship: _FakeQuery(first=ship),
        })

        returned = warp_gate_service._dispose_beacon_construction_sites(db, player, beacon)

        # Only 10 units of hold room total; ore is applied first (dict
        # insertion order), soaking all of it -- equipment gets nothing.
        assert returned["ore"] == 10
        assert returned.get("equipment", 0) == 0
        assert returned["lumen_crystals"] == 10  # a wallet ledger always refunds in full
        assert phase3_site.staged_ore == 190  # the rest is forfeited
        assert phase3_site.staged_equipment == 50
        assert phase3_site.status == GateConstructionSiteStatus.CANCELLED
        assert player.lumen_crystals == 10

    def test_phase3_materials_forfeited_entirely_with_no_active_ship(self) -> None:
        player = _fake_player(current_ship_id=None)
        beacon = _fake_beacon(player.id)
        phase3_site = _fake_site(beacon.id, phase=3, staged_ore=50, staged_equipment=20, staged_lumen=5)
        db = _FakeSession({GateConstructionSite: _FakeQuery(all=[phase3_site])})

        returned = warp_gate_service._dispose_beacon_construction_sites(db, player, beacon)

        assert returned["ore"] == 0
        assert returned["equipment"] == 0
        assert returned["lumen_crystals"] == 5
        assert phase3_site.staged_ore == 50  # forfeited
        assert phase3_site.status == GateConstructionSiteStatus.CANCELLED


@pytest.mark.unit
class TestCancelBeaconEndToEnd:
    def test_cancel_sinks_phase1_and_returns_staged_phase3_materials(self) -> None:
        player = _fake_player(turns=100, credits=0, lumen_crystals=0)
        ship = _fake_ship(owner_id=player.id, cargo={"capacity": 1000, "used": 0, "contents": {}})
        player.current_ship_id = ship.id
        beacon = _fake_beacon(player.id)
        phase1_site = _fake_site(
            beacon.id, phase=1, staged_ore=PHASE1_ORE, staged_equipment=PHASE1_EQUIPMENT,
            status=GateConstructionSiteStatus.READY,
        )
        phase3_site = _fake_site(beacon.id, phase=3, staged_ore=300, staged_equipment=100, staged_lumen=15)

        db = _FakeSession({
            WarpGate: _FakeQuery(first=None),  # not a gate id; also satisfies the in-progress check
            WarpGateBeacon: _FakeQuery(first=beacon),
            Player: _FakeQuery(first=player),
            GateConstructionSite: _FakeQuery(all=[phase1_site, phase3_site]),
            Ship: _FakeQuery(first=ship),
        })

        result = warp_gate_service.cancel(db, player, str(beacon.id))

        assert result["cancelled"] == "beacon"
        assert result["refunded"] == {"ore": 300, "equipment": 100, "lumen_crystals": 15}
        assert beacon.status == WarpGateBeaconStatus.CANCELLED
        assert phase1_site.status == GateConstructionSiteStatus.CANCELLED
        assert phase1_site.staged_ore == PHASE1_ORE  # sunk, untouched
        assert phase3_site.status == GateConstructionSiteStatus.CANCELLED
        assert ship.cargo["contents"]["ore"] == 300
        assert ship.cargo["contents"]["equipment"] == 100
        assert player.lumen_crystals == 15


# --- Accept #4: migration is additive-only -----------------------------------

_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "a3f9e1c74b28_add_gate_construction_sites.py"
)


@pytest.mark.unit
class TestMigrationIsAdditiveOnly:
    def test_migration_file_exists(self) -> None:
        assert _MIGRATION_PATH.is_file()

    def test_upgrade_only_creates_one_new_table_and_its_index(self) -> None:
        source = _MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        upgrade_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade")
        upgrade_src = ast.get_source_segment(source, upgrade_fn) or ""
        assert upgrade_src.count("op.create_table(") == 1
        assert upgrade_src.count("op.create_index(") == 1
        for banned in ("op.alter_column", "op.drop_column", "op.add_column", "op.drop_table"):
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
        assert assigns.get("down_revision") == "f4a8c1e6d930"
