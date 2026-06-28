"""
Canonical medal catalog (ADR-0028).

This module is the single source of truth for the *definitions* of every medal
in the game. Per ADR-0028 the catalog lives in code here and is seeded
idempotently into the relational ``medals`` table (see :func:`seed_medals`);
per-player awards live in the ``player_medals`` association table.

Schema mapping — each entry maps onto :class:`src.models.medal.Medal`:

    id          -> Medal.id          (stable string PK, namespaced, e.g. "combat.bronze_star")
    name        -> Medal.name
    description -> Medal.description
    category    -> Medal.category    (Combat / Economic / Exploration / Diplomatic / Special)
    tier        -> Medal.tier        (bronze / silver / gold / unique / platinum ...)
    criteria    -> Medal.criteria    (JSONB; the structured award-trigger condition)
    effect      -> Medal.effect      (JSONB nullable; bespoke gameplay effect — WO-CG)

The ``criteria`` JSONB preserves the original trigger shape plus the legacy
``icon`` key so the existing JSONB-era readers keep functioning during the
relational migration:

    {"type": <trigger_type>, "threshold": <int>, "icon": <legacy_icon_key>}

These ids are STABLE. Renaming an id orphans every existing award row, so the
legacy short keys (e.g. ``bronze_star``) are preserved in ``criteria.legacy_key``
to keep the JSONB-era data reconcilable by the backfill CLI.

WO-CG — bespoke per-medal gameplay effects (DECISIONS.md:479 medal-effects-model;
blessed spec audit/design-briefs/medal-effects-spec.md FINAL section). Each entry
carries an ``effect`` dict (or ``None`` = cosmetic-only). Two effect classes:

  * ``"kind": "passive"`` — read every time a resolver runs while the player holds
    the medal. ``hook`` names the real bonus stack it folds into
    (combat_damage / trading_discount / turn_regen / haggle_band), summed per-hook
    in :func:`medal_service.get_active_medal_bonuses` and clamped to the BLESSED
    HARD CAPS (combat_damage ≤ +3% · trading_discount ≤ −2% · turn_regen ≤ +0.05 ·
    haggle_band ≤ +0.08). ``magnitude`` is in the hook's native unit (percent for
    combat/trading, additive multiplier delta for turn_regen, band-factor delta
    for haggle_band — see the per-hook notes in medal_service).
  * ``"kind": "one_time"`` — a single grant fired ONCE on the medal-award INSERT
    (idempotent — re-award is a no-op). ``grants`` carries ``{credits, turns}``.

Magnitudes/grants are NO-CANON, conservative, tunable per the blessed FINAL table.
"""

from typing import Dict, Any, List, Optional

# ---------------------------------------------------------------------------
# Tier vocabulary (free-form String in the model; we keep it consistent here).
# ---------------------------------------------------------------------------
TIER_BRONZE = "bronze"
TIER_SILVER = "silver"
TIER_GOLD = "gold"
TIER_UNIQUE = "unique"
TIER_PLATINUM = "platinum"  # docs' proposed 4th tier (medals.md); adopted for Worldsmith.

# Category vocabulary (matches the legacy MEDAL_DEFINITIONS categories).
CAT_COMBAT = "Combat"
CAT_ECONOMIC = "Economic"
CAT_EXPLORATION = "Exploration"
CAT_DIPLOMATIC = "Diplomatic"
CAT_SPECIAL = "Special"

# ---------------------------------------------------------------------------
# Effect hooks — must match the per-hook keys read by medal_service /
# get_active_medal_bonuses and the four resolver insertion points.
# ---------------------------------------------------------------------------
HOOK_COMBAT_DAMAGE = "combat_damage"      # combat_service attacker_damage_mult (percent)
HOOK_TRADING_DISCOUNT = "trading_discount"  # trading.py rank_rate term (percent; sign per FINAL)
HOOK_TURN_REGEN = "turn_regen"            # turn_service aria_multiplier term (additive delta)
HOOK_HAGGLE_BAND = "haggle_band"          # haggle_service band multiplier (band-factor delta)

