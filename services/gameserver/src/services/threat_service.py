"""Threat rollup service — per-sector threat score over the player's known
graph (WO-UI2-TACTICAL-THREAT-ENDPOINT).

STATIC-ONLY scope (REVISE, post-ship): cipher + the lead confirmed a HIGH
security leak in the original design and the orchestrator RULED this rollup
static-only. The two LIVE, per-sector composition inputs that shipped in the
first pass — ``hostiles_present`` (live hostile NPCCharacter presence) and
``inbound_squads`` (live unresolved PendingEngagement rows) — are REMOVED,
not just zeroed. Why:

  - ``inbound_squads`` was never a clean "a squad is inbound" signal in the
    first place: ``PendingEngagement.arrival_sector_id`` (models/pending_
    engagement.py:72) is only populated once the row transitions to
    ``ARRIVED`` — i.e. a police squad ALREADY fighting a SPECIFIC named
    player in THAT player's current sector (see the model's own docstring:
    "squad placed in the offender's current sector, ENGAGED"). Surfacing
    that graph-wide, keyed only by sector, leaks one player's live combat
    encounter to every OTHER player who merely knows that sector — a
    confirmed HIGH cross-player intelligence leak. There is no clean
    genuinely-pending-arrival signal on this model to expose instead.
  - ``hostiles_present`` has the same shape of problem one level down: live
    NPC composition/count for a sector the player is NOT currently in is
    real-time intel about somewhere else, at odds with this codebase's own
    fog-of-war precedent — ``GET /player/current-sector``
    (api/routes/player.py:472, :503-504) enriches live NPC presence/activity
    ONLY for the player's CURRENT sector, never for the wider known graph.
    A graph-wide threat rollup exposing live hostile counts for sectors the
    player isn't standing in breaks that same precedent.

Both removed inputs were cross-player/cross-sector LIVE composition data —
qualitatively different from the four STATIC inputs kept below, none of
which reveal what any other player or squad is doing right now:
``Sector.security_level`` / ``Sector.hazard_level`` (sector attributes),
``Sector.last_combat`` (a historical timestamp, not a live position), and
region-level aggregate pirate pressure (an ecosystem-wide score, not a
per-player position). Read-only aggregation of these four inputs only. NO
migration, NO writes — this module never calls ``db.add`` / ``db.flush`` /
``db.commit``.

Grounding (verified against the live models before building):
  - ``Sector.security_level``  models/sector.py:78  (Integer, nullable, default 5, 1-10)
  - ``Sector.hazard_level``    models/sector.py:97  (Integer, NOT NULL, default 0, 0-10)
  - ``Sector.last_combat``     models/sector.py:133 (DateTime tz, nullable)
  - ``Sector.region_id``       models/sector.py:77  (UUID FK, nullable)
  - ``pirate_ecosystem_service.compute_population_score(db, region_id)`` :144
  - ``nav_service.NavService.get_known_sector_ids(player)`` :110 — same
    known-graph assembly ``GET /nav/chart`` and ``POST /nav/plot`` use.

Pirate-pressure derivation (documented choice): the formula's
``pirate_modifier(0..PIRATE_MAX)`` term is fed by
``pirate_ecosystem_service.compute_population_score(db, region_id)`` — the
LIVE (not cached-snapshot) tier-weighted sum of the region's
pirate-controlled holdings (CAMP=1 / OUTPOST=3 / STRONGHOLD=10,
``pirate_ecosystem_service.TIER_WEIGHT``), clamped to ``PIRATE_MAX``. This
is a REGION-level aggregate score, not per-player/per-sector live position
data, so it does not carry the same cross-player leak the two removed
inputs did. NOT ``suppression_modifier`` (:164): that field is an INVERSE
decay factor (0.10-1.0) driving the weekly growth-tick's target-population
math, not a threat magnitude — using it directly would be semantically
backwards (a LOW suppression_modifier means heavy recent player killing,
i.e. LESS pirate presence, not more) and would need an unstated
inversion/rescale. NOT ``get_pirate_ecosystem_state``'s cached
``current_population_score`` either: that JSONB snapshot column is written
only by ``refresh_pirate_ecosystem_snapshot`` / ``run_weekly_tick`` and
defaults to 0 until the region's first weekly tick ever runs, which would
silently under-report pirate presence for a region that already has live
PirateHolding rows but has never ticked. ``compute_population_score`` queries
PirateHolding directly, so it is always live, and the null-safety
requirement ("missing region pirate state -> 0") is satisfied structurally:
a region with no holdings scores 0 either way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from src.models.player import Player
from src.models.sector import Sector
from src.services.nav_service import NavService
from src.services.pirate_ecosystem_service import compute_population_score

# ---------------------------------------------------------------------------
# Formula constants — every weight/cap/threshold/band-edge named here, ALL
# TENTATIVE pending Max's ratification (WO-UI2-TACTICAL-THREAT-ENDPOINT).
# STATIC-ONLY (REVISE): hostiles_present/inbound_squads terms removed, see
# the module docstring's "STATIC-ONLY scope" note.
#
#   score = (10 - security_level) * SEC_W
#         + hazard_level * HAZ_W
#         + (RECENT_COMBAT_W if last_combat within RECENT_COMBAT_WINDOW_H else 0)
#         + pirate_modifier(0..PIRATE_MAX)
#   score = clamp(score, SCORE_MIN, SCORE_MAX)
#
# Reachability check (security 1-10, hazard 0-10 scales): worst-case static
# sector is security=1, hazard=10, recent combat, max pirate pressure ->
# (10-1)*4=36 + 10*2=20 + 15 + 15 = 86 -> LETHAL (>=75) is reachable.
# HOSTILE (50-74) e.g. security=1 (36) + hazard=8 (16) + recent_combat (15)
# = 67.
# ---------------------------------------------------------------------------

SEC_W = 4  # TENTATIVE — pending Max ratify
HAZ_W = 2  # TENTATIVE — pending Max ratify
RECENT_COMBAT_W = 15  # TENTATIVE — pending Max ratify
RECENT_COMBAT_WINDOW_H = 24  # TENTATIVE — pending Max ratify
PIRATE_MAX = 15  # TENTATIVE — pending Max ratify

SCORE_MIN = 0  # TENTATIVE — pending Max ratify
SCORE_MAX = 100  # TENTATIVE — pending Max ratify

BAND_CLEAR_MAX = 24  # TENTATIVE — pending Max ratify
BAND_CAUTION_MAX = 49  # TENTATIVE — pending Max ratify
BAND_HOSTILE_MAX = 74  # TENTATIVE — pending Max ratify
# 75..SCORE_MAX -> LETHAL (implicit ceiling at SCORE_MAX)

BAND_CLEAR = "CLEAR"
BAND_CAUTION = "CAUTION"
BAND_HOSTILE = "HOSTILE"
BAND_LETHAL = "LETHAL"

# Sector.security_level's own column default (models/sector.py:78) — reused
# here as the null-safety fallback per the WO's explicit instruction.
DEFAULT_SECURITY_LEVEL = 5

# Contributor "input" labels — named constants so callers/tests never
# hardcode the strings twice.
CONTRIB_LOW_SECURITY = "low_security"
CONTRIB_HAZARD = "hazard"
CONTRIB_RECENT_COMBAT = "recent_combat"
CONTRIB_PIRATE_PRESSURE = "pirate_pressure"


@dataclass
class ThreatContributor:
    """One named, positive-points driver behind a sector's score."""

    input: str
    points: int


