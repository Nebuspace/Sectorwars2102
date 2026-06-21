"""Unit tests for the WO-CR2 escape-chance formula (combat-resolver.md).

DB-free: the formula is pure given (fleeing, pursuing, sector_edge_proximity). _sector_edge_proximity
(the only db-touching part) is exercised in the in-process dev proof, not here.
"""
import types
from unittest.mock import MagicMock

from src.services.combat_service import CombatService
from src.models.ship import ShipType


def _cs():
    return CombatService(MagicMock())


def _ship(ship_type, hull=None, max_hull=None):
    s = types.SimpleNamespace()
    s.type = ship_type
    s.combat = {}
    if hull is not None:
        s.combat["hull"] = hull
    if max_hull is not None:
        s.combat["max_hull"] = max_hull
    s.current_sector_id = None
    return s


def test_escape_base_minus_pursuer_class():
    # freighter fleeing (not fast, full hull), edge 0, pursuer freighter (factor 0.3)
    # 0.15 + 0 + 0 + 0 − 0.3*0.10 = 0.12 → 12
    cs = _cs()
    assert cs._calculate_escape_chance(
        _ship(ShipType.LIGHT_FREIGHTER, 100, 100), _ship(ShipType.LIGHT_FREIGHTER), 0.0) == 12


def test_escape_fast_bonus():
    # 0.15 + 0.20(fast) + 0 + 0 − 0(escape-pod pursuer 0.0) = 0.35 → 35
    cs = _cs()
    assert cs._calculate_escape_chance(
        _ship(ShipType.FAST_COURIER, 100, 100), _ship(ShipType.ESCAPE_POD), 0.0) == 35


def test_escape_hull_and_edge_terms():
    # fully-damaged freighter, max edge, interdictor pursuer:
    # 0.15 + 0 + (1-0)*0.30 + 1.0*0.10 − 1.0*0.10 = 0.45 → 45
    cs = _cs()
    assert cs._calculate_escape_chance(
        _ship(ShipType.LIGHT_FREIGHTER, 0, 100), _ship(ShipType.NPC_MARSHAL_INTERDICTOR), 1.0) == 45


def test_interdictor_suppresses_escape():
    cs = _cs()
    fleeing = _ship(ShipType.FAST_COURIER, 100, 100)
    vs_pod = cs._calculate_escape_chance(fleeing, _ship(ShipType.ESCAPE_POD), 0.0)            # 35
    vs_interdictor = cs._calculate_escape_chance(fleeing, _ship(ShipType.NPC_MARSHAL_INTERDICTOR), 0.0)  # 25
    assert vs_pod == 35 and vs_interdictor == 25
    assert vs_interdictor < vs_pod   # pursuer-class term suppresses escape


def test_escape_floor_clamp():
    # weak fleeing hull + interdictor + no bonuses → 0.15 − 0.10 = 0.05 → below floor → clamp 10
    cs = _cs()
    assert cs._calculate_escape_chance(
        _ship(ShipType.COLONY_SHIP, 100, 100), _ship(ShipType.NPC_MARSHAL_INTERDICTOR), 0.0) == 10


def test_pursuer_class_factor_default():
    cs = _cs()
    # unknown/None pursuer → default factor
    assert cs._pursuer_class_factor(None) == cs.DEFAULT_PURSUER_CLASS_FACTOR
    assert cs._pursuer_class_factor(_ship(ShipType.NPC_MARSHAL_INTERDICTOR)) == 1.0