# Orange Cat is a SPECIAL-CASE perk: published +15%, EXEMPT from the haggle_band
# cap, and applied through haggle_service's existing dedicated lever — NOT summed
# through the capped get_active_medal_bonuses haggle path. Marking the catalog
# effect kind "special" keeps the generic bonus folder from double-applying it.
EFFECT_KIND_SPECIAL = "special"


def _effect(
    kind: str,
    *,
    hook: Optional[str] = None,
    scope: str = "global",
    magnitude: float = 0.0,
    credits: int = 0,
    turns: int = 0,
    notes: str = "",
) -> Dict[str, Any]:
    """Construct an ``effect`` dict for a catalog entry.

    ``kind`` ∈ {"passive", "one_time", "special"}. Passive effects carry a
    ``hook`` + ``magnitude``; one_time effects carry ``grants`` ({credits, turns});
    "special" marks a perk applied through a dedicated lever (Orange Cat) and
    excluded from the generic capped bonus fold.
    """
    eff: Dict[str, Any] = {"kind": kind, "scope": scope, "notes": notes}
    if kind == "one_time":
        eff["grants"] = {"credits": int(credits), "turns": int(turns)}
    else:  # passive / special
        eff["hook"] = hook
        eff["magnitude"] = float(magnitude)
    return eff


