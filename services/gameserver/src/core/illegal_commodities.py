"""Illicit-commodity catalog — the black-market kernel's standalone data module.

Single source of truth for the contraband commodities the black-market trading
loop buys and sells. Kept **deliberately separate** from ``models.resource``'s
``ResourceType`` so the legal commodity catalog and the supply/demand engine are
left entirely untouched (the brief §1.2 invariant).

This module is a *pure catalog*: it imports nothing from ``services`` or
``api.routes`` (and nothing from ``models`` either) so it can be consumed by the
service, the route, a migration, or a test without dragging in the ORM/session
graph. Per-faction reputation deltas are keyed on the **string value** of the
faction enum (``FactionType.OUTLAWS.value == "Outlaws"``) rather than on the enum
class itself, so this catalog stays free of model imports; the caller
(``contraband_service``) resolves the string back to ``FactionType`` and routes
it through the sync ``apply_faction_rep_delta`` helper, which already logs-and-
returns on any faction it does not recognise.

Design brief: ``audit/design-briefs/black-market.md`` §2 (the metadata table).

NO-CANON flags (file in ``sw2102-docs/DECISIONS.md`` Pending — built against the
listed defaults so nothing blocks):
* **[OPEN-2]** ``SLAVES`` inclusion — present here as a value but **disabled by a
  feature flag that has no enable path** (see ``SLAVES_ENABLED`` below). Theme
  call for Max: keep / rename (e.g. ``BONDSERVANTS``) / cut.
* **[OPEN-4]** ``base_price`` for the no-legal-market items. The values below are
  invented defaults grounded in current code where a reference exists:
  - ``WEAPONS`` 2000, ``CONTRABAND_SUBSTANCES`` 1500, ``SLAVES`` 2500 — fixed
    "Federation seizure-value" placeholders (FEATURES leaves these abstract).
  - ``STOLEN_GOODS`` 100 — a legal-commodity mid reference (matches
    ``commodity_economy.COMMODITY_BASE_PRICES["luxury_goods"]["base"] == 100``).
  - ``RESTRICTED_TECH`` 250 — the ``exotic_technology`` reference
    (``commodity_economy.COMMODITY_BASE_PRICES["exotic_technology"]["base"]``).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Dict, List, Mapping


# ── Severity tiers (drive fine multiplier + suspect/wanted flip in the service) ─
class IllegalSeverity(str, enum.Enum):
    """How harshly the law treats a given contraband category.

    The service maps these to a fine multiplier (brief [OPEN-6] default:
    LIGHT 2× / MODERATE 3× / SEVERE 4× cargo value) and to the heat outcome
    (brief [OPEN-7]: LIGHT/MODERATE -> ``Player.is_suspect``; SEVERE ->
    ``Player.is_wanted``). This module only *declares* the tier per commodity;
    it does not apply the consequence (single-writer: the service owns the txn).
    """

    LIGHT = "LIGHT"
    MODERATE = "MODERATE"
    SEVERE = "SEVERE"


# ── The illicit-commodity enum (separate from ResourceType — never merge) ──────
class IllegalCommodity(str, enum.Enum):
    """Contraband commodities tradeable on the black market.

    A str-enum so the value serialises cleanly to JSON (the API payloads and the
    ``Ship.cargo`` ``"illegal:<commodity>"`` key) and round-trips through the
    nullable ``MarketTransaction.illegal_commodity`` String column.
    """

    WEAPONS = "WEAPONS"
    STOLEN_GOODS = "STOLEN_GOODS"
    RESTRICTED_TECH = "RESTRICTED_TECH"
    CONTRABAND_SUBSTANCES = "CONTRABAND_SUBSTANCES"
    # [OPEN-2] disabled stub — present but never enabled (see SLAVES_ENABLED).
    SLAVES = "SLAVES"


# ── The SLAVES disable flag — a constant with NO ENABLE PATH ───────────────────
# This is intentionally a module-level constant, hard-set to False, that NOTHING
# in the codebase ever assigns True. There is no parameter, no env lookup, no
# admin toggle that activates SLAVES: ``ENABLED_COMMODITIES`` unconditionally
# excludes it, and the catalog / buy / sell paths must reject it on its own
# merits. To ever enable it, a human must edit this line in source review —
# which is the explicit gate Max asked for ([OPEN-2] is a *theme* decision, not a
# runtime flag flip). Do not add an enable path; the reviewer asserts its absence.
# SLAVES: intentionally disabled + omitted from public canon by design (2026-06-22).
SLAVES_ENABLED: bool = False

# The set of commodities that are permanently disabled regardless of any flag.
# SLAVES lives here so the exclusion is data-driven and a reviewer can grep one
# place to confirm there is no activation route.
_PERMANENTLY_DISABLED: frozenset[IllegalCommodity] = frozenset({IllegalCommodity.SLAVES})


@dataclass(frozen=True)
class IllegalCommodityMeta:
    """Static metadata for one contraband commodity (brief §2 table).

    Attributes:
        base_price: Pre-multiplier credits/unit reference ([OPEN-4] defaults).
        category_multiplier: The contraband markup over ``base_price``.
        severity: Legal-severity tier driving fine + heat outcome.
        federation_rep_delta: Reputation change with the Federation per trade
            (always negative — trading contraband always angers the law).
        other_faction_rep_deltas: Per-faction rep deltas keyed on the *string
            value* of ``FactionType`` (e.g. ``"Pirates"``). Only existing
            faction members are listed; factions not in the current roster
            (Nova Scientific, Frontier Coalition — brief §2 footnote) are simply
            absent, and the caller's ``apply_faction_rep_delta`` no-ops anything
            it cannot resolve.
    """

    base_price: int
    category_multiplier: float
    severity: IllegalSeverity
    federation_rep_delta: int
    other_faction_rep_deltas: Mapping[str, int] = field(default_factory=dict)


# ── The catalog — brief §2 metadata table ─────────────────────────────────────
# Other-faction deltas key ONLY on FactionType members that exist today
# (Federation, Independents, Pirates, Merchants, Explorers, Military, Mining,
# Outlaws, Syndicate, Concord). The brief's Nova Scientific / Frontier Coalition
# entries are not in the roster yet, so they are omitted (silent no-op).
ILLEGAL_COMMODITY_CATALOG: Dict[IllegalCommodity, IllegalCommodityMeta] = {
    IllegalCommodity.WEAPONS: IllegalCommodityMeta(
        base_price=2000,  # [OPEN-4] seizure-value default
        category_multiplier=2.00,
        severity=IllegalSeverity.SEVERE,
        federation_rep_delta=-150,
        other_faction_rep_deltas={
            "Merchants": -10,
            "Pirates": +15,
        },
    ),
    IllegalCommodity.STOLEN_GOODS: IllegalCommodityMeta(
        base_price=100,  # [OPEN-4] legal-commodity mid ref (luxury_goods base)
        category_multiplier=2.00,
        severity=IllegalSeverity.LIGHT,
        federation_rep_delta=-50,
        other_faction_rep_deltas={
            "Merchants": -25,
        },
    ),
    IllegalCommodity.RESTRICTED_TECH: IllegalCommodityMeta(
        base_price=250,  # [OPEN-4] exotic_technology ref (COMMODITY_BASE_PRICES)
        category_multiplier=2.50,
        severity=IllegalSeverity.MODERATE,
        federation_rep_delta=-100,
        other_faction_rep_deltas={
            # Nova Scientific (-75) not in roster -> omitted (silent no-op).
            "Mining": -10,
        },
    ),
    IllegalCommodity.CONTRABAND_SUBSTANCES: IllegalCommodityMeta(
        base_price=1500,  # [OPEN-4] seizure-value default
        category_multiplier=1.80,
        severity=IllegalSeverity.MODERATE,
        federation_rep_delta=-100,
        other_faction_rep_deltas={
            # Nova Scientific (-50) not in roster -> omitted (silent no-op).
        },
    ),
    IllegalCommodity.SLAVES: IllegalCommodityMeta(
        base_price=2500,  # [OPEN-4] seizure-value default
        category_multiplier=2.50,
        severity=IllegalSeverity.SEVERE,
        federation_rep_delta=-300,
        other_faction_rep_deltas={
            "Mining": -150,
            # Frontier Coalition (-50) not in roster -> omitted (silent no-op).
        },
    ),
}


def is_enabled(commodity: IllegalCommodity) -> bool:
    """True iff this commodity may be traded.

    SLAVES is permanently disabled (``_PERMANENTLY_DISABLED``) with no enable
    path: this returns False for SLAVES regardless of ``SLAVES_ENABLED``, which
    is itself hard-False and never assigned True anywhere. There is deliberately
    no argument or state that can flip SLAVES on.
    """
    if commodity is IllegalCommodity.SLAVES:
        # SLAVES is gated twice over: it is in _PERMANENTLY_DISABLED AND the
        # SLAVES_ENABLED constant is hard-False with no assignment that sets it
        # True. Even reading the flag here cannot enable it — the permanent set
        # check below would still exclude it.
        return SLAVES_ENABLED and (commodity not in _PERMANENTLY_DISABLED)
    return commodity not in _PERMANENTLY_DISABLED


def enabled_commodities() -> List[IllegalCommodity]:
    """The tradeable contraband list — ALWAYS excludes SLAVES.

    This is the single accessor the catalog, buy, and sell paths must use to
    decide what the venue offers. It can never include SLAVES regardless of any
    flag value, because SLAVES is in ``_PERMANENTLY_DISABLED``.
    """
    return [c for c in IllegalCommodity if c not in _PERMANENTLY_DISABLED]


# Pre-computed convenience tuple of the enabled commodities (excludes SLAVES).
# Provided so callers can iterate without recomputing; it is built from
# ``enabled_commodities()`` so the SLAVES exclusion is enforced in exactly one
# place.
ENABLED_COMMODITIES: tuple[IllegalCommodity, ...] = tuple(enabled_commodities())


def get_meta(commodity: IllegalCommodity) -> IllegalCommodityMeta:
    """Metadata for an enabled commodity.

    Raises ``KeyError`` for an unknown commodity and ``ValueError`` for a
    permanently-disabled one (SLAVES) — the buy/sell paths rely on this to
    reject SLAVES unconditionally rather than ever pricing or moving it.
    """
    if commodity in _PERMANENTLY_DISABLED:
        raise ValueError(f"{commodity.value} is permanently disabled and cannot be traded")
    return ILLEGAL_COMMODITY_CATALOG[commodity]


def cargo_key(commodity: IllegalCommodity) -> str:
    """The ``Ship.cargo`` JSONB contents key for held contraband.

    Contraband is stored under ``"illegal:<commodity>"`` so it counts against
    cargo capacity but stays distinguishable from legal goods for the detection
    roll (brief §1.3).
    """
    return f"illegal:{commodity.value}"
