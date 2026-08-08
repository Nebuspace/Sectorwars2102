"""Ground-expedition risk-roll engine (ADR-0091 §1/§3 "The at-launch
expedition model"). Every expedition is a fresh RNG roll generated at
launch -- there is no CandidateSite, no site_seed, no per-planet
pre-stored result. The planet's profile (type/hazard/size) *weights*
which shape-class template is drawn but never determines the SUCCESS/
PARTIAL/FAILURE outcome tier deterministically.

Cost is turns + credits only (M49: no survey-kit consumable in v1).
Hard pity (M20): K consecutive FAILUREs force the next roll to SUCCESS
-- tracked here via a live query of the player's most-recent non-demo
expeditions (no new counter column; the expedition table is itself the
ledger, matching the ADR's "the expedition record itself is the proof"
framing at M36).

Service is FLUSH-ONLY (db.flush(), never db.commit()) -- the route
layer owns the transaction boundary, matching escape_pod_service.py /
distress_service.py convention.
"""
import logging
import secrets
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.data.site_templates import SHAPE_CLASSES, templates_for_shape
from src.models.expedition import Expedition, ExpeditionStatus
from src.models.planet import Planet
from src.models.player import Player
from src.models.ship import Ship

logger = logging.getLogger(__name__)

_RNG = secrets.SystemRandom()

# [P] provisional, pending calibration (ADR-0091 "Remaining [P] numbers").
EXPEDITION_COST_TURNS = 2
EXPEDITION_COST_CREDITS = 500

# M20: K consecutive FAILUREs (ground-tier, general-population) -> forced
# SUCCESS. ADR states K = 4-5 [P]; using the ADR's own upper provisional
# default.
HARD_PITY_K = 5

# M31/§3 "Governors": per-player global concurrency cap of 3 active
# (non-terminal) expeditions. Demo expeditions never count toward this.
MAX_ACTIVE_EXPEDITIONS = 3

# Outcome-tier base weights before planet-profile weighting (ADR §1: planet
# profile weights the roll but does not determine it). [P] provisional.
_BASE_OUTCOME_WEIGHTS = {
    ExpeditionStatus.SUCCESS: 0.55,
    ExpeditionStatus.PARTIAL: 0.25,
    ExpeditionStatus.FAILURE: 0.20,
}

# Radiation/hazard-heavy planets skew slightly more failure-prone; this is a
# mild weighting nudge, never a determinism (ADR §1). [P] provisional.
_HIGH_HAZARD_RADIATION_THRESHOLD = 0.6
_HIGH_HAZARD_FAILURE_BONUS = 0.08


class ExpeditionError(Exception):
    """Raised for player-facing expedition failures; .args[0] is the
    human-readable detail string the route layer surfaces as a 4xx."""


def _active_expedition_count(db: Session, player_id) -> int:
    """Non-terminal = PENDING (demo expeditions never counted -- M39)."""
    return (
        db.query(func.count(Expedition.id))
        .filter(
            Expedition.player_id == player_id,
            Expedition.demo.is_(False),
            Expedition.status == ExpeditionStatus.PENDING,
        )
        .scalar()
        or 0
    )


def _consecutive_failure_streak(db: Session, player_id) -> int:
    """Walk the player's most-recent non-demo expeditions newest-first,
    counting a leading run of FAILUREs. Any non-FAILURE terminal status
    (or running out of history) ends the streak. Demo rolls are excluded
    -- they are zero-stakes and must not feed the pity counter."""
    recent = (
        db.query(Expedition.status)
        .filter(
            Expedition.player_id == player_id,
            Expedition.demo.is_(False),
            Expedition.status != ExpeditionStatus.PENDING,
        )
        .order_by(Expedition.launched_at.desc())
        .limit(HARD_PITY_K)
        .all()
    )
    streak = 0
    for (status,) in recent:
        if status == ExpeditionStatus.FAILURE:
            streak += 1
        else:
            break
    return streak


def _weighted_outcome(db: Session, player: Player, planet: Planet) -> ExpeditionStatus:
    """Draw SUCCESS/PARTIAL/FAILURE, weighted (not determined) by the
    planet's profile, then apply the hard-pity override (M20)."""
    if _consecutive_failure_streak(db, player.id) >= HARD_PITY_K:
        return ExpeditionStatus.SUCCESS

    weights = dict(_BASE_OUTCOME_WEIGHTS)
    radiation = getattr(planet, "radiation_level", 0.0) or 0.0
    if radiation >= _HIGH_HAZARD_RADIATION_THRESHOLD:
        weights[ExpeditionStatus.FAILURE] += _HIGH_HAZARD_FAILURE_BONUS
        weights[ExpeditionStatus.SUCCESS] = max(
            0.0, weights[ExpeditionStatus.SUCCESS] - _HIGH_HAZARD_FAILURE_BONUS
        )

    statuses = list(weights.keys())
    total = sum(weights.values())
    roll = _RNG.random() * total
    cumulative = 0.0
    for status in statuses:
        cumulative += weights[status]
        if roll <= cumulative:
            return status
    return statuses[-1]


def _shape_class_for_planet(planet: Planet) -> str:
    """Planet profile weights which shape class is more likely (M12); this
    is a v1 placeholder weighting keyed off planet_type string prefix
    matching, falling back to a uniform draw across all shape classes."""
    planet_type = (getattr(planet, "planet_type", None) or "").upper()
    if "VOLCANIC" in planet_type:
        candidates = ["COMPACT", "IRREGULAR", "IRREGULAR", "COMPACT"]
    elif "TERRAN" in planet_type or "OCEAN" in planet_type:
        candidates = ["SPRAWLING", "TERRACED", "TERRACED"]
    else:
        candidates = list(SHAPE_CLASSES)
    return _RNG.choice(candidates)


