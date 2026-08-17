"""LEG-159 — voluntary planet ownership transfer + 5% fee (LEG-DEC-15 package)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services import planet_ownership_transfer_service as pots
from src.services.abandonment_service import sunk_cost_for


@pytest.fixture(autouse=True)
def _noop_flag_modified(monkeypatch):
    monkeypatch.setattr(pots, "flag_modified", lambda *a, **k: None)


class _ExecDB:
    """Minimal Session stand-in: records execute() calls; no real SQL."""

    def __init__(self):
        self.executed = []

    def execute(self, *args, **kwargs):
        self.executed.append((args, kwargs))
        return None


def _player(**kw):
    defaults = dict(
        id=uuid4(),
        credits=100_000,
        is_active=True,
        current_planet_id=None,
        is_landed=False,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _planet(owner_id, citadel_level=1, structures=None):
    return SimpleNamespace(
        id=uuid4(),
        owner_id=owner_id,
        citadel_level=citadel_level,
        structures=structures,
        name="TestWorld",
    )


def test_fee_is_five_percent_of_sunk_cost():
    # L1 ⇒ sunk = claim fee only (10_000); 5% = 500
    assert pots.transfer_fee_credits(1) == {"fee_base": 10_000, "fee_credits": 500}
    # L2 ⇒ 10_000 + 50_000 = 60_000; 5% = 3000
    assert pots.transfer_fee_credits(2)["fee_credits"] == int(round(0.05 * sunk_cost_for(2)))


def test_offer_and_accept_happy_path():
    owner = _player(credits=10_000)
    recipient = _player()
    planet = _planet(owner.id, citadel_level=1)
    db = _ExecDB()

    offered = pots.offer_transfer(db, planet, owner, recipient)
    assert offered["success"] is True
    assert pots.get_pending_transfer(planet)["to_player_id"] == str(recipient.id)
    assert offered["offer"]["fee_credits"] == 500

    result = pots.accept_transfer(db, planet, recipient, owner)
    assert result["success"] is True
    assert planet.owner_id == recipient.id
    assert owner.credits == 10_000 - 500
    assert pots.get_pending_transfer(planet) is None
    assert len(db.executed) >= 2  # delete + insert player_planets


def test_accept_insufficient_credits():
    owner = _player(credits=100)  # fee is 500 at L1
    recipient = _player()
    planet = _planet(owner.id, citadel_level=1)
    db = _ExecDB()
    pots.offer_transfer(db, planet, owner, recipient)
    with pytest.raises(ValueError, match="insufficient_credits"):
        pots.accept_transfer(db, planet, recipient, owner)
    assert planet.owner_id == owner.id
    assert owner.credits == 100


def test_non_owner_cannot_offer():
    owner = _player()
    stranger = _player()
    recipient = _player()
    planet = _planet(owner.id)
    with pytest.raises(ValueError, match="not_owner"):
        pots.offer_transfer(_ExecDB(), planet, stranger, recipient)


def test_concurrent_offer_rejected():
    owner = _player()
    r1 = _player()
    r2 = _player()
    planet = _planet(owner.id)
    db = _ExecDB()
    pots.offer_transfer(db, planet, owner, r1)
    with pytest.raises(ValueError, match="offer_pending"):
        pots.offer_transfer(db, planet, owner, r2)


def test_pending_blocks_abandon_assert():
    owner = _player()
    recipient = _player()
    planet = _planet(owner.id)
    pots.offer_transfer(_ExecDB(), planet, owner, recipient)
    with pytest.raises(ValueError, match="transfer_pending"):
        pots.assert_abandon_allowed(planet)


def test_cancel_clears_pending_no_fee():
    owner = _player(credits=50_000)
    recipient = _player()
    planet = _planet(owner.id)
    db = _ExecDB()
    pots.offer_transfer(db, planet, owner, recipient)
    pots.cancel_transfer(db, planet, owner)
    assert pots.get_pending_transfer(planet) is None
    assert owner.credits == 50_000


def test_expired_offer_rejected_on_accept():
    owner = _player(credits=50_000)
    recipient = _player()
    planet = _planet(owner.id)
    db = _ExecDB()
    pots.offer_transfer(db, planet, owner, recipient)
    pending = pots.get_pending_transfer(planet)
    pending["expires_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    pots._set_pending(planet, pending)
    with pytest.raises(ValueError, match="offer_expired"):
        pots.accept_transfer(db, planet, recipient, owner)
