"""WO-GC-B §3.3 — the Galactic-Citizen P2W INCOME FENCE as a CI test.

The firewall (DECISIONS.md invariant): "PAID BUYS SHAPE, CONVENIENCE, AND
EXPRESSION; EARNED-AND-FREE BUYS POWER." This test makes that durable rather
than aspirational — it FAILS the moment any Citizen-gated surface (cosmetic,
module, hull slot) gains a combat/income axis. DB-free + pure-data, so it runs
in CI without a stack.

Covers, per 03-spec.md §3.3:
  - every L1 cosmetic is zero-stat (`effects == {}`);
  - no `requires:"citizen"` MODULE_DEFINITIONS entry carries an income effect-key,
    is a forbidden (combat/income) class, or exceeds its free same-class ceiling;
  - harvester / mining / combat classes are NEVER citizen-gated (the economy +
    combat doors stay shut);
  - no `requires:"citizen"` slot, and no citizen-acquisition hull's slots, in any
    seeded module_slots layout carry a forbidden class (forward-guard for GC-C).
"""
from src.services.ship_upgrade_service import (
    ShipUpgradeService,
    CITIZEN_COSMETICS,
    GC_INCOME_EFFECT_KEYS,
    GC_FORBIDDEN_CLASSES,
)
from src.core.ship_specifications_seeder import SHIP_SPECIFICATIONS, _build_module_slots

MODULE_DEFINITIONS = ShipUpgradeService.MODULE_DEFINITIONS


def _citizen_modules():
    return [
        (key, entry)
        for key, entry in MODULE_DEFINITIONS.items()
        if (entry.get("requires") == "citizen")
    ]


def test_l1_cosmetics_are_zero_stat():
    """Every cosmetic overlay is pure expression — effects:{} (no power/income)."""
    assert CITIZEN_COSMETICS, "cosmetic catalog must not be empty"
    for slot, entry in CITIZEN_COSMETICS.items():
        assert entry.get("effects") == {}, (
            f"cosmetic '{slot}' carries non-empty effects {entry.get('effects')} "
            f"— a cosmetic must be zero-stat (firewall)"
        )
        assert entry.get("requires") == "citizen", f"cosmetic '{slot}' must require citizen"
        assert entry.get("values"), f"cosmetic '{slot}' has no selectable values"


def test_no_citizen_module_carries_income_key_or_forbidden_class():
    """No Citizen-gated module is an income/combat class or leaks an income key."""
    for (cls, tier), entry in _citizen_modules():
        effects = entry.get("effects") or {}
        leaked = set(effects) & GC_INCOME_EFFECT_KEYS
        assert not leaked, (
            f"citizen module {cls}/Mk{tier} leaks income effect-key(s) {leaked} "
            f"— the income door must stay shut"
        )
        assert cls not in GC_FORBIDDEN_CLASSES, (
            f"citizen module class '{cls}' is a forbidden (combat/income) class"
        )


def test_citizen_module_never_exceeds_free_same_class_ceiling():
    """A Citizen module's per-effect magnitude never exceeds the free same-class
    ceiling (earned >= paid; the cap, not a paid edge)."""
    free_ceiling = {}  # (class, effect_key) -> max magnitude among free entries
    for (cls, tier), entry in MODULE_DEFINITIONS.items():
        if entry.get("requires") is None:
            for key, val in (entry.get("effects") or {}).items():
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    free_ceiling[(cls, key)] = max(free_ceiling.get((cls, key), val), val)
    for (cls, tier), entry in _citizen_modules():
        for key, val in (entry.get("effects") or {}).items():
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                continue
            # FIREWALL BACKSTOP (reviewer HIGH): a citizen module may ONLY carry a
            # numeric effect key that some FREE same-class module also carries — so
            # there is ALWAYS a free anchor to bound it. This closes the power-axis
            # hole: a novel citizen-only class, OR a brand-new power key with no free
            # counterpart (e.g. shield_bonus on a new class, or 'phase_cloak_bonus'),
            # would otherwise skip the ceiling check and slip an unbounded PAID power
            # axis past CI. Require the anchor FIRST, then bound by it.
            assert (cls, key) in free_ceiling, (
                f"citizen module {cls}/Mk{tier} carries effect '{key}' with NO free "
                f"same-class anchor — a novel paid power axis the firewall forbids "
                f"(combat+income ceiling must be no higher than a free player reaches)"
            )
            assert val <= free_ceiling[(cls, key)], (
                f"citizen module {cls}/Mk{tier} {key}={val} exceeds the free "
                f"same-class ceiling {free_ceiling[(cls, key)]}"
            )


def test_harvester_mining_combat_never_citizen_gated():
    """The economy + combat doors: these classes' `requires` must never be citizen."""
    for (cls, tier), entry in MODULE_DEFINITIONS.items():
        if cls in GC_FORBIDDEN_CLASSES:
            assert entry.get("requires") != "citizen", (
                f"{cls}/Mk{tier} is citizen-gated — economy/combat door breach"
            )


def test_no_citizen_slot_in_seeded_layouts_has_forbidden_class():
    """Forward-guard (GC-C): any requires:citizen slot, and any citizen-acquisition
    hull's slots, must be fenced away from combat/income classes."""
    for ship_type, spec in SHIP_SPECIFICATIONS.items():
        layout = _build_module_slots(ship_type, spec.get("ship_size")) or {}
        slots = layout.get("slots") or []
        is_citizen_hull = "citizen" in (spec.get("acquisition_methods") or [])
        for slot in slots:
            sclass = slot.get("class")
            if slot.get("requires") == "citizen" or is_citizen_hull:
                assert sclass not in GC_FORBIDDEN_CLASSES, (
                    f"{ship_type} slot {slot.get('i')} class '{sclass}' is a forbidden "
                    f"(combat/income) class on a citizen surface"
                )