def _draw_template(planet: Planet, *, guaranteed_good: bool = False) -> dict:
    """Draw a template from the site-template pool, weighted by the
    planet's profile (ADR §1/§6). guaranteed_good (M38) restricts the draw
    to templates meeting the onboarding structural floor (>=12 slots)."""
    if guaranteed_good:
        pool = [t for t in _all_templates() if t["slot_count"] >= 12]
        if not pool:
            pool = _all_templates()
        return _RNG.choice(pool)

    shape_class = _shape_class_for_planet(planet)
    pool = templates_for_shape(shape_class)
    if not pool:
        pool = _all_templates()
    return _RNG.choice(pool)


def _all_templates() -> list:
    from src.data.site_templates import SITE_TEMPLATES

    return SITE_TEMPLATES


def _build_result_payload(template: dict, *, guaranteed_good: bool, partial: bool) -> dict:
    """Ephemeral SiteIntel payload (ADR §1: shape/slots/energy/resources/
    hazards/native_life). guaranteed_good forces the M38 structural floor:
    zero severity-0 hazards, native energy present, >=1 T1-T2 deposit.
    partial (PARTIAL outcome) returns banded/uncertain information --
    slot count and shape are known, energy/resources/hazards are omitted."""
    payload = {
        "shape_class": template["shape_class"],
        "template_id": template["template_id"],
        "usable_slots": template["slot_count"],
    }
    if partial:
        payload["banded"] = True
        return payload

    payload["energy_baseline"] = template["energy_baseline"]
    if guaranteed_good:
        payload["hazards"] = []
        payload["native_energy_present"] = True
        payload["resources"] = [{"tier": "T1", "deposit": "starter_ore"}]
        payload["native_life"] = False
    else:
        payload["hazards"] = []
        payload["resources"] = []
        payload["native_life"] = None
    return payload


def roll_expedition(
    db: Session,
    player: Player,
    planet: Planet,
    ship: Optional[Ship],
    *,
    forced_success: bool = False,
    guaranteed_good: bool = False,
    demo: bool = False,
    waive_cost: bool = False,
) -> Expedition:
    """The core at-launch risk-roll (ADR-0091 §1/§3).

    forced_success / guaranteed_good are the onboarding-comp override
    branches (M35-M44): a comped first colony's expedition is forced to
    SUCCESS with a guaranteed-good result (>=12 slots, zero sev-0 hazards,
    native energy present, >=1 T1-T2 deposit -- M38).

    demo=True (M39: 2-3 free zero-stakes teaching rolls) bypasses the
    cost/concurrency-cap checks entirely and is flagged on the returned
    row; demo rolls never count toward MAX_ACTIVE_EXPEDITIONS and never
    feed the hard-pity streak.

    waive_cost=True (M38: the comped first-colony expedition itself, NOT
    a demo roll) skips only the turns/credits debit -- the concurrency
    cap and pity streak still apply normally, unlike demo=True.

    Raises ExpeditionError for player-facing failures (cap exceeded,
    insufficient turns/credits). Flush-only -- caller commits.
    """
    if not demo:
        active = _active_expedition_count(db, player.id)
        if active >= MAX_ACTIVE_EXPEDITIONS:
            raise ExpeditionError(
                f"You already have {active} active expeditions (max {MAX_ACTIVE_EXPEDITIONS})."
            )
        if not waive_cost:
            if player.turns < EXPEDITION_COST_TURNS:
                raise ExpeditionError("Not enough turns to launch an expedition.")
            if player.credits < EXPEDITION_COST_CREDITS:
                raise ExpeditionError("Not enough credits to launch an expedition.")

            player.turns -= EXPEDITION_COST_TURNS
            player.credits -= EXPEDITION_COST_CREDITS

    if forced_success:
        outcome = ExpeditionStatus.SUCCESS
    else:
        outcome = _weighted_outcome(db, player, planet)

    result_payload = None
    if outcome in (ExpeditionStatus.SUCCESS, ExpeditionStatus.PARTIAL):
        template = _draw_template(planet, guaranteed_good=guaranteed_good)
        result_payload = _build_result_payload(
            template,
            guaranteed_good=guaranteed_good,
            partial=(outcome == ExpeditionStatus.PARTIAL),
        )
    # FAILURE: no stored result (M19) -- result stays NULL.

    expedition = Expedition(
        player_id=player.id,
        planet_id=planet.id,
        ship_id=ship.id if ship is not None else None,
        status=outcome,
        result=result_payload,
        demo=demo,
    )
    db.add(expedition)
    db.flush()  # route owns the commit
    logger.info(
        "Expedition rolled: player=%s planet=%s outcome=%s demo=%s forced=%s",
        player.id, planet.id, outcome.name, demo, forced_success,
    )
    return expedition


def reroll_expedition(
    db: Session,
    player: Player,
    planet: Planet,
    ship: Optional[Ship],
) -> Expedition:
    """A re-roll is a fresh, independent expedition at the same base price
    (ADR §3: "no escalating Kth-scout" -- there is no "same site" to
    re-scout). This is a thin alias over roll_expedition for route-layer
    clarity; it carries no discount and no special-casing."""
    return roll_expedition(db, player, planet, ship)
