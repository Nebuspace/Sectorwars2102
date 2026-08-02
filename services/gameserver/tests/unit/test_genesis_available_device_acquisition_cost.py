"""Genesis available-purchases device_acquisition_cost field (WO-API-PHASE1
Lane C / B8, Option A).

Proves GenesisService.get_available_purchases exposes a device_acquisition_cost
field DRY-sourced from GENESIS_DEVICE_PRICE -- the same constant player.py's
purchase_genesis_device route imports from this module to charge the
acquisition (POST /player/genesis/purchase) -- so the client can read the
flat one-time acquisition price instead of the hardcoded 25000 it used to
carry. This is a SEPARATE concept from tiers.*.cost (the deploy sequence
cost); see the two constants' docstrings in genesis_service.py.

Two layers of proof:
  1. Dynamic -- the returned field equals the shared constant.
  2. Static (source pin) -- player.py's purchase route imports the constant
     rather than redefining its own literal, which is the actual DRY
     invariant; a dynamic-only test can't tell "imported" from "coincidentally
     re-hardcoded to the same number" apart, and re-hardcoding is exactly the
     drift risk this WO exists to close (see the earlier tiers.basic.cost
     coupling STOP -- STEP-0 finding on this same WO).
"""
import inspect

from src.services.genesis_service import GENESIS_DEVICE_PRICE, GenesisService
from src.api.routes import player as player_route

from types import SimpleNamespace
from unittest.mock import MagicMock


def _make_player():
    """A player with no current ship -- keeps get_available_purchases to a
    single db.query(Player) round-trip, since the Ship lookups are only
    reached when current_ship_id is set (matches the reputation_gate test's
    fixture in the sibling file)."""
    return SimpleNamespace(
        id="22222222-2222-2222-2222-222222222222",
        credits=100000,
        current_ship_id=None,
        personal_reputation=1000,
        settings={},
    )


def _make_service(player):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = player
    return GenesisService(db)


def test_device_acquisition_cost_equals_shared_constant():
    player = _make_player()
    service = _make_service(player)

    result = service.get_available_purchases(player_id=player.id)

    assert result["device_acquisition_cost"] == GENESIS_DEVICE_PRICE
    assert result["device_acquisition_cost"] == 25000
    # Distinct from the deploy sequence cost -- both currently 25000 for the
    # basic tier, but they must not be the same field/source.
    assert result["tiers"]["basic"]["cost"] == 25000


def test_purchase_route_imports_price_not_redefines_it():
    """Source pin: purchase_genesis_device must pull GENESIS_DEVICE_PRICE from
    genesis_service.py's local import (matching MAX_PURCHASES_PER_WEEK /
    GENESIS_MIN_REPUTATION's existing pattern in the same function), never
    redefine its own module-level literal -- that redefinition is exactly
    what let the two constants drift apart (in concept, if not yet in value)
    before this WO."""
    source = inspect.getsource(player_route)

    assert "GENESIS_DEVICE_PRICE = 25000" not in source, (
        "player.py must not redefine GENESIS_DEVICE_PRICE as its own literal -- "
        "import it from genesis_service.py instead (single source of truth)."
    )
    assert "GENESIS_DEVICE_PRICE" in inspect.getsource(player_route.purchase_genesis_device)

    purchase_src = inspect.getsource(player_route.purchase_genesis_device)
    assert "from src.services.genesis_service import" in purchase_src
    assert "GENESIS_DEVICE_PRICE" in purchase_src.split("from src.services.genesis_service import", 1)[1].split(")", 1)[0]
