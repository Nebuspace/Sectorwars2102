"""Special-formation discovery (WO-CA; per-player since ADR-0045 /
WO-GWQ-FORMATION-KNOWLEDGE).

Mirrors the planet/feature discovery pattern (discovery_service.py): a player
arriving in — or scanning — a sector that is a formation's anchor, or any of its
interior sectors, personally discovers that formation (records a
``PlayerFormationKnowledge`` row -- ADR-0045, mirrors ``PlayerWarpKnowledge``
for the warp-knowledge layer). ``SpecialFormation.is_discovered`` is a global
aggregate (first-ever-discovery flag) still flipped False→True on the FIRST
player to ever discover a formation, and still triggers the one-time public
``name`` back-fill from ``properties["name"]`` (the bang importer only ever
wrote the name into the JSONB, never the dedicated column — see
bang_import_service.py) -- but it no longer gates disclosure to any individual
player; see ``is_formation_known_to_player``, the real per-player gate. One
player's visit must never reveal a formation's identity to every other player.

Discovery is first-observe and idempotent PER PLAYER: a formation this player
already knows is a no-op (no duplicate row, no re-flip). A concurrent double-
visit from two sessions for the same (player, formation) races on the table's
UNIQUE constraint; the loser's INSERT is SAVEPOINT-scoped so its
``IntegrityError`` rolls back only that insert, never the caller's open
transaction (mirrors ``medal_service.award_medal``). Flush-only — the caller
owns the commit (so the flip rides the move's own single commit, exactly like
the ARIA / medal hooks in movement_service).

The reverse "which formations contain this sector" lookup is NOT a SQLAlchemy
relationship — interior membership lives in the ``interior_sector_ids`` ARRAY,
queried via the GIN containment index ``ix_special_formations_interior_sector_ids``
(see SpecialFormation model). So we issue two predicates (anchor match OR interior
containment) in one query.

``discovery_requirement`` (a documented-but-unenforced JSONB precondition) is
intentionally NOT enforced here: no code anywhere reads it yet, and inventing an
unlock rule would be inventing canon. Visiting the sector is the discovery event.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import flag_modified

from src.models.sector import Sector, sector_warps
from src.models.special_formation import (
    SpecialFormation,
    SpecialFormationType,
    PlayerFormationKnowledge,
    FormationRevealedVia,
)
from src.models.station import Station
from src.models.region import Region

logger = logging.getLogger(__name__)


class FormationNotDiscoveredError(Exception):
    """Raised when a player tries to investigate a formation they have not yet
    discovered (the route maps this to 404 — an undiscovered formation does not
    exist from the player's perspective, mirroring identity-withholding)."""


class FormationAlreadyInvestigatedError(Exception):
    """Raised when a formation has already been investigated (the route maps this
    to 409 — investigation is a one-time event; the reward is not repeatable)."""


class GoldBubblePlacementError(Exception):
    """Operator Gold Bubble placement refused (LEG-52 / place_gold_bubble).

    ``code`` is a stable machine token the admin route maps to HTTP status;
    ``detail`` is the human-readable refusal.
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


# Canon default for GOLD_BUBBLE interior_size_min
# (DATA_MODELS/special-formations.md + jsonb-schema.md).
GOLD_BUBBLE_INTERIOR_SIZE_MIN = 100
GOLD_BUBBLE_GATEWAY_COUNT_MIN = 1
GOLD_BUBBLE_GATEWAY_COUNT_MAX = 3

_BUBBLE_FAMILY = (
    SpecialFormationType.BUBBLE,
    SpecialFormationType.DEAD_END_BUBBLE,
    SpecialFormationType.GOLD_BUBBLE,
)


# --- Investigation reward calibration ----------------------------------------
# Ratified: DECISIONS.md anomaly-investigate-reward (human 2026-06-22) —
# one-time credits by rarity: 250 (common) / 500 (uncommon) / 1,000 (rare);
# default for any unmapped formation type is 250.
INVESTIGATE_REWARD_NO_CANON = False

# Rarity tiers per the ruling. (Default for any unmapped/new type = common.)
_FORMATION_INVESTIGATE_CREDITS: Dict[SpecialFormationType, int] = {
    # Common — single-sector or simple terminal topologies.
    SpecialFormationType.DEAD_END: 250,
    SpecialFormationType.WARP_SINK: 250,
    SpecialFormationType.ESCAPE_HATCH: 250,
    SpecialFormationType.BLISTER: 250,
    SpecialFormationType.LOST_SECTOR: 250,
    # Uncommon — multi-sector enclaves and bypass topologies.
    SpecialFormationType.BUBBLE: 500,
    SpecialFormationType.DEAD_END_BUBBLE: 500,
    SpecialFormationType.TUNNEL: 500,
    SpecialFormationType.BACKDOOR: 500,
    SpecialFormationType.LOST_CLUSTER: 500,
    # Rare — operator-placed / large / aggregate island formations.
    SpecialFormationType.GOLD_BUBBLE: 1000,
    SpecialFormationType.ARCHIPELAGO: 1000,
}
_FORMATION_INVESTIGATE_CREDITS_DEFAULT = 250


def _investigate_reward_credits(formation: SpecialFormation) -> int:
    """Rarity-scaled investigate credit reward (DECISIONS.md anomaly-investigate-reward)."""
    return _FORMATION_INVESTIGATE_CREDITS.get(
        formation.type, _FORMATION_INVESTIGATE_CREDITS_DEFAULT
    )


def find_formations_for_sector(db: Session, sector: Sector) -> List[SpecialFormation]:
    """Return every SpecialFormation that includes ``sector`` — as its anchor OR
    as one of its interior sectors. Both predicates key on the Sector UUID
    (``sector.id``): anchor via the FK, interior via GIN array containment."""
    return (
        db.query(SpecialFormation)
        .filter(
            or_(
                SpecialFormation.anchor_sector_id == sector.id,
                SpecialFormation.interior_sector_ids.contains([sector.id]),
            )
        )
        .all()
    )


def is_formation_known_to_player(db: Session, player_id, formation_id) -> bool:
    """True if ``player_id`` has personally discovered ``formation_id``
    (ADR-0045 -- the per-player disclosure gate). This answers "does THIS
    player know about this formation"; ``SpecialFormation.is_discovered``
    remains a global aggregate (first-ever-discovery flag + name back-fill
    trigger) and no longer answers that question for any individual player.
    """
    return (
        db.query(PlayerFormationKnowledge)
        .filter(
            PlayerFormationKnowledge.player_id == player_id,
            PlayerFormationKnowledge.formation_id == formation_id,
        )
        .first()
        is not None
    )


def flip_formation_discovery(db: Session, player, sector: Sector) -> int:
    """Discover, for ``player`` personally, any formation that includes
    ``sector`` and that this player has not already discovered (ADR-0045).

    For each matching formation not yet known to this player: record a
    ``PlayerFormationKnowledge`` row (idempotent -- a concurrent double-visit
    from two sessions for the same (player, formation) races on the table's
    UNIQUE(player_id, formation_id) constraint; the loser's INSERT is
    SAVEPOINT-scoped so its ``IntegrityError`` rolls back only that insert,
    never the caller's open transaction, and is treated as an already-known
    no-op -- mirrors ``medal_service.award_medal``). Also flips the
    formation's global ``is_discovered`` (first-ever-discovery aggregate,
    still written) and, if the dedicated ``name`` column is still NULL,
    back-fills it from ``properties["name"]`` (the bang importer's only home
    for the name).

    Re-visiting a formation this player already knows is a pure no-op — no
    duplicate row, no re-flip, no re-count. Flush-only; caller commits.

    Returns the count of formations newly discovered BY THIS PLAYER this
    call (0 on a no-op / idempotent revisit).
    """
    newly_known = 0
    for formation in find_formations_for_sector(db, sector):
        if is_formation_known_to_player(db, player.id, formation.id):
            continue

        # Global aggregate: first-ever discovery (by any player) flips the
        # flag and back-fills the name once. Independent of per-player state
        # (idempotent: a no-op if some earlier player already flipped it).
        if not formation.is_discovered:
            formation.is_discovered = True
            props = formation.properties or {}
            jsonb_name = props.get("name")
            if not formation.name and jsonb_name:
                formation.name = jsonb_name

        knowledge = PlayerFormationKnowledge(
            player_id=player.id,
            formation_id=formation.id,
            revealed_via=FormationRevealedVia.VISIT,
        )
        try:
            with db.begin_nested():
                db.add(knowledge)
                db.flush()
        except IntegrityError:
            # Lost the race to a concurrent visit from another session for
            # the same (player, formation) -- already known now, not a new
            # discovery for this call. begin_nested already rolled back to
            # the savepoint; nothing else lost.
            logger.info(
                "flip_formation_discovery: player %s already knows formation %s "
                "(race resolved by UNIQUE)",
                getattr(player, "id", None), formation.id,
            )
            continue

        newly_known += 1
        logger.info(
            "Player %s discovered formation %s (%s) in sector %s",
            getattr(player, "id", None),
            formation.id,
            formation.type.name if formation.type else "?",
            sector.sector_id,
        )
    if newly_known:
        db.flush()
    return newly_known


# Key under which investigation state is recorded in the formation's
# ``properties`` JSONB. There is no dedicated ``is_investigated`` column, and the
# WO is additive-only / no-migration — so investigation state rides the existing
# JSONB (additive key), exactly as ``name`` lived in ``properties["name"]`` before
# ADR-0044 promoted it to a first-class column. Shape under this key:
#   {"investigated": True, "investigated_by": "<player-uuid>",
#    "investigated_at": "<iso8601>", "reward_credits": int}
_INVESTIGATION_PROP_KEY = "investigation"


def is_formation_investigated(formation: SpecialFormation) -> bool:
    """True if ``formation`` has already been investigated (state stored in the
    ``properties`` JSONB under ``_INVESTIGATION_PROP_KEY``)."""
    props = formation.properties or {}
    inv = props.get(_INVESTIGATION_PROP_KEY)
    return bool(inv and inv.get("investigated"))


def investigate_formation(
    db: Session, player, formation_id
) -> Dict[str, Any]:
    """Investigate a DISCOVERED special-formation, granting a one-time reward.

    Preconditions (raised as exceptions the route maps to HTTP status):
      * the formation must exist AND be discovered BY THIS PLAYER (ADR-0045,
        ``is_formation_known_to_player``) — else ``FormationNotDiscoveredError``
        (404). Discovery is per-player, set by visiting/scanning the
        formation's sector (see flip_formation_discovery); a formation this
        player has not personally discovered is withheld from them entirely
        — even if some other player has already discovered it — so
        investigating one is indistinguishable from "not found".
      * the formation must not already be investigated — else
        ``FormationAlreadyInvestigatedError`` (409). Investigation is one-time;
        the reward is not repeatable.

    On success: marks the formation investigated (records who/when/reward in the
    ``properties`` JSONB — additive, no schema change), grants the rarity-scaled
    credit reward (DECISIONS.md anomaly-investigate-reward) to the player, and
    returns a payload of the formation details + the investigation reward.
    Commits (mirrors the discovery serializer's commit-on-write).

    Returns a dict payload:
      {
        "formation": {id, type, name, is_discovered, is_investigated, region_id,
                      anchor_sector_id},
        "reward": {"credits": int},
        "credits_remaining": int,
        "reward_is_no_canon": False,  # magnitudes ratified
      }
    """
    # Lock the row for the check-then-set (WO-AI review HIGH: TOCTOU) — concurrent
    # investigates serialize so the second sees the first's committed
    # investigated=True and correctly raises FormationAlreadyInvestigatedError.
    formation: Optional[SpecialFormation] = (
        db.query(SpecialFormation)
        .filter(SpecialFormation.id == formation_id)
        .populate_existing()
        .with_for_update()
        .first()
    )

    # 404 — not found OR not yet discovered BY THIS PLAYER (ADR-0045; identity
    # is withheld pre-discovery per-player, so both collapse to the same "you
    # don't know this exists" response — even if another player already does).
    if formation is None or not is_formation_known_to_player(db, player.id, formation_id):
        raise FormationNotDiscoveredError(
            "Formation not found or not yet discovered."
        )

    # 409 — already investigated; the reward is one-time.
    if is_formation_investigated(formation):
        raise FormationAlreadyInvestigatedError(
            "Formation has already been investigated."
        )

    # Grant the rarity-scaled credit reward (DECISIONS.md anomaly-investigate-reward).
    reward_credits = _investigate_reward_credits(formation)
    player.credits = (player.credits or 0) + reward_credits

    # Record investigation state in the JSONB (additive key — no migration).
    props = dict(formation.properties or {})
    props[_INVESTIGATION_PROP_KEY] = {
        "investigated": True,
        "investigated_by": str(getattr(player, "id", "")),
        "investigated_at": datetime.now(timezone.utc).isoformat(),
        "reward_credits": reward_credits,
    }
    formation.properties = props
    # JSONB in-place reassignment can miss the dirty-tracking; flag explicitly so
    # the change is flushed (mirrors how mutable-JSONB writes are persisted).
    flag_modified(formation, "properties")

    db.commit()
    db.refresh(formation)

    logger.info(
        "Player %s investigated formation %s (%s) — reward %s credits",
        getattr(player, "id", None),
        formation.id,
        formation.type.name if formation.type else "?",
        reward_credits,
    )

    return {
        "formation": {
            "id": str(formation.id),
            "type": formation.type.value if hasattr(formation.type, "value") else str(formation.type),
            "name": formation.name,
            "is_discovered": bool(formation.is_discovered),
            "is_investigated": True,
            "region_id": str(formation.region_id) if formation.region_id else None,
            "anchor_sector_id": str(formation.anchor_sector_id) if formation.anchor_sector_id else None,
        },
        "reward": {"credits": reward_credits},
        "credits_remaining": int(player.credits),
        # False — magnitudes ratified in DECISIONS.md anomaly-investigate-reward.
        "reward_is_no_canon": INVESTIGATE_REWARD_NO_CANON,
    }


# --- Operator Gold Bubble placement (LEG-52 / LEG-DEC-817) -------------------- #


def _dedupe_preserve(ids: Sequence[UUID]) -> List[UUID]:
    seen: Set[UUID] = set()
    out: List[UUID] = []
    for i in ids:
        if i in seen:
            continue
        seen.add(i)
        out.append(i)
    return out


def _assert_bubble_topology(
    db: Session,
    *,
    interior: Set[UUID],
    gateways: Set[UUID],
) -> None:
    """Bubble invariant (DATA_MODELS/special-formations.md): every directed
    edge ``(u → v)`` with ``u`` interior must have ``v ∈ interior ∪ gateways``.
    """
    allowed = interior | gateways
    if not interior:
        return
    rows = (
        db.execute(
            sector_warps.select().where(
                sector_warps.c.source_sector_id.in_(list(interior))
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        dest = row["destination_sector_id"]
        if dest not in allowed:
            raise GoldBubblePlacementError(
                "topology_violation",
                (
                    f"Interior sector {row['source_sector_id']} has warp to "
                    f"{dest} outside the Gold Bubble (not interior or gateway)."
                ),
            )


def _isolate_gold_bubble_warps(
    db: Session,
    *,
    interior: Set[UUID],
    gateways: Set[UUID],
) -> int:
    """Phase B stamp: strip warps that break the Gold Bubble envelope.

    1. Delete outbound warps from interior whose destination is outside
       ``interior ∪ gateways``.
    2. Delete inbound warps into interior whose source is outside
       ``interior ∪ gateways`` (non-gateway exterior entries are backdoors;
       operator placement must not leave accidental gaps).
    """
    allowed = interior | gateways
    interior_list = list(interior)
    deleted = 0

    # Outbound leaks from interior.
    outbound = (
        db.execute(
            sector_warps.select().where(
                sector_warps.c.source_sector_id.in_(interior_list)
            )
        )
        .mappings()
        .all()
    )
    for row in outbound:
        if row["destination_sector_id"] not in allowed:
            db.execute(
                sector_warps.delete().where(
                    and_(
                        sector_warps.c.source_sector_id
                        == row["source_sector_id"],
                        sector_warps.c.destination_sector_id
                        == row["destination_sector_id"],
                    )
                )
            )
            deleted += 1

    # Inbound from exterior (non-gateway) into interior.
    inbound = (
        db.execute(
            sector_warps.select().where(
                sector_warps.c.destination_sector_id.in_(interior_list)
            )
        )
        .mappings()
        .all()
    )
    for row in inbound:
        src = row["source_sector_id"]
        if src not in allowed:
            db.execute(
                sector_warps.delete().where(
                    and_(
                        sector_warps.c.source_sector_id == src,
                        sector_warps.c.destination_sector_id
                        == row["destination_sector_id"],
                    )
                )
            )
            deleted += 1

    return deleted


def place_gold_bubble(
    db: Session,
    *,
    region_id: UUID,
    gateway_sector_ids: Sequence[UUID],
    interior_sector_ids: Sequence[UUID],
    name: Optional[str] = None,
    discovery_requirement: Optional[Dict[str, Any]] = None,
    isolate_warps: bool = True,
) -> SpecialFormation:
    """Operator-only Gold Bubble stamp for a live region (LEG-52).

    Canon: GOLD_BUBBLE is never in the random budget; operators place it by
    hand via an admin endpoint (FEATURES/galaxy/special-formations.md,
    SYSTEMS/special-formations-generation.md). Bang emits zero GOLD_BUBBLE
    rows — this is the live-region path.

    Does **not** invent graph shape: the operator supplies gateway + interior
    sector UUIDs. Optional ``isolate_warps`` (default True) applies Phase B
    envelope isolation against ``sector_warps``, then Phase C validates the
    bubble topology invariant before the row is written.

    Flush-only — the admin route's ``admin_action_attempt`` owns the commit.
    """
    gateways = _dedupe_preserve(list(gateway_sector_ids))
    interior = _dedupe_preserve(list(interior_sector_ids))

    gateway_count = len(gateways)
    if not (
        GOLD_BUBBLE_GATEWAY_COUNT_MIN
        <= gateway_count
        <= GOLD_BUBBLE_GATEWAY_COUNT_MAX
    ):
        raise GoldBubblePlacementError(
            "invalid_gateway_count",
            (
                f"gateway_count must be in "
                f"[{GOLD_BUBBLE_GATEWAY_COUNT_MIN}, "
                f"{GOLD_BUBBLE_GATEWAY_COUNT_MAX}], got {gateway_count}."
            ),
        )

    if len(interior) < GOLD_BUBBLE_INTERIOR_SIZE_MIN:
        raise GoldBubblePlacementError(
            "interior_too_small",
            (
                f"GOLD_BUBBLE requires interior_size >= "
                f"{GOLD_BUBBLE_INTERIOR_SIZE_MIN}, got {len(interior)}."
            ),
        )

    gateway_set = set(gateways)
    interior_set = set(interior)
    overlap = gateway_set & interior_set
    if overlap:
        raise GoldBubblePlacementError(
            "gateway_interior_overlap",
            (
                "Gateway sectors must sit outside the interior "
                f"(overlap: {sorted(str(x) for x in overlap)})."
            ),
        )

    region = db.query(Region).filter(Region.id == region_id).first()
    if region is None:
        raise GoldBubblePlacementError("region_not_found", "Region not found.")

    all_ids = list(gateway_set | interior_set)
    sectors = (
        db.query(Sector)
        .filter(Sector.id.in_(all_ids), Sector.region_id == region_id)
        .all()
    )
    found = {s.id for s in sectors}
    missing = [i for i in all_ids if i not in found]
    if missing:
        raise GoldBubblePlacementError(
            "sector_not_in_region",
            (
                "One or more sectors are missing or not in this region "
                f"({len(missing)} id(s))."
            ),
        )

    capitals = [s for s in sectors if s.id in interior_set and s.is_capital]
    if capitals:
        raise GoldBubblePlacementError(
            "capital_in_interior",
            "Capital sector may not sit inside a GOLD_BUBBLE interior.",
        )

    spacedocks = (
        db.query(Station)
        .filter(
            Station.sector_id.in_(list(interior_set)),
            Station.is_spacedock.is_(True),
        )
        .all()
    )
    if spacedocks:
        raise GoldBubblePlacementError(
            "spacedock_in_interior",
            "SpaceDock sector may not sit inside a GOLD_BUBBLE interior.",
        )

    # Cross-formation overlap (bubble-family interiors + anchors).
    existing = (
        db.query(SpecialFormation)
        .filter(
            SpecialFormation.region_id == region_id,
            SpecialFormation.type.in_(_BUBBLE_FAMILY),
        )
        .all()
    )
    claimed: Set[UUID] = set()
    for f in existing:
        claimed.add(f.anchor_sector_id)
        for sid in f.interior_sector_ids or []:
            claimed.add(sid)
    collision = (gateway_set | interior_set) & claimed
    if collision:
        raise GoldBubblePlacementError(
            "formation_overlap",
            (
                "Proposed Gold Bubble overlaps an existing bubble-family "
                f"formation ({len(collision)} sector(s))."
            ),
        )

    warps_deleted = 0
    if isolate_warps:
        warps_deleted = _isolate_gold_bubble_warps(
            db, interior=interior_set, gateways=gateway_set
        )
    _assert_bubble_topology(db, interior=interior_set, gateways=gateway_set)

    primary_anchor = gateways[0]
    props: Dict[str, Any] = {
        "gateway_count": gateway_count,
        "interior_size_min": GOLD_BUBBLE_INTERIOR_SIZE_MIN,
        "interior_size": len(interior),
    }
    if warps_deleted:
        props["warps_isolated"] = warps_deleted

    formation = SpecialFormation(
        region_id=region_id,
        type=SpecialFormationType.GOLD_BUBBLE,
        name=name,
        anchor_sector_id=primary_anchor,
        interior_sector_ids=list(interior),
        properties=props,
        discovery_requirement=discovery_requirement,
        is_discovered=False,
        generation_seed=None,
    )
    db.add(formation)
    db.flush()
    return formation
