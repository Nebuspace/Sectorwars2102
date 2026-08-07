"""DB-free unit coverage for ADR-0054 X-D1 -- the suspended-region
stakeholder-ingress rule.

Two layers, mirroring test_movement_nexus_gate.py's proven convention for
the sibling ADR-0043 traversal gate:
  (1) TestIsRegionStakeholder -- direct coverage of ``region_lifecycle_
      service.is_region_stakeholder`` for each of the five asset classes
      plus the non-stakeholder negative, using a FakeSession that
      distinguishes a plain ``db.query(Player)`` ownership/team lookup
      from the function's five short-circuiting ``db.query(exists().
      where(...)).scalar()`` calls (order-sequenced, since each call site
      is reached in a fixed order and the function returns True on the
      first hit).
  (2) TestRegionIngressGateWiring -- the movement_service ingress gate
      itself: outbound always allowed regardless of status; inbound
      allowed for the region owner and for a stakeholder; inbound
      rejected for a non-stakeholder while suspended/grace; the gate
      doesn't fire at all outside suspended/grace (even for a non-
      stakeholder).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from src.models.player import Player
from src.models.region import Region, RegionStatus
from src.models.sector import Sector
from src.services import region_lifecycle_service
from src.services.movement_service import MovementService

# --------------------------------------------------------------------------- #
# (1) is_region_stakeholder -- direct coverage.
# --------------------------------------------------------------------------- #

class _StakeholderFakeSession:
    """``db.query(Player)`` returns a canned player (for the team_id
    lookup ahead of the holding check); any other ``db.query(...)`` call
    is one of the function's five ``exists()``-scalar checks, answered in
    call order from ``scalar_seq``."""

    def __init__(self, *, player=None, scalar_seq):
        self._player = player
        self._scalar_seq = list(scalar_seq)

    def query(self, arg):
        if arg is Player:
            return _PlayerQuery(self._player)
        return _ScalarQuery(self._scalar_seq)


class _PlayerQuery:
    def __init__(self, player):
        self._player = player

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._player


class _ScalarQuery:
    def __init__(self, seq):
        self._seq = seq

    def scalar(self):
        # Every check not yet reached (because an earlier one already
        # short-circuited True) must never be called -- pop() raising
        # IndexError on an exhausted seq is the intentional failure mode.
        return self._seq.pop(0)


def _player(*, team_id=None) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), team_id=team_id)


class TestIsRegionStakeholder:
    def test_planet_owner_is_a_stakeholder(self):
        db = _StakeholderFakeSession(scalar_seq=[True])
        assert region_lifecycle_service.is_region_stakeholder(
            db, uuid.uuid4(), uuid.uuid4()
        ) is True

    def test_station_owner_is_a_stakeholder(self):
        db = _StakeholderFakeSession(scalar_seq=[False, True])
        assert region_lifecycle_service.is_region_stakeholder(
            db, uuid.uuid4(), uuid.uuid4()
        ) is True

    def test_individual_holding_capture_is_a_stakeholder(self):
        db = _StakeholderFakeSession(
            player=_player(team_id=None), scalar_seq=[False, False, True],
        )
        assert region_lifecycle_service.is_region_stakeholder(
            db, uuid.uuid4(), uuid.uuid4()
        ) is True

    def test_team_holding_capture_is_a_stakeholder(self):
        """A holding captured by a TEAMMATE (owner_team_id, not owner_
        player_id) still counts -- the ADR's 'or via team' framing."""
        db = _StakeholderFakeSession(
            player=_player(team_id=uuid.uuid4()), scalar_seq=[False, False, True],
        )
        assert region_lifecycle_service.is_region_stakeholder(
            db, uuid.uuid4(), uuid.uuid4()
        ) is True

    def test_warp_gate_endpoint_owner_is_a_stakeholder(self):
        db = _StakeholderFakeSession(
            player=_player(), scalar_seq=[False, False, False, True],
        )
        assert region_lifecycle_service.is_region_stakeholder(
            db, uuid.uuid4(), uuid.uuid4()
        ) is True

    def test_ship_presence_is_a_stakeholder(self):
        db = _StakeholderFakeSession(
            player=_player(), scalar_seq=[False, False, False, False, True],
        )
        assert region_lifecycle_service.is_region_stakeholder(
            db, uuid.uuid4(), uuid.uuid4()
        ) is True

    def test_non_stakeholder_across_all_five_classes_is_rejected(self):
        db = _StakeholderFakeSession(
            player=_player(), scalar_seq=[False, False, False, False, False],
        )
        assert region_lifecycle_service.is_region_stakeholder(
            db, uuid.uuid4(), uuid.uuid4()
        ) is False

    def test_short_circuits_on_first_hit_never_reaching_later_checks(self):
        """A planet-owner hit on check 1 must never trigger checks 2-5 --
        an exhausted ``scalar_seq`` after the first pop proves this: any
        extra pop() would raise IndexError and fail the test."""
        db = _StakeholderFakeSession(scalar_seq=[True])
        assert region_lifecycle_service.is_region_stakeholder(
            db, uuid.uuid4(), uuid.uuid4()
        ) is True


