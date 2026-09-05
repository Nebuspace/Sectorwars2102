"""LEG-4210 — GET /admin/outlaw-bases/{base_id} read-only inspect."""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.params import Depends

from src.api.routes import admin as admin_mod
from src.api.routes.admin import (
    _ADMIN_OUTLAW_BASE_FIELDS,
    _admin_outlaw_base_payload,
    get_admin_outlaw_base,
)
from src.auth.admin_scopes import PLAYERS_VIEW
from src.models.npc_character import NPCArchetype
from src.models.outlaw_base import OutlawBase


def _base(**overrides):
    bid = overrides.pop("id", uuid.uuid4())
    return SimpleNamespace(
        id=bid,
        name=overrides.pop("name", "Black Anchor"),
        sector_id=overrides.pop("sector_id", 77),
        home_region_id=overrides.pop("home_region_id", uuid.uuid4()),
        faction_code=overrides.pop("faction_code", "OUTLAW"),
        archetype=overrides.pop("archetype", NPCArchetype.HOSTILE_RAIDER),
        capacity=overrides.pop("capacity", 12),
        current_occupants_count=overrides.pop("current_occupants_count", 3),
        is_player_discoverable=overrides.pop("is_player_discoverable", True),
        raid_cooldown_until=overrides.pop("raid_cooldown_until", None),
        last_raided_at=overrides.pop("last_raided_at", None),
        relocation_pending=overrides.pop("relocation_pending", False),
        # Deferred / non-inspect columns — must never appear in payload
        assigned_npc_ids=overrides.pop("assigned_npc_ids", []),
        composition_profile=overrides.pop("composition_profile", {"bogus": True}),
        interior_sector_ids=overrides.pop("interior_sector_ids", [1, 2]),
        parent_holding_id=overrides.pop("parent_holding_id", uuid.uuid4()),
        **overrides,
    )


class _FakeQuery:
    def __init__(self, row):
        self._row = row
        self.filter_args = []

    def filter(self, *args, **kwargs):
        self.filter_args.extend(args)
        for expr in args:
            try:
                key = expr.left.key
                rhs = expr.right
                val = getattr(rhs, "value", rhs)
                if self._row is None or getattr(self._row, key, None) != val:
                    self._row = None
            except Exception:
                pass
        return self

    def first(self):
        return self._row


class _FakeSession:
    def __init__(self, row):
        self._row = row
        self.queried_model = None
        self.last_query = None

    def query(self, model):
        self.queried_model = model
        self.last_query = _FakeQuery(self._row)
        return self.last_query


@pytest.mark.asyncio
async def test_present_base_returns_committed_fields_only():
    bid = uuid.uuid4()
    region_id = uuid.uuid4()
    cooldown = datetime(2026, 9, 4, 1, 0, tzinfo=timezone.utc)
    raided = datetime(2026, 9, 3, 18, 0, tzinfo=timezone.utc)
    row = _base(
        id=bid,
        home_region_id=region_id,
        raid_cooldown_until=cooldown,
        last_raided_at=raided,
        relocation_pending=True,
        capacity=9,
        current_occupants_count=2,
        is_player_discoverable=False,
        faction_code="RENEGADE",
        name="Dust Haven",
        sector_id=101,
    )
    db = _FakeSession(row)

    result = await get_admin_outlaw_base(
        base_id=bid,
        _=SimpleNamespace(id=uuid.uuid4()),
        db=db,
    )

    assert set(result.keys()) == set(_ADMIN_OUTLAW_BASE_FIELDS)
    assert result == {
        "id": str(bid),
        "name": "Dust Haven",
        "sector_id": 101,
        "home_region_id": str(region_id),
        "faction_code": "RENEGADE",
        "archetype": "HOSTILE_RAIDER",
        "capacity": 9,
        "current_occupants_count": 2,
        "is_player_discoverable": False,
        "raid_cooldown_until": cooldown.isoformat(),
        "last_raided_at": raided.isoformat(),
        "relocation_pending": True,
    }
    # Deferred / invented columns must not leak
    assert "parent_holding_id" not in result
    assert "composition_profile" not in result
    assert "interior_sector_ids" not in result
    assert "assigned_npc_ids" not in result
    assert db.queried_model is OutlawBase


@pytest.mark.asyncio
async def test_missing_base_returns_404():
    db = _FakeSession(None)
    with pytest.raises(HTTPException) as exc:
        await get_admin_outlaw_base(
            base_id=uuid.uuid4(),
            _=SimpleNamespace(id=uuid.uuid4()),
            db=db,
        )
    assert exc.value.status_code == 404


def test_payload_helper_null_datetimes():
    payload = _admin_outlaw_base_payload(_base())
    assert payload["raid_cooldown_until"] is None
    assert payload["last_raided_at"] is None
    assert set(payload.keys()) == set(_ADMIN_OUTLAW_BASE_FIELDS)


def test_route_registered_and_gated_players_view():
    match = None
    for r in admin_mod.router.routes:
        if getattr(r, "path", None) == "/outlaw-bases/{base_id}":
            if "GET" in getattr(r, "methods", set()):
                match = r
                break
    assert match is not None

    sig = inspect.signature(get_admin_outlaw_base)
    admin_dep = sig.parameters["_"].default
    assert isinstance(admin_dep, Depends)
    closure = admin_dep.dependency.__closure__
    assert closure is not None
    closed = {cell.cell_contents for cell in closure}
    assert PLAYERS_VIEW in closed


def test_no_mutate_routes_for_outlaw_bases():
    mutate = []
    for r in admin_mod.router.routes:
        path = getattr(r, "path", "") or ""
        methods = getattr(r, "methods", set()) or set()
        if "outlaw-bases" in path:
            for m in methods:
                if m in {"POST", "PUT", "PATCH", "DELETE"}:
                    mutate.append((m, path))
    assert mutate == []


def test_route_source_stays_read_only():
    src = Path(admin_mod.__file__).read_text()
    assert "get_admin_outlaw_base" in src
    assert "/outlaw-bases/{base_id}" in src
    assert "composition_profile" not in src.split("get_admin_outlaw_base")[1].split(
        "@router"
    )[0]
