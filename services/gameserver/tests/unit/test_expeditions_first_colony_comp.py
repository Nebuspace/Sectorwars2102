"""WO-WIRE-FIRST-COLONY-EXPEDITION-COMP -- ADR-0091 M38.

DB-free: the route's own DB-touching helpers (`_get_owned_planet` /
`_get_owned_ship`) and `first_login_service.get_first_colony_expedition_overrides`
are monkeypatched -- this pins the WIRING defect the WO fixes (the route
must merge whatever the overrides helper returns into `roll_expedition`'s
kwargs), not the helper's own first-colony/starter-planet decision logic,
which is that function's own concern.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from src.api.routes import expeditions
from src.services import first_login_service


class _FakeSession:
    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


def _request(planet_id: uuid.UUID) -> expeditions.LaunchExpeditionRequest:
    return expeditions.LaunchExpeditionRequest(planet_id=planet_id)


@pytest.mark.asyncio
async def test_launch_expedition_merges_first_colony_comp_overrides(monkeypatch):
    """M38: on a first-colony player's starter planet, the comp overrides
    (forced_success/guaranteed_good/waive_cost) must reach roll_expedition."""
    player = SimpleNamespace(id=uuid.uuid4())
    planet = SimpleNamespace(id=uuid.uuid4())
    db = _FakeSession()
    captured = {}

    monkeypatch.setattr(expeditions, "_get_owned_planet", lambda db, pid: planet)
    monkeypatch.setattr(
        first_login_service,
        "get_first_colony_expedition_overrides",
        lambda db, p, pl: {"forced_success": True, "guaranteed_good": True, "waive_cost": True},
    )

    def _fake_roll(db, player, planet, ship, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            id=uuid.uuid4(), planet_id=planet.id, ship_id=None,
            status=SimpleNamespace(value="SUCCESS"), result=None, demo=False,
            launched_at=None,
        )

    monkeypatch.setattr(expeditions.expedition_service, "roll_expedition", _fake_roll)

    await expeditions.launch_expedition(_request(planet.id), player=player, db=db)

    assert captured == {"forced_success": True, "guaranteed_good": True, "waive_cost": True}


@pytest.mark.asyncio
async def test_launch_expedition_passes_no_overrides_outside_first_colony_comp(monkeypatch):
    """Every other planet / after colony #1: the helper returns {}, so
    roll_expedition must be called with its normal (all-default) kwargs --
    the merge must be a true no-op, never injecting a stray truthy flag."""
    player = SimpleNamespace(id=uuid.uuid4())
    planet = SimpleNamespace(id=uuid.uuid4())
    db = _FakeSession()
    captured = {}

    monkeypatch.setattr(expeditions, "_get_owned_planet", lambda db, pid: planet)
    monkeypatch.setattr(
        first_login_service, "get_first_colony_expedition_overrides", lambda db, p, pl: {},
    )

    def _fake_roll(db, player, planet, ship, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            id=uuid.uuid4(), planet_id=planet.id, ship_id=None,
            status=SimpleNamespace(value="SUCCESS"), result=None, demo=False,
            launched_at=None,
        )

    monkeypatch.setattr(expeditions.expedition_service, "roll_expedition", _fake_roll)

    await expeditions.launch_expedition(_request(planet.id), player=player, db=db)

    assert captured == {}