# --------------------------------------------------------------------------- #
# (2) The movement_service ingress gate.
# --------------------------------------------------------------------------- #

class _GateFakeQuery:
    def __init__(self, *, first=None, seq=None):
        self._first = first
        self._seq = list(seq) if seq is not None else None

    def filter(self, *a, **k):
        return self

    def first(self):
        if self._seq is not None:
            return self._seq.pop(0) if self._seq else None
        return self._first


class _GateFakeSession:
    def __init__(self, specs):
        self._specs = specs

    def query(self, model):
        assert model in self._specs, f"unexpected query for {model!r}"
        return self._specs[model]


def _region(*, status, owner_id=None) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), status=status, owner_id=owner_id)


def _sector(*, sector_id: int, region_id) -> SimpleNamespace:
    return SimpleNamespace(sector_id=sector_id, region_id=region_id)


def _mover(*, current_region_id, user_id=None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), user_id=user_id or uuid.uuid4(),
        current_region_id=current_region_id,
    )


class TestRegionIngressGateWiring:
    def test_outbound_always_allowed_regardless_of_status(self, monkeypatch):
        """Leaving a suspended/grace region -- origin == suspended, dest
        elsewhere -- is never gated, per the ADR's 'Outbound: always
        allowed' rule."""
        origin_region = _region(status=RegionStatus.SUSPENDED)
        other_region = _region(status=RegionStatus.ACTIVE)
        destination_sector = _sector(sector_id=2001, region_id=other_region.id)
        player = _mover(current_region_id=origin_region.id)

        db = _GateFakeSession({
            Region: _GateFakeQuery(first=other_region),
            Sector: _GateFakeQuery(first=destination_sector),
        })
        monkeypatch.setattr(
            "src.services.region_lifecycle_service.is_region_stakeholder",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called")),
        )

        service = MovementService(db)
        result = service._check_region_ingress_gate(player, 2001)
        assert result is None

    def test_inbound_allowed_for_active_destination_never_gates(self, monkeypatch):
        origin_region = _region(status=RegionStatus.ACTIVE)
        destination_region = _region(status=RegionStatus.ACTIVE)
        destination_sector = _sector(sector_id=2001, region_id=destination_region.id)
        player = _mover(current_region_id=origin_region.id)

        db = _GateFakeSession({
            Region: _GateFakeQuery(first=destination_region),
            Sector: _GateFakeQuery(first=destination_sector),
        })
        monkeypatch.setattr(
            "src.services.region_lifecycle_service.is_region_stakeholder",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called")),
        )

        service = MovementService(db)
        result = service._check_region_ingress_gate(player, 2001)
        assert result is None

    def test_inbound_allowed_for_region_owner(self, monkeypatch):
        owner_user_id = uuid.uuid4()
        origin_region = _region(status=RegionStatus.ACTIVE)
        destination_region = _region(status=RegionStatus.GRACE, owner_id=owner_user_id)
        destination_sector = _sector(sector_id=2001, region_id=destination_region.id)
        player = _mover(current_region_id=origin_region.id, user_id=owner_user_id)

        db = _GateFakeSession({
            Region: _GateFakeQuery(first=destination_region),
            Sector: _GateFakeQuery(first=destination_sector),
        })
        monkeypatch.setattr(
            "src.services.region_lifecycle_service.is_region_stakeholder",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called")),
        )

        service = MovementService(db)
        result = service._check_region_ingress_gate(player, 2001)
        assert result is None

    def test_inbound_allowed_for_a_stakeholder(self, monkeypatch):
        origin_region = _region(status=RegionStatus.ACTIVE)
        destination_region = _region(status=RegionStatus.SUSPENDED, owner_id=uuid.uuid4())
        destination_sector = _sector(sector_id=2001, region_id=destination_region.id)
        player = _mover(current_region_id=origin_region.id)

        db = _GateFakeSession({
            Region: _GateFakeQuery(first=destination_region),
            Sector: _GateFakeQuery(first=destination_sector),
        })
        monkeypatch.setattr(
            "src.services.region_lifecycle_service.is_region_stakeholder",
            lambda *a, **k: True,
        )

        service = MovementService(db)
        result = service._check_region_ingress_gate(player, 2001)
        assert result is None

    def test_inbound_rejected_for_a_non_stakeholder_while_suspended(self, monkeypatch):
        origin_region = _region(status=RegionStatus.ACTIVE)
        destination_region = _region(status=RegionStatus.SUSPENDED, owner_id=uuid.uuid4())
        destination_sector = _sector(sector_id=2001, region_id=destination_region.id)
        player = _mover(current_region_id=origin_region.id)

        db = _GateFakeSession({
            Region: _GateFakeQuery(first=destination_region),
            Sector: _GateFakeQuery(first=destination_sector),
        })
        monkeypatch.setattr(
            "src.services.region_lifecycle_service.is_region_stakeholder",
            lambda *a, **k: False,
        )

        service = MovementService(db)
        result = service._check_region_ingress_gate(player, 2001)
        assert result is not None
        assert result["success"] is False
        assert result["message"] == "ERR_REGION_NEW_RESIDENTS_BLOCKED"
        assert result["error"] == "ERR_REGION_NEW_RESIDENTS_BLOCKED"
        assert result["turn_cost"] == 0

    def test_inbound_rejected_for_a_non_stakeholder_while_grace(self, monkeypatch):
        origin_region = _region(status=RegionStatus.ACTIVE)
        destination_region = _region(status=RegionStatus.GRACE, owner_id=uuid.uuid4())
        destination_sector = _sector(sector_id=2001, region_id=destination_region.id)
        player = _mover(current_region_id=origin_region.id)

        db = _GateFakeSession({
            Region: _GateFakeQuery(first=destination_region),
            Sector: _GateFakeQuery(first=destination_sector),
        })
        monkeypatch.setattr(
            "src.services.region_lifecycle_service.is_region_stakeholder",
            lambda *a, **k: False,
        )

        service = MovementService(db)
        result = service._check_region_ingress_gate(player, 2001)
        assert result is not None
        assert result["message"] == "ERR_REGION_NEW_RESIDENTS_BLOCKED"

    def test_same_region_movement_is_never_gated(self, monkeypatch):
        """Origin == destination region (an in-region move while the
        region happens to be suspended) is not cross-region ingress at
        all -- must never consult stakeholder status."""
        region = _region(status=RegionStatus.SUSPENDED)
        destination_sector = _sector(sector_id=2001, region_id=region.id)
        player = _mover(current_region_id=region.id)

        db = _GateFakeSession({
            Region: _GateFakeQuery(first=region),
            Sector: _GateFakeQuery(first=destination_sector),
        })
        monkeypatch.setattr(
            "src.services.region_lifecycle_service.is_region_stakeholder",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called")),
        )

        service = MovementService(db)
        result = service._check_region_ingress_gate(player, 2001)
        assert result is None
