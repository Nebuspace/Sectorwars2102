"""Unit pins for npc_lodging_service occupancy + barracks shielding."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.services.npc_lodging_service import (
    DOCKED_OFF_DUTY,
    ERR_NPC_SHIP_AT_BARRACKS,
    apply_roster_lodging_to_npc,
    enter_sleep,
    is_npc_ship_barracks_shielded,
    leave_sleep,
)


def test_apply_roster_lodging_barracks():
    npc = SimpleNamespace(home_barracks_id=None, home_outlaw_base_id=None)
    lid = uuid.uuid4()
    roster = SimpleNamespace(default_lodging_id=lid, default_lodging_type="barracks")
    apply_roster_lodging_to_npc(npc, roster)
    assert npc.home_barracks_id == lid
    assert npc.home_outlaw_base_id is None


def test_apply_roster_lodging_outlaw():
    npc = SimpleNamespace(home_barracks_id=None, home_outlaw_base_id=None)
    lid = uuid.uuid4()
    roster = SimpleNamespace(default_lodging_id=lid, default_lodging_type="outlaw_base")
    apply_roster_lodging_to_npc(npc, roster)
    assert npc.home_outlaw_base_id == lid
    assert npc.home_barracks_id is None


def test_enter_leave_sleep_updates_occupancy_and_docked():
    npc_id = uuid.uuid4()
    ship_id = uuid.uuid4()
    barracks_id = uuid.uuid4()
    sector_id = 42

    lodging = SimpleNamespace(
        id=barracks_id,
        capacity=9,
        current_occupants_count=0,
        assigned_npc_ids=[],
    )
    sector = SimpleNamespace(sector_id=sector_id, defenses={})
    npc = SimpleNamespace(
        id=npc_id,
        ship_id=ship_id,
        current_sector_id=sector_id,
        home_barracks_id=barracks_id,
        home_outlaw_base_id=None,
    )

    db = MagicMock()

    def query_side(model):
        q = MagicMock()
        name = getattr(model, "__name__", str(model))
        if name == "NPCBarracks" or "npc_barracks" in str(model).lower():
            q.filter.return_value.first.return_value = lodging
        elif name == "Sector" or "sector" in str(model).lower():
            q.filter.return_value.first.return_value = sector
        else:
            q.filter.return_value.first.return_value = None
        return q

    db.query.side_effect = query_side

    # Patch resolve via real enter_sleep — it queries NPCBarracks by class.
    from src.models.npc_barracks import NPCBarracks
    from src.models.sector import Sector

    def query_real(model):
        q = MagicMock()
        if model is NPCBarracks:
            q.filter.return_value.first.return_value = lodging
        elif model is Sector:
            q.filter.return_value.first.return_value = sector
        else:
            q.filter.return_value.first.return_value = None
        return q

    db.query.side_effect = query_real

    assert enter_sleep(db, npc) is True
    assert str(npc_id) in lodging.assigned_npc_ids
    assert lodging.current_occupants_count == 1
    docked = sector.defenses["docked_npc_ships"]
    assert len(docked) == 1
    assert docked[0]["status"] == DOCKED_OFF_DUTY
    assert docked[0]["ship_id"] == str(ship_id)

    assert leave_sleep(db, npc) is True
    assert lodging.assigned_npc_ids == []
    assert lodging.current_occupants_count == 0
    assert sector.defenses["docked_npc_ships"] == []


def test_barracks_shield_constant():
    assert ERR_NPC_SHIP_AT_BARRACKS == "ERR_NPC_SHIP_AT_BARRACKS"


def test_is_npc_ship_barracks_shielded_true():
    ship_id = uuid.uuid4()
    npc_id = uuid.uuid4()
    sector_id = 7
    npc = SimpleNamespace(
        id=npc_id, ship_id=ship_id, current_sector_id=sector_id
    )
    sector = SimpleNamespace(
        sector_id=sector_id,
        defenses={
            "docked_npc_ships": [
                {
                    "npc_id": str(npc_id),
                    "ship_id": str(ship_id),
                    "status": DOCKED_OFF_DUTY,
                }
            ]
        },
    )
    db = MagicMock()
    from src.models.npc_character import NPCCharacter
    from src.models.sector import Sector

    def query_real(model):
        q = MagicMock()
        if model is NPCCharacter:
            q.filter.return_value.first.return_value = npc
        elif model is Sector:
            q.filter.return_value.first.return_value = sector
        else:
            q.filter.return_value.first.return_value = None
        return q

    db.query.side_effect = query_real
    assert is_npc_ship_barracks_shielded(db, ship_id) is True


def test_sync_sector_lodging_flags_sets_and_clears():
    """OutlawBase / sector-location NPCBarracks drive Sector flags."""
    from src.models.npc_barracks import NPCBarracks
    from src.models.outlaw_base import OutlawBase
    from src.models.sector import Sector
    from src.services.npc_lodging_service import sync_sector_lodging_flags

    sector_outlaw = SimpleNamespace(
        sector_id=10, is_outlaw_zone=False, is_npc_barracks_sector=False
    )
    sector_barracks = SimpleNamespace(
        sector_id=20, is_outlaw_zone=False, is_npc_barracks_sector=False
    )
    sector_stale = SimpleNamespace(
        sector_id=30, is_outlaw_zone=True, is_npc_barracks_sector=True
    )
    sectors = {10: sector_outlaw, 20: sector_barracks, 30: sector_stale}
    pending = {"sid": None}

    db = MagicMock()

    def query_real(model):
        q = MagicMock()
        q.filter.return_value = q
        if model is OutlawBase.sector_id:
            q.all.return_value = [(10,)]
            return q
        if model is NPCBarracks.sector_id:
            q.all.return_value = [(20,)]
            return q
        if model is Sector.sector_id:
            q.all.return_value = [(30,)]
            return q
        if model is Sector:
            def _filter(*args, **kwargs):
                for a in args:
                    right = getattr(a, "right", None)
                    val = getattr(right, "value", None)
                    if isinstance(val, int):
                        pending["sid"] = val
                return q

            q.filter.side_effect = _filter
            q.all.return_value = [(30,)]
            q.first.side_effect = lambda: sectors.get(pending["sid"])
            return q
        q.all.return_value = []
        q.first.return_value = None
        return q

    db.query.side_effect = query_real
    stats = sync_sector_lodging_flags(db)
    assert sector_outlaw.is_outlaw_zone is True
    assert sector_outlaw.is_npc_barracks_sector is False
    assert sector_barracks.is_outlaw_zone is False
    assert sector_barracks.is_npc_barracks_sector is True
    assert sector_stale.is_outlaw_zone is False
    assert sector_stale.is_npc_barracks_sector is False
    assert stats["sectors_updated"] == 3


def test_refresh_sector_lodging_flags_scoped():
    from src.models.npc_barracks import NPCBarracks
    from src.models.outlaw_base import OutlawBase
    from src.models.sector import Sector
    from src.services.npc_lodging_service import refresh_sector_lodging_flags

    sector = SimpleNamespace(
        sector_id=99, is_outlaw_zone=False, is_npc_barracks_sector=False
    )
    db = MagicMock()

    def query_real(model):
        q = MagicMock()
        q.filter.return_value = q
        if model is OutlawBase.sector_id:
            q.all.return_value = [(99,)]
            return q
        if model is NPCBarracks.sector_id:
            q.all.return_value = []
            return q
        if model is Sector:
            q.first.return_value = sector
            return q
        q.all.return_value = []
        q.first.return_value = None
        return q

    db.query.side_effect = query_real
    stats = refresh_sector_lodging_flags(db, 99)
    assert sector.is_outlaw_zone is True
    assert stats["sectors_checked"] == 1
