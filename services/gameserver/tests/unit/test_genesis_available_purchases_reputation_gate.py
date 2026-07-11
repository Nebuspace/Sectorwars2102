"""Genesis available-purchases reputation_gate field.

Proves GenesisService.get_available_purchases exposes a reputation_gate
field (required/current/met) sourced from GENESIS_MIN_REPUTATION -- the
same constant player.py's purchase_genesis_device gate imports from this
module -- so the client can render the rep requirement pre-click instead
of only surfacing it on the 400 the acquisition gate raises.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.services.genesis_service import GENESIS_MIN_REPUTATION, GenesisService


def _make_player(personal_reputation):
    """A player with no current ship -- keeps get_available_purchases to a
    single db.query(Player) round-trip, since the Ship lookups are only
    reached when current_ship_id is set."""
    return SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        credits=100000,
        current_ship_id=None,
        personal_reputation=personal_reputation,
        settings={},
    )


def _make_service(player):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = player
    return GenesisService(db)


def test_reputation_gate_not_met_below_threshold():
    player = _make_player(personal_reputation=100)
    service = _make_service(player)

    result = service.get_available_purchases(player_id=player.id)

    gate = result["reputation_gate"]
    assert gate["required"] == GENESIS_MIN_REPUTATION
    assert gate["current"] == 100
    assert gate["met"] is False


def test_reputation_gate_met_at_threshold():
    player = _make_player(personal_reputation=GENESIS_MIN_REPUTATION)
    service = _make_service(player)

    result = service.get_available_purchases(player_id=player.id)

    gate = result["reputation_gate"]
    assert gate["required"] == GENESIS_MIN_REPUTATION
    assert gate["current"] == GENESIS_MIN_REPUTATION
    assert gate["met"] is True


def test_reputation_gate_defaults_none_reputation_to_zero():
    player = _make_player(personal_reputation=None)
    service = _make_service(player)

    result = service.get_available_purchases(player_id=player.id)

    gate = result["reputation_gate"]
    assert gate["current"] == 0
    assert gate["met"] is False