@dataclass
class ThreatEntry:
    """One sector's threat rollup (mirrors nav_service.HopInfo's shape)."""

    sector_id: int
    score: int
    band: str
    contributors: List[ThreatContributor] = field(default_factory=list)


def _band_for_score(score: int) -> str:
    if score <= BAND_CLEAR_MAX:
        return BAND_CLEAR
    if score <= BAND_CAUTION_MAX:
        return BAND_CAUTION
    if score <= BAND_HOSTILE_MAX:
        return BAND_HOSTILE
    return BAND_LETHAL


def _pirate_pressure_by_region(db: Session, region_ids: set) -> Dict[object, int]:
    """pirate_modifier(0..PIRATE_MAX) per region — see the module docstring's
    "Pirate-pressure derivation" note for why compute_population_score (LIVE)
    is the chosen source, not suppression_modifier or the cached snapshot.
    A region with no PirateHolding rows scores 0 (compute_population_score's
    own empty-list-sums-to-0 behaviour) — the null-safety fallback falls out
    of the real function, not a special case here."""
    return {
        region_id: max(0, min(PIRATE_MAX, compute_population_score(db, region_id)))
        for region_id in region_ids
        if region_id is not None
    }


def _score_sector(
    sector: Sector,
    *,
    pirate_pressure_by_region: Dict[object, int],
    now: datetime,
) -> ThreatEntry:
    security = sector.security_level if sector.security_level is not None else DEFAULT_SECURITY_LEVEL
    hazard = sector.hazard_level if sector.hazard_level is not None else 0
    pirate_points = (
        pirate_pressure_by_region.get(sector.region_id, 0) if sector.region_id is not None else 0
    )

    security_points = max(0, 10 - security) * SEC_W
    hazard_points = max(0, hazard) * HAZ_W

    recent_combat = (
        sector.last_combat is not None
        and sector.last_combat >= now - timedelta(hours=RECENT_COMBAT_WINDOW_H)
    )
    combat_points = RECENT_COMBAT_W if recent_combat else 0

    contributors: List[ThreatContributor] = []
    if security_points > 0:
        contributors.append(ThreatContributor(input=CONTRIB_LOW_SECURITY, points=security_points))
    if hazard_points > 0:
        contributors.append(ThreatContributor(input=CONTRIB_HAZARD, points=hazard_points))
    if combat_points > 0:
        contributors.append(ThreatContributor(input=CONTRIB_RECENT_COMBAT, points=combat_points))
    if pirate_points > 0:
        contributors.append(ThreatContributor(input=CONTRIB_PIRATE_PRESSURE, points=pirate_points))

    raw_score = security_points + hazard_points + combat_points + pirate_points
    score = max(SCORE_MIN, min(SCORE_MAX, raw_score))

    return ThreatEntry(
        sector_id=sector.sector_id,
        score=score,
        band=_band_for_score(score),
        contributors=contributors,
    )


