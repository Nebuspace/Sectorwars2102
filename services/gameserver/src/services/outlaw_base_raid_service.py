"""OutlawBase discovery eligibility + raid lifecycle (LEG-INI-19).

Canon: sw2102-docs/DATA_MODELS/npc-lodging.md § Discovery and raid model.

Rules honored here:
- Evaluate only canon example discovery keys (no invented keys).
- NULL ``discovery_requirements`` ⇒ always discoverable when the flag is true.
- Successful raid outcomes: optional operator-configured loot share, optional
  operator-configured influence delta via ``faction_service.adjust_sector_influence``,
  KIA of sleeping residents, 30-day re-raid cooldown, relocation_pending marker.
- Loot share fraction, relocation placement, influence magnitude, and how
  ``min_faction_intel_rep`` maps onto live Reputation rows are DECISION-NEEDED —
  never guessed. Missing operator config skips that outcome and records the gap.

Flush-only — caller owns commit. Combat against defenses / resident NPC ships
is delegated to an injectable resolver that is expected to wrap
``CombatService`` (canonical combat path).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.npc_character import (
    NPCActivity,
    NPCCharacter,
    NPCDeathLog,
    NPCLifecycleStage,
    NPCStatus,
)
from src.models.outlaw_base import OutlawBase
from src.models.sector import Sector

logger = logging.getLogger(__name__)

# Canon example keys only (npc-lodging.md discovery_requirements).
KNOWN_DISCOVERY_KEYS = frozenset(
    {
        "min_faction_intel_rep",
        "requires_item",
        "requires_clue_count",
    }
)

RAID_COOLDOWN = timedelta(days=30)
MAX_AUDIT_ENTRIES = 50

# Operator config keys (not discovery_requirements keys).
CONFIG_LOOT_SHARE_FRACTION = "loot_share_fraction"
CONFIG_INFLUENCE_DELTA = "influence_delta"
CONFIG_HOSTILE_FACTION_ID = "hostile_faction_id"

DECISION_LOOT_SHARE = (
    "DECISION-NEEDED: operator loot_share_fraction (0..1) is unspecified in canon"
)
DECISION_RELOCATION = (
    "DECISION-NEEDED: outlaw-base relocation placement rule is unspecified in canon"
)
DECISION_INFLUENCE = (
    "DECISION-NEEDED: regional influence delta magnitude / hostile faction id "
    "for OutlawBase raids is unspecified in canon"
)
DECISION_INTEL_REP_SOURCE = (
    "DECISION-NEEDED: how min_faction_intel_rep maps to live player state "
    "(no faction-intel system in code; do not invent)"
)


@dataclass(frozen=True)
class DiscoveryContext:
    """Player-side values for known discovery keys.

    Absent fields mean the backing system is unresolved — requirements that
    need them fail closed rather than inventing a source.
    """

    faction_intel_rep: Optional[int] = None
    item_ids: Optional[Set[str]] = None
    clue_count: Optional[int] = None


@dataclass
class EligibilityResult:
    eligible: bool
    reasons: List[str] = field(default_factory=list)
    decision_needed: List[str] = field(default_factory=list)
    unknown_keys: List[str] = field(default_factory=list)


@dataclass
class RaidResult:
    success: bool
    message: str
    idempotent_replay: bool = False
    completion_id: Optional[uuid.UUID] = None
    kia_npc_ids: List[str] = field(default_factory=list)
    loot_taken: Dict[str, int] = field(default_factory=dict)
    influence_applied: bool = False
    decision_needed: List[str] = field(default_factory=list)
    audit_entry: Optional[Dict[str, Any]] = None


CombatResolver = Callable[[Session, OutlawBase, uuid.UUID], Mapping[str, Any]]


def evaluate_discovery_requirements(
    requirements: Optional[Mapping[str, Any]],
    context: DiscoveryContext,
) -> EligibilityResult:
    """Server-authoritative gate over stored discovery_requirements.

    NULL/empty requirements ⇒ eligible (canon: always discoverable).
    Unknown keys ⇒ ineligible (fail closed; do not invent semantics).
    """
    if requirements is None or requirements == {}:
        return EligibilityResult(eligible=True)

    if not isinstance(requirements, Mapping):
        return EligibilityResult(
            eligible=False,
            reasons=["discovery_requirements must be a mapping"],
        )

    unknown = sorted(k for k in requirements.keys() if k not in KNOWN_DISCOVERY_KEYS)
    if unknown:
        return EligibilityResult(
            eligible=False,
            reasons=[f"unknown discovery key(s): {', '.join(unknown)}"],
            unknown_keys=unknown,
        )

    reasons: List[str] = []
    decisions: List[str] = []

    if "min_faction_intel_rep" in requirements:
        need = int(requirements["min_faction_intel_rep"])
        if context.faction_intel_rep is None:
            reasons.append("unresolved:min_faction_intel_rep")
            decisions.append(DECISION_INTEL_REP_SOURCE)
        elif int(context.faction_intel_rep) < need:
            reasons.append(
                f"min_faction_intel_rep: have {context.faction_intel_rep}, need {need}"
            )

    if "requires_item" in requirements:
        item_id = str(requirements["requires_item"])
        if context.item_ids is None:
            reasons.append("unresolved:requires_item")
        elif item_id not in context.item_ids:
            reasons.append(f"requires_item: missing {item_id}")

    if "requires_clue_count" in requirements:
        need = int(requirements["requires_clue_count"])
        if context.clue_count is None:
            reasons.append("unresolved:requires_clue_count")
        elif int(context.clue_count) < need:
            reasons.append(
                f"requires_clue_count: have {context.clue_count}, need {need}"
            )

    return EligibilityResult(
        eligible=not reasons,
        reasons=reasons,
        decision_needed=decisions,
    )


def is_outlaw_base_visible(
    base: OutlawBase,
    context: DiscoveryContext,
) -> EligibilityResult:
    """Hidden when not player-discoverable; else apply discovery_requirements."""
    if not bool(getattr(base, "is_player_discoverable", False)):
        return EligibilityResult(
            eligible=False,
            reasons=["hidden: is_player_discoverable=false"],
        )
    return evaluate_discovery_requirements(base.discovery_requirements, context)


def list_visible_outlaw_bases(
    bases: Iterable[OutlawBase],
    context: DiscoveryContext,
) -> List[OutlawBase]:
    return [b for b in bases if is_outlaw_base_visible(b, context).eligible]


def is_on_raid_cooldown(base: OutlawBase, *, now: Optional[datetime] = None) -> bool:
    until = getattr(base, "raid_cooldown_until", None)
    if until is None:
        return False
    clock = now or datetime.now(timezone.utc)
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > clock


def default_combat_resolver(
    db: Session,
    base: OutlawBase,
    attacker_id: uuid.UUID,
) -> Mapping[str, Any]:
    """Resolve base defenses + resident NPC ships via CombatService.

    Callers that need a different seam (unit tests) inject their own resolver.
    """
    from src.services.combat_service import CombatService

    combat = CombatService(db)
    defenses = base.defenses if isinstance(base.defenses, dict) else {}
    results: Dict[str, Any] = {"success": True, "steps": []}

    drone_count = int(defenses.get("drone_count") or defenses.get("drones") or 0)
    if drone_count > 0:
        step = combat.attack_sector_drones(attacker_id, base.sector_id)
        results["steps"].append({"defenses_drones": step})
        if not step.get("success"):
            results["success"] = False
            results["message"] = step.get("message", "defense drones unresolved")
            return results

    # Resident NPC ships: engage each assigned NPC that still has a ship.
    for raw_id in list(base.assigned_npc_ids or []):
        try:
            npc_id = uuid.UUID(str(raw_id))
        except (ValueError, TypeError):
            continue
        npc = db.query(NPCCharacter).filter(NPCCharacter.id == npc_id).first()
        if npc is None or npc.ship_id is None:
            continue
        if npc.status in (NPCStatus.KIA, NPCStatus.RESPAWNING):
            continue
        # Sleeping residents are caught at raid completion (KIA path), not
        # fought as active combatants.
        activity = npc.current_activity
        if activity == NPCActivity.SLEEP or (
            isinstance(activity, str) and activity.upper() == "SLEEP"
        ):
            continue
        step = combat.attack_npc_ship(attacker_id, npc.ship_id)
        results["steps"].append({"npc_ship": str(npc.id), "result": step})
        if not step.get("success"):
            results["success"] = False
            results["message"] = step.get("message", "resident NPC combat failed")
            return results

    results["message"] = "combat cleared"
    return results


def complete_outlaw_base_raid(
    db: Session,
    *,
    base_id: uuid.UUID,
    attacker_id: uuid.UUID,
    completion_id: uuid.UUID,
    discovery_context: DiscoveryContext,
    operator_config: Optional[Mapping[str, Any]] = None,
    combat_resolver: Optional[CombatResolver] = None,
    now: Optional[datetime] = None,
) -> RaidResult:
    """Atomically complete a successful raid. Idempotent on completion_id.

    On failure (ineligible / cooldown / combat / lock), no durable raid
    outcomes are written (savepoint rollback). Flush-only.
    """
    clock = now or datetime.now(timezone.utc)
    config = dict(operator_config or {})
    resolver = combat_resolver or default_combat_resolver
    decisions: List[str] = []

    # Outer savepoint so a failed attempt does not poison the caller session.
    try:
        with db.begin_nested():
            base = (
                db.query(OutlawBase)
                .filter(OutlawBase.id == base_id)
                .populate_existing()
                .with_for_update()
                .first()
            )
            if base is None:
                return RaidResult(success=False, message="OutlawBase not found")

            # Idempotent replay: same completion_id already applied.
            if base.last_raid_completion_id == completion_id:
                return RaidResult(
                    success=True,
                    message="raid already completed (idempotent)",
                    idempotent_replay=True,
                    completion_id=completion_id,
                )

            visibility = is_outlaw_base_visible(base, discovery_context)
            decisions.extend(visibility.decision_needed)
            if not visibility.eligible:
                return RaidResult(
                    success=False,
                    message="; ".join(visibility.reasons) or "base not eligible",
                    decision_needed=decisions,
                )

            if is_on_raid_cooldown(base, now=clock):
                return RaidResult(
                    success=False,
                    message="raid cooldown active",
                    decision_needed=decisions,
                )

            lock_holder = getattr(base, "combat_lock_held_by", None)
            if lock_holder is not None and lock_holder != attacker_id:
                return RaidResult(
                    success=False,
                    message="combat lock held by another player",
                    decision_needed=decisions,
                )
            base.combat_lock_held_by = attacker_id

            combat_outcome = resolver(db, base, attacker_id)
            if not combat_outcome.get("success"):
                base.combat_lock_held_by = None
                return RaidResult(
                    success=False,
                    message=str(
                        combat_outcome.get("message") or "combat resolution failed"
                    ),
                    decision_needed=decisions,
                )

            kia_ids = _kia_sleeping_residents(
                db, base=base, attacker_id=attacker_id, now=clock
            )

            loot_taken: Dict[str, int] = {}
            fraction = config.get(CONFIG_LOOT_SHARE_FRACTION)
            if fraction is None:
                decisions.append(DECISION_LOOT_SHARE)
            else:
                loot_taken = _apply_loot_share(base, float(fraction))

            influence_applied = False
            influence_delta = config.get(CONFIG_INFLUENCE_DELTA)
            hostile_faction_id = config.get(CONFIG_HOSTILE_FACTION_ID)
            if influence_delta is None or hostile_faction_id is None:
                decisions.append(DECISION_INFLUENCE)
            else:
                influence_applied = _apply_influence_reduction(
                    db,
                    base=base,
                    faction_id=uuid.UUID(str(hostile_faction_id)),
                    delta=float(influence_delta),
                )

            # Relocation placement is unspecified — mark pending only.
            base.relocation_pending = True
            decisions.append(DECISION_RELOCATION)

            base.last_raided_at = clock
            base.raid_cooldown_until = clock + RAID_COOLDOWN
            base.last_raid_completion_id = completion_id
            base.combat_lock_held_by = None

            audit_entry = {
                "completion_id": str(completion_id),
                "attacker_id": str(attacker_id),
                "at": clock.isoformat(),
                "kia_npc_ids": kia_ids,
                "loot_taken": loot_taken,
                "influence_applied": influence_applied,
                "raid_cooldown_until": base.raid_cooldown_until.isoformat(),
                "relocation_pending": True,
                "decision_needed": list(decisions),
            }
            _append_audit(base, audit_entry)
            db.flush()

            return RaidResult(
                success=True,
                message="raid completed",
                completion_id=completion_id,
                kia_npc_ids=kia_ids,
                loot_taken=loot_taken,
                influence_applied=influence_applied,
                decision_needed=decisions,
                audit_entry=audit_entry,
            )
    except Exception:
        logger.exception("complete_outlaw_base_raid failed for base %s", base_id)
        raise


def _apply_loot_share(base: OutlawBase, fraction: float) -> Dict[str, int]:
    if fraction < 0 or fraction > 1:
        raise ValueError("loot_share_fraction must be in [0, 1]")
    inventory = dict(base.loot_inventory or {})
    taken: Dict[str, int] = {}
    remaining: Dict[str, int] = {}
    for key, qty in inventory.items():
        try:
            q = int(qty)
        except (TypeError, ValueError):
            remaining[key] = qty
            continue
        steal = int(q * fraction)
        if steal > 0:
            taken[key] = steal
        left = q - steal
        if left > 0:
            remaining[key] = left
    base.loot_inventory = remaining
    flag_modified(base, "loot_inventory")
    return taken


def _apply_influence_reduction(
    db: Session,
    *,
    base: OutlawBase,
    faction_id: uuid.UUID,
    delta: float,
) -> bool:
    """Apply a negative regional-power delta through faction_service.

    ``delta`` should already be signed (canon: reduction ⇒ negative).
    Resolves host sector UUID from OutlawBase.sector_id (global int).
    """
    from src.services.faction_service import adjust_sector_influence

    sector = (
        db.query(Sector).filter(Sector.sector_id == base.sector_id).first()
    )
    if sector is None:
        logger.warning(
            "outlaw-base raid: no Sector row for sector_id=%s; influence skipped",
            base.sector_id,
        )
        return False
    # Canon: reduce faction power — if caller passed a positive number, negate.
    applied_delta = delta if delta <= 0 else -abs(delta)
    row = adjust_sector_influence(db, sector.id, faction_id, applied_delta)
    return row is not None


def _kia_sleeping_residents(
    db: Session,
    *,
    base: OutlawBase,
    attacker_id: uuid.UUID,
    now: datetime,
) -> List[str]:
    kia_ids: List[str] = []
    remaining: List[Any] = []
    for raw_id in list(base.assigned_npc_ids or []):
        try:
            npc_id = uuid.UUID(str(raw_id))
        except (ValueError, TypeError):
            remaining.append(raw_id)
            continue
        npc = db.query(NPCCharacter).filter(NPCCharacter.id == npc_id).first()
        if npc is None:
            continue
        activity = npc.current_activity
        is_sleep = activity == NPCActivity.SLEEP or (
            isinstance(activity, str) and str(activity).upper() == "SLEEP"
        )
        if not is_sleep:
            remaining.append(str(npc.id))
            continue
        if npc.status in (NPCStatus.KIA, NPCStatus.RESPAWNING):
            continue
        npc.status = NPCStatus.KIA
        npc.lifecycle_stage = NPCLifecycleStage.KIA
        npc.destroyed_at = now
        npc.current_activity = NPCActivity.SLEEP
        db.add(
            NPCDeathLog(
                npc_id=npc.id,
                killed_by_player_id=attacker_id,
                sector_id=base.sector_id,
                home_region_id=base.home_region_id,
                region_id_snapshot=base.home_region_id,
                destruction_cause="outlaw_base_raid",
                killed_at=now,
            )
        )
        kia_ids.append(str(npc.id))

    base.assigned_npc_ids = remaining
    base.current_occupants_count = len(remaining)
    flag_modified(base, "assigned_npc_ids")
    return kia_ids


def _append_audit(base: OutlawBase, entry: Mapping[str, Any]) -> None:
    log = list(base.raid_audit_log or [])
    log.append(dict(entry))
    if len(log) > MAX_AUDIT_ENTRIES:
        log = log[-MAX_AUDIT_ENTRIES:]
    base.raid_audit_log = log
    flag_modified(base, "raid_audit_log")