def _medal(
    medal_id: str,
    name: str,
    category: str,
    tier: str,
    description: str,
    trigger_type: str,
    threshold: int,
    icon: str,
    legacy_key: str,
    effect: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Construct a catalog entry whose shape mirrors the ``medals`` columns."""
    return {
        "id": medal_id,
        "name": name,
        "category": category,
        "tier": tier,
        "description": description,
        "criteria": {
            "type": trigger_type,
            "threshold": threshold,
            "icon": icon,
            "legacy_key": legacy_key,
        },
        "effect": effect,
    }


# ---------------------------------------------------------------------------
# THE CATALOG — keyed by the stable string medal_id.
# 13 ported from the legacy MEDAL_DEFINITIONS + 21 expansion medals (WO-CG).
# Effects per the BLESSED FINAL spec (medal-effects-spec.md). Magnitudes/grants
# are NO-CANON, conservative, tunable; the per-hook caps in medal_service are the
# hard ceiling on summed passive contributions.
# ---------------------------------------------------------------------------
MEDAL_CATALOG: Dict[str, Dict[str, Any]] = {
    # ── Combat ──────────────────────────────────────────────────────
    "combat.first_blood": _medal(
        "combat.first_blood", "First Blood", CAT_COMBAT, TIER_BRONZE,
        "Awarded for your first combat victory",
        "combat_victories", 1, "blood_first", "first_blood",
        effect=_effect("one_time", credits=150, turns=10,
                       notes="Blooded bounty — first-kill celebration, fires once."),
    ),
    "combat.bronze_star": _medal(
        "combat.bronze_star", "Bronze Star", CAT_COMBAT, TIER_BRONZE,
        "Awarded for 100 combat victories",
        "combat_victories", 100, "star_bronze", "bronze_star",
        effect=_effect("passive", hook=HOOK_COMBAT_DAMAGE, scope="loop:combat",
                       magnitude=1.0, notes="+1% combat damage (passive)."),
    ),
    "combat.silver_star": _medal(
        "combat.silver_star", "Silver Star", CAT_COMBAT, TIER_SILVER,
        "Awarded for 1000 combat victories",
        "combat_victories", 1000, "star_silver", "silver_star",
        effect=_effect("passive", hook=HOOK_COMBAT_DAMAGE, scope="loop:combat",
                       magnitude=2.0, notes="+2% combat damage; supersedes Bronze Star."),
    ),
    "combat.quantum_cross": _medal(
        "combat.quantum_cross", "Quantum Cross", CAT_COMBAT, TIER_GOLD,
        "Awarded for defeating a player 5+ ranks above you",
        "rank_upset", 5, "cross_quantum", "quantum_cross",
        effect=_effect("passive", hook=HOOK_COMBAT_DAMAGE, scope="loop:combat",
                       magnitude=2.0, notes="Flat +2% combat (Silver Star duplicate; Q9 conditional NOT built)."),
    ),
    "combat.fleet_commander": _medal(
        "combat.fleet_commander", "Fleet Commander", CAT_COMBAT, TIER_SILVER,
        "Awarded for commanding a fleet of 5+ ships",
        "ships_owned", 5, "commander_fleet", "fleet_commander",
        effect=_effect("passive", hook=HOOK_COMBAT_DAMAGE, scope="loop:combat",
                       magnitude=1.5, notes="+1.5% combat damage (coordinated firepower)."),
    ),
    # ── Economic ────────────────────────────────────────────────────
    "economic.traders_merit": _medal(
        "economic.traders_merit", "Trader's Merit", CAT_ECONOMIC, TIER_BRONZE,
        "Awarded for completing 500 trades",
        "total_trades", 500, "medal_trade", "traders_merit",
        effect=_effect("passive", hook=HOOK_TRADING_DISCOUNT, scope="loop:trade",
                       magnitude=0.5, notes="−0.5% buy / +0.5% sell edge."),
    ),
    "economic.merchant_prince": _medal(
        "economic.merchant_prince", "Merchant Prince", CAT_ECONOMIC, TIER_GOLD,
        "Awarded for accumulating 10,000,000 credits lifetime",
        "lifetime_credits", 10000000, "crown_merchant", "merchant_prince",
        effect=_effect("passive", hook=HOOK_TRADING_DISCOUNT, scope="loop:trade",
                       magnitude=1.0, notes="−1% buy / +1% sell edge; supersedes Trader's Merit."),
    ),
    # ── Exploration ─────────────────────────────────────────────────
    "exploration.explorers_badge": _medal(
        "exploration.explorers_badge", "Explorer's Badge", CAT_EXPLORATION, TIER_SILVER,
        "Awarded for visiting 500 unique sectors",
        "sectors_visited", 500, "badge_explorer", "explorers_badge",
        effect=_effect("passive", hook=HOOK_TURN_REGEN, scope="global",
                       magnitude=0.02, notes="+0.02 turn-regen multiplier (more fuel to roam)."),
    ),
    "exploration.colonizer": _medal(
        "exploration.colonizer", "Colonizer", CAT_EXPLORATION, TIER_BRONZE,
        "Awarded for colonizing your first planet",
        "planets_colonized", 1, "flag_colony", "colonizer",
        effect=_effect("one_time", credits=200,
                       notes="Colonization bounty — first-colony celebration, fires once."),
    ),
    "exploration.genesis_award": _medal(
        "exploration.genesis_award", "Genesis Award", CAT_EXPLORATION, TIER_GOLD,
        "Awarded for creating 25 planets with genesis devices",
        "planets_created", 25, "award_genesis", "genesis_award",
        # HYBRID (FINAL): one-time +1000cr AND keep a +0.02 turn-regen passive.
        # We encode the one-time grant as the primary effect and carry the passive
        # turn_regen on a dedicated sub-key the bonus folder also reads.
        effect={
            "kind": "one_time",
            "scope": "global",
            "grants": {"credits": 1000, "turns": 0},
            "passive_extra": {"hook": HOOK_TURN_REGEN, "magnitude": 0.02},
            "notes": "Hybrid: +1000cr once + +0.02 turn-regen passive (prolific creator).",
        },
    ),
    # ── Diplomatic ──────────────────────────────────────────────────
    "diplomatic.ambassadors_star": _medal(
        "diplomatic.ambassadors_star", "Ambassador's Star", CAT_DIPLOMATIC, TIER_GOLD,
        "Awarded for reaching HONORED reputation with 10 factions",
        "faction_honored", 10, "star_ambassador", "ambassadors_star",
        effect=_effect("passive", hook=HOOK_HAGGLE_BAND, scope="loop:trade",
                       magnitude=0.03, notes="+0.03 haggle band ease (diplomatic standing)."),
    ),
    # ── Special ─────────────────────────────────────────────────────
    "special.arias_favor": _medal(
        "special.arias_favor", "ARIA's Favor", CAT_SPECIAL, TIER_UNIQUE,
        "Awarded for reaching maximum consciousness level with ARIA",
        "aria_consciousness", 5, "favor_aria", "arias_favor",
        effect=_effect("passive", hook=HOOK_TURN_REGEN, scope="global",
                       magnitude=0.03, notes="+0.03 turn-regen (ARIA's favor floor)."),
    ),
    "special.orange_cat_society": _medal(
        "special.orange_cat_society", "Orange Cat Society", CAT_SPECIAL, TIER_UNIQUE,
        "Awarded for discovering the hidden Orange Cat sector",
        # WO-CG3: DISTINCT trigger_type (was the shared "special_discovery" @1) so a
        # count-based _evaluate_and_award can never sweep the special-medal group.
        # threshold=1: the single cat-mention-during-first-login earn event. Wired.
        "cat_mention_first_login", 1, "cat_orange", "orange_cat_society",
        # SPECIAL-CASE (FINAL): the PUBLISHED +15% haggle ease, EXEMPT from the
        # +0.08 medal cap. Applied through haggle_service's dedicated Orange-Cat
        # lever (ORANGE_CAT_BAND_FACTOR), NOT through the capped generic fold —
        # kind "special" so get_active_medal_bonuses never double-applies it.
        effect=_effect(EFFECT_KIND_SPECIAL, hook=HOOK_HAGGLE_BAND, scope="loop:trade",
                       magnitude=0.15,
                       notes="Published +15% haggle ease; EXEMPT from cap; via dedicated lever."),
    ),

    # ════════════════════════════════════════════════════════════════
    # EXPANSION (WO-CG 18→30) — fill motivation gaps; effects per FINAL spec.
    # ════════════════════════════════════════════════════════════════

    # ── 3.1 Economic / trade ────────────────────────────────────────
    "economic.spread_hunter": _medal(
        "economic.spread_hunter", "Spread Hunter", CAT_ECONOMIC, TIER_BRONZE,
        "Awarded for 50 profitable round-trips",
        "profitable_round_trips", 50, "hunter_spread", "spread_hunter",
        effect=_effect("passive", hook=HOOK_TRADING_DISCOUNT, scope="loop:trade",
                       magnitude=0.3, notes="−0.3% buy/sell sliver."),
    ),
    "economic.cartel_breaker": _medal(
        "economic.cartel_breaker", "Cartel Breaker", CAT_ECONOMIC, TIER_SILVER,
        "Awarded for selling 1,000,000 credits of one commodity in a region",
        "regional_commodity_sales", 1000000, "breaker_cartel", "cartel_breaker",
        effect=_effect("one_time", credits=1000,
                       notes="Credit bounty, fires once."),
    ),
    "economic.port_baron": _medal(
        "economic.port_baron", "Port Baron", CAT_ECONOMIC, TIER_GOLD,
        "Awarded for owning 5 ports simultaneously",
        "ports_owned", 5, "baron_port", "port_baron",
        # Q6: port-ownership exposes no per-owner tax-take stat to bump (verified
        # in scan) — cosmetic-only rather than invent a stat.
        effect=None,
    ),

    # ── 3.2 Exploration ─────────────────────────────────────────────
    "exploration.pathfinder": _medal(
        "exploration.pathfinder", "Pathfinder", CAT_EXPLORATION, TIER_BRONZE,
        "Awarded for personally discovering your first sector",
        "sectors_discovered", 1, "finder_path", "pathfinder",
        effect=_effect("one_time", turns=15,
                       notes="Discovery turn grant, fires once."),
    ),
    "exploration.cartographer": _medal(
        "exploration.cartographer", "Cartographer", CAT_EXPLORATION, TIER_SILVER,
        "Awarded for visiting 1000 unique sectors",
        "sectors_visited", 1000, "cartographer", "cartographer",
        effect=_effect("passive", hook=HOOK_TURN_REGEN, scope="global",
                       magnitude=0.02, notes="+0.02 turn-regen (more fuel to roam)."),
    ),
    "exploration.void_walker": _medal(
        "exploration.void_walker", "Void Walker", CAT_EXPLORATION, TIER_GOLD,
        "Awarded for 10 Quantum-Jumps into uncharted void",
        "void_jumps", 10, "walker_void", "void_walker",
        effect=_effect("passive", hook=HOOK_TURN_REGEN, scope="global",
                       magnitude=0.03, notes="+0.03 turn-regen; supersedes Cartographer."),
    ),
    "exploration.worldsmith": _medal(
        "exploration.worldsmith", "Worldsmith", CAT_EXPLORATION, TIER_PLATINUM,
        "Awarded for creating 100 planets via Genesis",
        "planets_created", 100, "worldsmith", "worldsmith",
        effect=_effect("one_time", credits=2000,
                       notes="Catalog apex one-time grant, fires once."),
    ),

    # ── 3.3 Combat ──────────────────────────────────────────────────
    "combat.drone_reaper": _medal(
        "combat.drone_reaper", "Drone Reaper", CAT_COMBAT, TIER_BRONZE,
        "Awarded for clearing 100 sector drones",
        "drones_cleared", 100, "reaper_drone", "drone_reaper",
        effect=_effect("passive", hook=HOOK_COMBAT_DAMAGE, scope="loop:combat",
                       magnitude=0.5, notes="+0.5% combat damage sliver."),
    ),
    "combat.siege_master": _medal(
        "combat.siege_master", "Siege Master", CAT_COMBAT, TIER_SILVER,
        "Awarded for 25 successful planetary assaults",
        "planetary_assaults", 25, "master_siege", "siege_master",
        effect=_effect("passive", hook=HOOK_COMBAT_DAMAGE, scope="loop:combat",
                       magnitude=1.5, notes="+1.5% combat damage."),
    ),
    "combat.bounty_hunter": _medal(
        "combat.bounty_hunter", "Bounty Hunter", CAT_COMBAT, TIER_SILVER,
        "Awarded for collecting 50 bounties",
        "bounties_collected", 50, "hunter_bounty", "bounty_hunter",
        effect=_effect("one_time", credits=1000,
                       notes="Credit bounty, fires once."),
    ),
    "combat.untouchable": _medal(
        "combat.untouchable", "Untouchable", CAT_COMBAT, TIER_GOLD,
        "Awarded for surviving 100 combats with zero ship losses",
        "flawless_combats", 100, "untouchable", "untouchable",
        # FINAL correction: re-hooked off the mis-read defender term → a flat
        # combat_damage passive (no real per-player mitigation stat exists).
        effect=_effect("passive", hook=HOOK_COMBAT_DAMAGE, scope="loop:combat",
                       magnitude=1.0, notes="Flat +1% combat (re-hooked; no mitigation stat)."),
    ),

    # ── 3.4 Social / governance / diplomacy ─────────────────────────
    "diplomatic.first_citizen": _medal(
        "diplomatic.first_citizen", "First Citizen", CAT_DIPLOMATIC, TIER_BRONZE,
        "Awarded for casting your first governance vote",
        "governance_votes", 1, "citizen_first", "first_citizen",
        effect=None,  # governance has no per-player resolver stat — cosmetic-only.
    ),
    "diplomatic.lawgiver": _medal(
        "diplomatic.lawgiver", "Lawgiver", CAT_DIPLOMATIC, TIER_SILVER,
        "Awarded for authoring a passed regional ordinance",
        "ordinances_passed", 1, "lawgiver", "lawgiver",
        effect=None,  # cosmetic-only.
    ),
    "diplomatic.peacemaker": _medal(
        "diplomatic.peacemaker", "Peacemaker", CAT_DIPLOMATIC, TIER_SILVER,
        "Awarded for reaching HONORED reputation with 3 factions",
        "faction_honored", 3, "peacemaker", "peacemaker",
        effect=_effect("passive", hook=HOOK_HAGGLE_BAND, scope="loop:trade",
                       magnitude=0.02, notes="+0.02 haggle band sliver."),
    ),
    "economic.quartermaster": _medal(
        "economic.quartermaster", "Quartermaster", CAT_ECONOMIC, TIER_BRONZE,
        "Awarded for completing 25 contracts",
        "contracts_completed", 25, "quartermaster", "quartermaster",
        effect=_effect("one_time", credits=700,
                       notes="Credit bounty, fires once."),
    ),
    "diplomatic.beacon_keeper": _medal(
        "diplomatic.beacon_keeper", "Beacon Keeper", CAT_DIPLOMATIC, TIER_BRONZE,
        "Awarded for placing 10 message beacons",
        "beacons_placed", 10, "keeper_beacon", "beacon_keeper",
        effect=None,  # messaging has no resolver stat — cosmetic-only.
    ),
    "diplomatic.team_founder": _medal(
        "diplomatic.team_founder", "Team Founder", CAT_DIPLOMATIC, TIER_BRONZE,
        "Awarded for founding a team that reaches 5 members",
        "team_members", 5, "founder_team", "team_founder",
        effect=None,  # cosmetic-only.
    ),

    # ── 3.5 Special / hidden ────────────────────────────────────────
    "special.honorary_tabby": _medal(
        "special.honorary_tabby", "Honorary Tabby", CAT_SPECIAL, TIER_UNIQUE,
        "Awarded for a second hidden cat-related discovery",
        # WO-CG3: DISTINCT trigger_type (was the shared "special_discovery" @2).
        # threshold=1: the composite first-login earn event — cat-mention AND
        # negotiation_skill=STRONG AND awarded ship rarity_tier>=3, all in one
        # session (the dispatcher gates the conjunction, then fires this @1). Wired.
        "honorary_tabby_combo", 1, "tabby_honorary", "honorary_tabby",
        effect=_effect("passive", hook=HOOK_HAGGLE_BAND, scope="loop:trade",
                       magnitude=0.02, notes="+0.02 haggle band sliver (cat-charm)."),
    ),
    "special.pioneer_office_pillar": _medal(
        "special.pioneer_office_pillar", "Pillar of the Pioneer Office", CAT_ECONOMIC, TIER_SILVER,
        "Transport 10,000 colonists in cryosleep transit (lifetime)",
        # WO-CG3 follow-up (Orchestrator ruling 2026-06-20T23:57): the doc-identity
        # collision is RESOLVED — medals.md is authoritative: this is the ECONOMIC /
        # SILVER medal earned by hauling >=10,000 colonists in cryosleep (lifetime),
        # effect -2% per-pioneer migration fee at a Capital Class-0 station. Aligned
        # category/tier/criterion to canon (was Special/Unique "civic-recognition"
        # code drift); stable id + legacy_key kept so existing PlayerMedal rows are
        # unaffected. STILL UNWIRED — gated on a lifetime-colonist-transport counter
        # that doesn't exist yet (filed as a future counter WO); distinct trigger_type
        # keeps it admin-grant-safe + collision-proof. No auto-award dispatcher.
        "colonists_transported_lifetime", 10000, "pillar_pioneer", "pioneer_office_pillar",
        # FINAL: Pioneer Office keeps published −2%. Sign convention: positive
        # magnitude on trading_discount = a buy discount / sell uplift.
        effect=_effect("passive", hook=HOOK_TRADING_DISCOUNT, scope="loop:trade",
                       magnitude=2.0, notes="Published −2% fee (−2% buy / +2% sell)."),
    ),
    "special.ghost_in_the_static": _medal(
        "special.ghost_in_the_static", "Ghost in the Static", CAT_SPECIAL, TIER_UNIQUE,
        "Awarded for discovering a lost world in dark territory",
        # WO-CG3: DISTINCT trigger_type (was the shared "special_discovery" @4) so it
        # is admin-grant-safe and group-collision-proof. PARKED — UNWIRED: NO-CANON
        # (no Special/Hidden doc entry defines it) and no supporting "dark-territory /
        # lost-world discovery" game system exists. No dispatcher; no invented
        # criterion. Admin-grant-only until canon defines it. Routed to Max.
        "dark_territory_discovery", 1, "ghost_static", "ghost_in_the_static",
        effect=None,  # pure prestige — cosmetic-only.
    ),
}


# Reverse map: legacy short key -> stable namespaced id. Used by the
# read-compat path so callers (and old JSONB rows) referencing e.g.
# ``bronze_star`` resolve to ``combat.bronze_star``.
LEGACY_KEY_TO_ID: Dict[str, str] = {
    entry["criteria"]["legacy_key"]: medal_id
    for medal_id, entry in MEDAL_CATALOG.items()
}


def get_catalog_entry(medal_id_or_legacy: str) -> Dict[str, Any]:
    """Resolve a catalog entry by stable id OR legacy short key. Returns {} if unknown."""
    if medal_id_or_legacy in MEDAL_CATALOG:
        return MEDAL_CATALOG[medal_id_or_legacy]
    resolved = LEGACY_KEY_TO_ID.get(medal_id_or_legacy)
    if resolved:
        return MEDAL_CATALOG[resolved]
    return {}


def medals_for_trigger(trigger_type: str) -> List[Dict[str, Any]]:
    """All catalog entries whose criteria trigger on the given trigger_type."""
    return [
        entry
        for entry in MEDAL_CATALOG.values()
        if entry["criteria"].get("type") == trigger_type
    ]


# ---------------------------------------------------------------------------
# Idempotent seed — upsert the catalog into the ``medals`` table.
# Mirrors src/core/ship_specifications_seeder.py:seed_ship_specifications:
# update existing rows in place, insert missing ones, safe on every re-run.
# ---------------------------------------------------------------------------
def seed_medals(db) -> int:
    """Idempotently upsert :data:`MEDAL_CATALOG` into the ``medals`` table.

    Returns the number of catalog entries processed (created + updated).
    Safe to call on every startup; never deletes rows (ON DELETE RESTRICT on
    PlayerMedal.medal_id would block deletion of held medals anyway).
    """
    import logging
    from src.models.medal import Medal

    logger = logging.getLogger(__name__)

    processed = 0
    for medal_id, entry in MEDAL_CATALOG.items():
        existing = db.query(Medal).filter(Medal.id == medal_id).first()
        if existing is None:
            db.add(
                Medal(
                    id=entry["id"],
                    name=entry["name"],
                    description=entry["description"],
                    category=entry["category"],
                    tier=entry["tier"],
                    criteria=entry["criteria"],
                    effect=entry.get("effect"),
                )
            )
        else:
            # Update mutable metadata in place (text rewrites, balance tweaks).
            existing.name = entry["name"]
            existing.description = entry["description"]
            existing.category = entry["category"]
            existing.tier = entry["tier"]
            existing.criteria = entry["criteria"]
            existing.effect = entry.get("effect")
        processed += 1

    db.commit()
    logger.info("Seeded %d medals into the catalog", processed)
    return processed
