"""LEG-4176 / LEG-4197 — GET /admin/sectors/{sector_id}/pirate-holdings."""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.params import Depends

from src.api.routes import admin as admin_mod
from src.api.routes.admin import (
    _ADMIN_PIRATE_HOLDING_FIELDS,
    _admin_pirate_holding_payload,
    get_sector_pirate_holdings,
)
from src.auth.admin_scopes import PLAYERS_VIEW
from src.models.pirate_holding import PirateHolding, PirateHoldingTier


def _holding(**overrides):
    hid = overrides.pop("id", uuid.uuid4())
    region_id = overrides.pop("region_id", uuid.uuid4())
    owner_player_id = overrides.pop("owner_player_id", None)
    owner_team_id = overrides.pop("owner_team_id", None)
    captured_at = overrides.pop("captured_at", None)
    combat_lock_held_by = overrides.pop("combat_lock_held_by", None)
    outlaw_base_id = overrides.pop("outlaw_base_id", None)
    return SimpleNamespace(
        id=hid,
        tier=overrides.pop("tier", PirateHoldingTier.OUTPOST),
        sector_id=overrides.pop("sector_id", 42),
        region_id=region_id,
        owner_player_id=owner_player_id,
        owner_team_id=owner_team_id,
        captured_at=captured_at,
        combat_lock_held_by=combat_lock_held_by,
        current_strength=overrides.pop("current_strength", 0.75),
        outlaw_base_id=outlaw_base_id,
        **overrides,
    )


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.queried_model = None

    def query(self, model):
        self.queried_model = model
        return _FakeQuery(self._rows)


@pytest.mark.asyncio
async def test_empty_sector_returns_200_shape_empty_list():
    db = _FakeSession([])
    result = await get_sector_pirate_holdings(
        sector_id=99,
        _=SimpleNamespace(id=uuid.uuid4()),
        db=db,
    )
    assert result == {"holdings": []}
    assert db.queried_model is PirateHolding


@pytest.mark.asyncio
async def test_present_holdings_committed_columns_only():
    hid = uuid.uuid4()
    region_id = uuid.uuid4()
    owner = uuid.uuid4()
    team = uuid.uuid4()
    locker = uuid.uuid4()
    outlaw_base = uuid.uuid4()
    captured = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
    holding = _holding(
        id=hid,
        sector_id=42,
        region_id=region_id,
        owner_player_id=owner,
        owner_team_id=team,
        captured_at=captured,
        combat_lock_held_by=locker,
        current_strength=0.4,
        tier=PirateHoldingTier.CAMP,
        outlaw_base_id=outlaw_base,
    )
    db = _FakeSession([holding])

    result = await get_sector_pirate_holdings(
        sector_id=42,
        _=SimpleNamespace(id=uuid.uuid4()),
        db=db,
    )

    assert list(result.keys()) == ["holdings"]
    assert len(result["holdings"]) == 1
    item = result["holdings"][0]
    assert set(item.keys()) == set(_ADMIN_PIRATE_HOLDING_FIELDS)
    assert item == {
        "id": str(hid),
        "tier": "CAMP",
        "sector_id": 42,
        "region_id": str(region_id),
        "owner_player_id": str(owner),
        "owner_team_id": str(team),
        "captured_at": captured.isoformat(),
        "combat_lock_held_by": str(locker),
        "current_strength": 0.4,
        "outlaw_base_id": str(outlaw_base),
    }


def test_payload_helper_includes_outlaw_base_id_when_set():
    base_id = uuid.uuid4()
    payload = _admin_pirate_holding_payload(_holding(outlaw_base_id=base_id))
    assert payload["outlaw_base_id"] == str(base_id)
    assert set(payload.keys()) == set(_ADMIN_PIRATE_HOLDING_FIELDS)


def test_payload_helper_null_outlaw_base_id_when_unset():
    payload = _admin_pirate_holding_payload(_holding())
    assert "outlaw_base_id" in payload
    assert payload["outlaw_base_id"] is None
    assert set(payload.keys()) == set(_ADMIN_PIRATE_HOLDING_FIELDS)


def test_route_registered_and_gated_players_view():
    match = None
    for r in admin_mod.router.routes:
        if getattr(r, "path", None) == "/sectors/{sector_id}/pirate-holdings":
            if "GET" in getattr(r, "methods", set()):
                match = r
                break
    assert match is not None

    sig = inspect.signature(get_sector_pirate_holdings)
    admin_dep = sig.parameters["_"].default
    assert isinstance(admin_dep, Depends)
    closure = admin_dep.dependency.__closure__
    assert closure is not None
    closed = {cell.cell_contents for cell in closure}
    assert PLAYERS_VIEW in closed


def test_route_source_includes_outlaw_base_id_and_stays_gated():
    src = Path(admin_mod.__file__).read_text()
    assert "outlaw_base_id" in src
    assert "_admin_pirate_holding_payload" in src
    assert "/sectors/{sector_id}/pirate-holdings" in src
    assert "require_scope(PLAYERS_VIEW)" in src