def compute_threat_rollup(db: Session, player: Player, *, now: Optional[datetime] = None) -> List[ThreatEntry]:
    """Per-sector threat rollup over ``player``'s KNOWN graph
    (``NavService.get_known_sector_ids`` — the same known-graph assembly
    ``GET /nav/chart`` and ``POST /nav/plot`` use). PLAYER-SCOPED: the
    returned sector set is EXACTLY the known-graph set, never wider — a
    player must not see threat data for a sector they don't know.

    STATIC-ONLY (REVISE): scores from Sector attributes (security_level /
    hazard_level / last_combat) and region-level pirate pressure only — see
    the module docstring for why the two live per-sector composition inputs
    (hostile NPC presence, inbound police squads) were removed rather than
    zeroed.

    Read-only: issues 1 batched Sector query plus one compute_population_
    score call per unique region among the known sectors — safe for a known
    graph spanning hundreds of sectors. Never crashes on missing/null
    inputs (accept e) — every lookup below falls back to 0/None via .get()
    or an explicit None check.
    """
    now = now if now is not None else datetime.now(timezone.utc)

    known_ids = NavService(db).get_known_sector_ids(player)
    if not known_ids:
        return []

    sectors = db.query(Sector).filter(Sector.sector_id.in_(known_ids)).all()

    region_ids = {s.region_id for s in sectors if s.region_id is not None}
    pirate_pressure_by_region = _pirate_pressure_by_region(db, region_ids)

    entries = [
        _score_sector(
            sector,
            pirate_pressure_by_region=pirate_pressure_by_region,
            now=now,
        )
        for sector in sectors
    ]
    entries.sort(key=lambda e: e.sector_id)
    return entries
