"""Single source of truth for commodity base economics (WO-Y / ADR-0082).

Background
----------
Commodity base economics historically lived in TWO independent, disagreeing
places:

* ``trading_service.COMMODITY_PRICE_RANGES`` — the spec-defined min/max clamp
  ranges the dynamic-pricing engine bounds every reprice to (e.g. equipment
  50-120).
* ``citadel_service.COMMODITY_CREDIT_VALUE`` — the flat per-unit "base trade
  price" used to value commodities deposited into the citadel safe against its
  cr-equivalent capacity (e.g. equipment 35). ADR-0082 named these "the
  economy's existing base trade prices (the same constants the market/nexus
  economy already uses)" — and they are: equipment 35 / organics 18 / ore 15
  exactly match the per-station ``base_price`` seeds in the station model,
  ``bang_import_service._COMMODITY_DEFAULTS`` and
  ``nexus_generation_service.base_commodities``.

Max ruled (WO-Y) the two systems should be ONE source. This module is that
source. **It is behavior-preserving**: it reproduces today's live trading
ranges AND today's live citadel credit values EXACTLY. It does NOT move
balance — the unification of the *numbers themselves* into a single derived
value is a separate balance call (see ``PROPOSED CANON`` below) for Max/the
orchestrator to bless before anything observable changes.

The single table
----------------
``COMMODITY_BASE_PRICES[commodity] = base`` is the canonical per-commodity base
price in credits/unit. ``base`` is the per-station seed ``base_price`` — the
genuine "economy base trade price" ADR-0082 references, and the value the
citadel safe already uses for the three storable commodities.

Both downstream systems derive from it WITHOUT changing observable values:

1. **Citadel credit value** = ``base * CREDIT_VALUE_FACTOR`` with the factor
   chosen to reproduce today's value EXACTLY. Today credit_value == base for
   all three storable commodities (ore/fuel_ore 15, organics 18, equipment 35),
   so ``CREDIT_VALUE_FACTOR = 1.0``. Documented as a factor (not hardcoded) so
   the coupling ADR-0082 describes ("if base trade prices are rebalanced the
   safe's effective capacity shifts with them") is now mechanical and explicit.

2. **Trading clamp range** = today an INDEPENDENT balance artifact with NO clean
   single-factor relationship to ``base`` (e.g. equipment base 35 but range
   50-120 does not even contain 35; organics base 18 sits inside its 8-25 range
   but is not the midpoint). To be honest AND behavior-preserving we store the
   exact current ranges here, co-located with ``base`` as a per-commodity
   ``(min, max)``. The range is therefore explicitly recorded (not derived) for
   now; making range a clean function of ``base`` is part of the PROPOSED CANON
   balance call, not this refactor.

Vocabulary unification (ore vs fuel_ore)
----------------------------------------
The market/trading/station/bang/nexus economy universally names this commodity
``"ore"`` (station JSONB key, ``MarketPrice.commodity``,
``bang_import.COMMODITY_WIRE_ORDER``, station-class trade patterns, the unit
tests). The PLANET resource domain names the same substance ``fuel_ore`` — it
is a ``planet.fuel_ore`` Column, the citadel safe keys on it (it reads
``getattr(planet, commodity)``), and the citadel API contract validates
``^(fuel_ore|organics|equipment)$``. ``bang_import`` already bridges them
(``fuel_ore=int(p.get("ore", 0))``).

Canonical key here is **``ore``** (the commodity-economy term — the majority,
the wire format, the test vocabulary, the docs commodity list). The
``fuel_ore`` planet-Column / citadel-API term is preserved at the domain
boundary via :data:`COMMODITY_ALIASES` so the existing UI/API contract and the
``planet.fuel_ore`` Column are untouched, while the *price table* speaks one
canonical vocabulary. :func:`canonical_commodity` resolves either spelling.

PROPOSED CANON (NOT APPLIED — balance call for Max/orchestrator)
----------------------------------------------------------------
See ``commodity_economy``'s module-level note and the WO-Y report's
``proposed_canon`` field. In brief: make the trading clamp range a documented
function of ``base`` (e.g. ``min = round(base * RANGE_MIN_FACTOR)``,
``max = round(base * RANGE_MAX_FACTOR)``) so a single ``base`` per commodity
fully determines BOTH systems. This WOULD change live trading ranges, so it is
deliberately left as a proposal, not implemented here.
"""

from __future__ import annotations

from typing import Dict, Tuple

# ---------------------------------------------------------------------------
# THE single source of truth.
# ---------------------------------------------------------------------------
# Per-commodity canonical economics. ``base`` is the credits/unit base trade
# price (== the per-station seed base_price == ADR-0082's "economy base trade
# price"). ``range`` is the spec-defined dynamic-pricing clamp (min, max),
# recorded explicitly because today it is an independent balance artifact (no
# clean single factor off ``base`` — that unification is the PROPOSED CANON).
#
# Behaviour-preservation invariants (asserted at import, below):
#   * range here == today's trading_service.COMMODITY_PRICE_RANGES exactly.
#   * base * CREDIT_VALUE_FACTOR == today's citadel COMMODITY_CREDIT_VALUE
#     exactly for every storable commodity.
COMMODITY_BASE_PRICES: Dict[str, Dict[str, object]] = {
    "ore":               {"base": 15,  "range": (15, 45)},
    "organics":          {"base": 18,  "range": (8, 25)},
    "equipment":         {"base": 35,  "range": (50, 120)},
    "fuel":              {"base": 12,  "range": (20, 60)},
    "gourmet_food":      {"base": 80,  "range": (30, 70)},
    "luxury_goods":      {"base": 100, "range": (75, 200)},
    "exotic_technology":  {"base": 250, "range": (150, 300)},
    "colonists":         {"base": 50,  "range": (30, 80)},
    "precious_metals":   {"base": 130, "range": (80, 180)},
}

