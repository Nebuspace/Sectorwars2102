"""Unit tests for WO-SHIP-CLIPPER-PARITY (Citizen Clipper exact-Fast-Courier parity).

Canon: sw2102-docs/FEATURES/gameplay/ship-roster.md:66-70 — the Clipper is an exact
FAST_COURIER mirror ("no edge in combat or income — a badge of citizenship, not a
power spike"); models/ship.py:21-25 states the P2W firewall this pin enforces.

DB-free, mirroring test_combat_escape.py: the decay/escape/pursuer/matchup lookups
are pure given a ShipType, so no session is exercised here.
"""
import types
from unittest.mock import MagicMock

from src.services.combat_service import CombatService
from src.services.maintenance_service import DECAY_PCT_PER_DAY, _decay_pct_per_day
from src.models.ship import ShipType

FC = ShipType.FAST_COURIER
CC = ShipType.CITIZEN_CLIPPER


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


def test_decay_parity():
    # Clipper untouched for 10 days loses 10.0 condition, identical to a Fast Courier.
    assert _decay_pct_per_day(_ship(CC)) == _decay_pct_per_day(_ship(FC)) == 1.0
    assert DECAY_PCT_PER_DAY[CC] == DECAY_PCT_PER_DAY[FC] == 1.0


def test_escape_chance_parity():
    cs = _cs()
    pursuer = _ship(ShipType.NPC_MARSHAL_INTERDICTOR)
    for hull, max_hull, edge in [(100, 100, 0.0), (40, 100, 0.5), (0, 100, 1.0)]:
        clipper_chance = cs._calculate_escape_chance(_ship(CC, hull, max_hull), pursuer, edge)
        fc_chance = cs._calculate_escape_chance(_ship(FC, hull, max_hull), pursuer, edge)
        assert clipper_chance == fc_chance
    # The +0.20 ESCAPE_FAST_BONUS applies to both (vs a non-fast baseline it wouldn't).
    baseline = cs._calculate_escape_chance(_ship(ShipType.LIGHT_FREIGHTER, 100, 100), pursuer, 0.0)
    fast = cs._calculate_escape_chance(_ship(CC, 100, 100), pursuer, 0.0)
    assert fast > baseline


def test_pursuer_class_factor_parity():
    cs = _cs()
    assert cs._pursuer_class_factor(_ship(CC)) == cs._pursuer_class_factor(_ship(FC)) == 0.4
    assert cs.PURSUER_CLASS_FACTOR[CC] == cs.PURSUER_CLASS_FACTOR[FC] == 0.4


def test_matchup_modifiers_parity():
    cs = _cs()
    mods = cs.SHIP_COMBAT_MODIFIERS
    assert mods[(CC, ShipType.CARRIER)] == mods[(FC, ShipType.CARRIER)] == 0.7
    assert mods[(ShipType.CARRIER, CC)] == mods[(ShipType.CARRIER, FC)] == 1.5


def test_fast_escape_membership_parity():
    cs = _cs()
    assert CC in cs.FAST_ESCAPE_SHIP_TYPES
    assert FC in cs.FAST_ESCAPE_SHIP_TYPES


def test_table_completeness_guard():
    """Every player-facing ShipType (is_npc_only False by convention — the NPC-only
    hulls are the NPC_-prefixed ones per models/ship.py:31-37 — ESCAPE_POD exempt,
    it does not decay) must appear in DECAY_PCT_PER_DAY, so the NEXT hull addition
    fails loudly instead of silently defaulting to permanent Pristine (0.0 decay)."""
    npc_only = {ShipType.NPC_MARSHAL_INTERDICTOR, ShipType.NPC_SENTINEL_INTERDICTOR}
    decay_exempt = {ShipType.ESCAPE_POD}
    for st in ShipType:
        if st in npc_only or st in decay_exempt:
            continue
        assert st in DECAY_PCT_PER_DAY, f"{st} missing from DECAY_PCT_PER_DAY"


def test_clipper_matches_fc_in_all_four_tables():
    cs = _cs()
    assert DECAY_PCT_PER_DAY[CC] == DECAY_PCT_PER_DAY[FC]
    assert (CC in cs.FAST_ESCAPE_SHIP_TYPES) == (FC in cs.FAST_ESCAPE_SHIP_TYPES)
    assert cs.PURSUER_CLASS_FACTOR[CC] == cs.PURSUER_CLASS_FACTOR[FC]
    assert cs.SHIP_COMBAT_MODIFIERS[(CC, ShipType.CARRIER)] == cs.SHIP_COMBAT_MODIFIERS[(FC, ShipType.CARRIER)]
    assert cs.SHIP_COMBAT_MODIFIERS[(ShipType.CARRIER, CC)] == cs.SHIP_COMBAT_MODIFIERS[(ShipType.CARRIER, FC)]
