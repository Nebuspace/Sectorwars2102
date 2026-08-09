"""WO-BUILD-SHIP-PIN-VISIBILITY-AND-SET -- hatch-pin changing (ship-registry.md
"Hatch pin lock": "the current pilot -- owner OR borrower -- can change the
pin while aboard. A borrower who changes the pin locks the owner out of
their own ship; the owner's recourse is to file a stolen report.").

Deliberately does NOT special-case owner vs borrower -- both may change the
pin, with the borrower-lockout consequence being the canon-intended (if
adversarial) outcome, not a bug to guard against here.

DB-free: set_pin issues no queries of its own (no old-ship lookup, unlike
eject/board), so a bare stub with a no-op flush() is enough.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from src.services.ship_registry_service import ShipRegistryError, set_pin


class _FakeSession:
    def flush(self):
        pass


def make_player(**overrides):
    defaults = dict(id=uuid.uuid4())
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_ship(**overrides):
    defaults = dict(
        id=uuid.uuid4(),
        current_pilot_id=None,
        registered_owner_id=uuid.uuid4(),
        hatch_pin_code="ABC123",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_set_pin_not_current_pilot_rejects():
    player = make_player()
    ship = make_ship(current_pilot_id=uuid.uuid4())  # someone else aboard
    with pytest.raises(ShipRegistryError) as exc:
        set_pin(_FakeSession(), ship=ship, player=player, new_pin="NEWPIN1")
    assert exc.value.code == "ERR_NOT_CURRENT_PILOT"
    assert ship.hatch_pin_code == "ABC123"  # unchanged


def test_set_pin_nobody_aboard_rejects():
    player = make_player()
    ship = make_ship(current_pilot_id=None)
    with pytest.raises(ShipRegistryError) as exc:
        set_pin(_FakeSession(), ship=ship, player=player, new_pin="NEWPIN1")
    assert exc.value.code == "ERR_NOT_CURRENT_PILOT"


@pytest.mark.parametrize("bad_pin", ["", "AB", "ABC", "ABCDEFGHI", "AB-123", "AB 123"])
def test_set_pin_malformed_rejects(bad_pin):
    player = make_player()
    ship = make_ship(current_pilot_id=player.id)
    with pytest.raises(ShipRegistryError) as exc:
        set_pin(_FakeSession(), ship=ship, player=player, new_pin=bad_pin)
    assert exc.value.code == "ERR_INVALID_PIN"
    assert ship.hatch_pin_code == "ABC123"  # unchanged


def test_set_pin_owner_aboard_succeeds_and_normalizes_uppercase():
    player = make_player()
    ship = make_ship(current_pilot_id=player.id, registered_owner_id=player.id)
    result = set_pin(_FakeSession(), ship=ship, player=player, new_pin="newpin1")
    assert result == {"ship_id": str(ship.id), "hatch_pin_code": "NEWPIN1"}
    assert ship.hatch_pin_code == "NEWPIN1"


def test_set_pin_borrower_aboard_can_lock_out_the_owner():
    owner_id = uuid.uuid4()
    borrower = make_player()
    ship = make_ship(current_pilot_id=borrower.id, registered_owner_id=owner_id)
    result = set_pin(_FakeSession(), ship=ship, player=borrower, new_pin="LOCK99")
    assert result["hatch_pin_code"] == "LOCK99"
    assert ship.hatch_pin_code == "LOCK99"  # owner no longer knows this pin


@pytest.mark.parametrize("length", [4, 8])
def test_set_pin_accepts_boundary_lengths(length):
    player = make_player()
    ship = make_ship(current_pilot_id=player.id)
    pin = "A" * length
    result = set_pin(_FakeSession(), ship=ship, player=player, new_pin=pin)
    assert result["hatch_pin_code"] == pin
