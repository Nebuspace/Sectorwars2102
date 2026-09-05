"""LEG-4211 — GET /admin/pirate-holdings?owner_player_id=…"""
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
    get_pirate_holdings_by_owner,
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
        self._rows = list(rows)
        self.filter_args = []

    def filter(self, *args, **kwargs):
        self.filter_args.extend(args)
        for expr in args:
            try:
                key = expr.left.key
                rhs = expr.right
                val = getattr(rhs, "value", rhs)
                self._rows = [r for r in self._rows if getattr(r, key, None) == val]
            except Exception:
                pass
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.queried_model = None
        self.last_query = None

    def query(self, model):
        self.queried_model = model
        self.last_query = _FakeQuery(self._rows)
        return self.last_query


@pytest.mark.asyncio
async def test_empty_owner_returns_200_empty_list():
    owner = uuid.uuid4()
    db = _FakeSession([])
    result = await get_pirate_holdings_by_owner(
        owner_player_id=owner,
        _=SimpleNamespace(id=uuid.uuid4()),
        db=db,
    )
    assert result == {"holdings": []}
    assert db.queried_model is PirateHolding


@pytest.mark.asyncio
async def test_filter_returns_only_matching_owner():
    owner = uuid.uuid4()
    other = uuid.uuid4()
    hid = uuid.uuid4()
    region_id = uuid.uuid4()
    outlaw_base = uuid.uuid4()
    captured = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
    match = _holding(
        id=hid,
        owner_player_id=owner,
        region_id=region_id,
        outlaw_base_id=outlaw_base,
        captured_at=captured,
        tier=PirateHoldingTier.CAMP,
        current_strength=0.4,
    )
    mismatch = _holding(owner_player_id=other)
    unowned = _holding(owner_player_id=None)
    db = _FakeSession([match, mismatch, unowned])

    result = await get_pirate_holdings_by_owner(
        owner_player_id=owner,
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
        "owner_team_id": None,
        "captured_at": captured.isoformat(),
        "combat_lock_held_by": None,
        "current_strength": 0.4,
        "outlaw_base_id": str(outlaw_base),
    }
    assert db.last_query is not None
    assert len(db.last_query.filter_args) == 1


def test_payload_helper_shape_unchanged():
    base_id = uuid.uuid4()
    payload = _admin_pirate_holding_payload(_holding(outlaw_base_id=base_id))
    assert payload["outlaw_base_id"] == str(base_id)
    assert set(payload.keys()) == set(_ADMIN_PIRATE_HOLDING_FIELDS)


def test_route_registered_and_gated_players_view():
    match = None
    for r in admin_mod.router.routes:
        if getattr(r, "path", None) == "/pirate-holdings":
            if "GET" in getattr(r, "methods", set()):
                match = r
                break
    assert match is not None

    sig = inspect.signature(get_pirate_holdings_by_owner)
    admin_dep = sig.parameters["_"].default
    assert isinstance(admin_dep, Depends)
    closure = admin_dep.dependency.__closure__
    assert closure is not None
    closed = {cell.cell_contents for cell in closure}
    assert PLAYERS_VIEW in closed


def test_route_source_reuses_payload_helper():
    src = Path(admin_mod.__file__).read_text()
    assert "get_pirate_holdings_by_owner" in src
    assert '"/pirate-holdings"' in src or "'/pirate-holdings'" in src
    assert "owner_player_id" in src
    assert "_admin_pirate_holding_payload" in src