# Factor that turns ``base`` into a citadel-safe credit value. Chosen to
# REPRODUCE today's COMMODITY_CREDIT_VALUE exactly: credit_value == base for
# ore/organics/equipment today, so the factor is 1.0. Kept as a named factor
# (not folded away) so the base<->safe coupling ADR-0082 describes is explicit
# and a future rebalance flows through one place.
CREDIT_VALUE_FACTOR: float = 1.0

# Commodities that are storable in the citadel safe today (ADR-0082). Keyed on
# the canonical ``ore`` vocabulary; the citadel layer maps its ``fuel_ore``
# wire/Column term through COMMODITY_ALIASES.
SAFE_STORABLE_COMMODITIES: Tuple[str, ...] = ("ore", "organics", "equipment")

# Domain-boundary vocabulary aliases: alternate spelling -> canonical key.
# The planet-resource / citadel domain calls ore "fuel_ore" (a planet Column,
# the citadel API pattern, the UI contract). Resolve it to the canonical
# ``ore`` so the single price table speaks one vocabulary while the external
# fuel_ore contract and the planet.fuel_ore Column stay untouched.
COMMODITY_ALIASES: Dict[str, str] = {
    "fuel_ore": "ore",
}


def canonical_commodity(name: str) -> str:
    """Resolve a commodity name (either vocabulary) to its canonical key.

    ``fuel_ore`` (planet/citadel domain) -> ``ore`` (commodity-economy domain).
    Any name already canonical (or unknown) is returned unchanged.
    """
    return COMMODITY_ALIASES.get(name, name)


def base_price(name: str) -> int:
    """Canonical base trade price (credits/unit) for a commodity.

    Accepts either vocabulary (``ore`` or ``fuel_ore``). Returns 0 for unknown
    commodities, matching the historical ``COMMODITY_CREDIT_VALUE.get(.., 0)``
    behaviour relied on by the citadel safe total.
    """
    entry = COMMODITY_BASE_PRICES.get(canonical_commodity(name))
    return int(entry["base"]) if entry else 0


def credit_value(name: str) -> int:
    """Citadel-safe credit-equivalent value per unit (== base * factor).

    Behaviour-preserving: with CREDIT_VALUE_FACTOR == 1.0 this equals
    :func:`base_price` exactly, reproducing today's COMMODITY_CREDIT_VALUE.
    """
    return int(round(base_price(name) * CREDIT_VALUE_FACTOR))


def price_range(name: str) -> Dict[str, int]:
    """Spec dynamic-pricing clamp ``{"min": .., "max": ..}`` for a commodity.

    Accepts either vocabulary. Returns ``{}`` for an unknown commodity (the
    trading engine treats a missing range as "no clamp", matching today).
    """
    entry = COMMODITY_BASE_PRICES.get(canonical_commodity(name))
    if not entry:
        return {}
    lo, hi = entry["range"]  # type: ignore[misc]
    return {"min": int(lo), "max": int(hi)}


def get_commodity_price_ranges() -> Dict[str, Dict[str, int]]:
    """All spec price ranges keyed by canonical commodity (legacy shape).

    Drop-in replacement for the old
    ``trading_service.COMMODITY_PRICE_RANGES``: a ``{commodity: {"min", "max"}}``
    dict, in the same declaration order, with the same values.
    """
    return {name: price_range(name) for name in COMMODITY_BASE_PRICES}


def get_commodity_credit_values() -> Dict[str, int]:
    """Storable-commodity credit values keyed in the CITADEL vocabulary.

    Drop-in replacement for ``citadel_service.COMMODITY_CREDIT_VALUE``: keys are
    the citadel/planet ``fuel_ore`` spelling (reverse of COMMODITY_ALIASES)
    where one exists, preserving the exact external contract the citadel API's
    ``commodity_values`` response and request validation depend on.
    """
    reverse = {canon: alias for alias, canon in COMMODITY_ALIASES.items()}
    out: Dict[str, int] = {}
    for canon in SAFE_STORABLE_COMMODITIES:
        wire = reverse.get(canon, canon)
        out[wire] = credit_value(canon)
    return out


# ---------------------------------------------------------------------------
# Behaviour-preservation guard rails. These reproduce the pre-refactor live
# values; if anyone edits a base/range/factor in a way that would silently move
# balance, import fails loudly rather than shipping a hidden balance change.
# ---------------------------------------------------------------------------
_LEGACY_TRADING_RANGES = {
    "ore":               {"min": 15,  "max": 45},
    "organics":          {"min": 8,   "max": 25},
    "gourmet_food":      {"min": 30,  "max": 70},
    "fuel":              {"min": 20,  "max": 60},
    "equipment":         {"min": 50,  "max": 120},
    "precious_metals":   {"min": 80,  "max": 180},
    "exotic_technology":  {"min": 150, "max": 300},
    "luxury_goods":      {"min": 75,  "max": 200},
    "colonists":         {"min": 30,  "max": 80},
}
_LEGACY_CREDIT_VALUES = {"fuel_ore": 15, "organics": 18, "equipment": 35}

for _c, _r in _LEGACY_TRADING_RANGES.items():
    assert price_range(_c) == _r, (
        f"commodity_economy: trading range for {_c} changed "
        f"({price_range(_c)} != {_r}); this is a SILENT BALANCE MOVE — forbidden"
    )
for _c, _v in _LEGACY_CREDIT_VALUES.items():
    assert credit_value(_c) == _v, (
        f"commodity_economy: credit value for {_c} changed "
        f"({credit_value(_c)} != {_v}); this is a SILENT BALANCE MOVE — forbidden"
    )
del _c, _r, _v
