"""
Colonist profession kernel (LEG-2253 / FEATURES/planets/professions.md).

Training durations and numeric bonus multipliers are canon-backed. Training
costs use ADR-0093 item 35 provisional per-100 magnitudes (LEG-DEC-804).
"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from src.core.game_time import scaled_deadline
from src.models.colonist_profession import (
    ColonistProfession,
    PROFESSION_TRAINING_COST_PER_100,
    PROFESSION_TRAINING_DAYS,
    ProfessionType,
    TrainingCostPer100,
)
from src.models.planet import Planet, player_planets
from src.models.player import Player
from src.models.station import Station
from src.models.profession_training_queue import (
    ProfessionTrainingQueue,
    ProfessionTrainingStatus,
)

# Citadel L3+ (Colony phase) required to operate training (professions.md).
MIN_CITADEL_FOR_TRAINING = 3
# Research Scientists require Research Lab L3 (Planet.research_level; professions.md L40).
MIN_RESEARCH_LAB_FOR_RESEARCH_SCIENTISTS = 3
MIN_ORBITAL_SHIPYARD_FOR_SPACE_ENGINEERS = 2  # professions.md Space Engineers
MIN_MILITARY_ACADEMY_FOR_COMBAT_PILOTS = 2  # professions.md Combat Pilots
MIN_TERRAFORMING_LAB_FOR_TERRAFORM_ENGINEERS = 3  # professions.md Terraform Engineers

# Numeric bonus multipliers from professions.md (non-TBD cells only).
PRODUCTION_BONUS: dict[ProfessionType, dict[str, float]] = {
    ProfessionType.MINING_ENGINEERS: {"fuel": 1.30},
    ProfessionType.INDUSTRIAL_MANAGERS: {"equipment": 1.35},
    ProfessionType.AGRICULTURAL_SCIENTISTS: {"organics": 1.35, "colonists": 1.15},
    ProfessionType.MEDICAL_PROFESSIONALS: {"colonists": 1.20},
}

RESEARCH_SCIENTIST_MULTIPLIER = 1.40
STRUCTURAL_ENGINEER_COST_MULTIPLIER = 0.80  # −20% building upgrade costs
COMBAT_PILOT_DRONE_MULTIPLIER = 1.50
DEFENSE_COORDINATOR_MULTIPLIER = 1.30
TERRAFORM_ENGINEER_RATE_PER_1K = 0.5  # habitability / month per 1k engineers
TERRAFORM_ENGINEER_MONTHLY_CAP = 5.0  # at 10k engineers
SPACE_ENGINEER_REPAIR_MULTIPLIER = 1.25  # professions.md L32
# tradedock-shipyard.md §Space Engineer profession integration — up to three
# engineers per active construction project (per-project assignment API is a
# follow-on; this cap applies to the interim pool-wide count wire).
MAX_CONSTRUCTION_ENGINEERS_PER_PROJECT = 3
TRADE_SPECIALIST_CREDIT_MULTIPLIER = 1.25  # professions.md L57
MINING_ENGINEER_ORE_MULTIPLIER = 1.30  # professions.md L34; mining.md step 5 (ore)


def _parse_profession(value: str) -> ProfessionType:
    try:
        return ProfessionType(value)
    except ValueError as exc:
        raise ValueError(f"unknown_profession:{value}") from exc


def profession_counts(db: Session, planet_id: UUID) -> Dict[ProfessionType, int]:
    rows = (
        db.query(ColonistProfession)
        .filter(ColonistProfession.planet_id == planet_id)
        .all()
    )
    out: Dict[ProfessionType, int] = {}
    for row in rows:
        try:
            prof = ProfessionType(row.profession)
        except ValueError:
            continue
        out[prof] = int(row.count or 0)
    return out


def production_multipliers(counts: Dict[ProfessionType, int]) -> Dict[str, float]:
    """Stack multiplicative production/growth bonuses for planetary_service."""
    fuel = 1.0
    organics = 1.0
    equipment = 1.0
    colonists = 1.0
    for prof, bonus_map in PRODUCTION_BONUS.items():
        if counts.get(prof, 0) <= 0:
            continue
        fuel *= bonus_map.get("fuel", 1.0)
        organics *= bonus_map.get("organics", 1.0)
        equipment *= bonus_map.get("equipment", 1.0)
        colonists *= bonus_map.get("colonists", 1.0)
    return {
        "fuel": fuel,
        "organics": organics,
        "equipment": equipment,
        "colonists": colonists,
    }


def research_multiplier(counts: Dict[ProfessionType, int]) -> float:
    if counts.get(ProfessionType.RESEARCH_SCIENTISTS, 0) > 0:
        return RESEARCH_SCIENTIST_MULTIPLIER
    return 1.0


def structural_engineer_cost_multiplier(db: Session, planet_id: UUID) -> float:
    counts = profession_counts(db, planet_id)
    if counts.get(ProfessionType.STRUCTURAL_ENGINEERS, 0) > 0:
        return STRUCTURAL_ENGINEER_COST_MULTIPLIER
    return 1.0


def combat_pilot_drone_multiplier(db: Session, planet_id: UUID) -> float:
    counts = profession_counts(db, planet_id)
    if counts.get(ProfessionType.COMBAT_PILOTS, 0) > 0:
        return COMBAT_PILOT_DRONE_MULTIPLIER
    return 1.0


def defense_coordinator_multiplier(db: Session, planet_id: UUID) -> float:
    counts = profession_counts(db, planet_id)
    if counts.get(ProfessionType.DEFENSE_COORDINATORS, 0) > 0:
        return DEFENSE_COORDINATOR_MULTIPLIER
    return 1.0


def space_engineer_repair_multiplier(db: Session, planet_id: UUID) -> float:
    counts = profession_counts(db, planet_id)
    if counts.get(ProfessionType.SPACE_ENGINEERS, 0) > 0:
        return SPACE_ENGINEER_REPAIR_MULTIPLIER
    return 1.0


def construction_engineer_count(db: Session, player_id: UUID) -> int:
    """Legacy pool-wide Space Engineer count (LEG-302 interim wire).

    Superseded for construction-event RNG by
    :func:`construction_service.assigned_construction_engineer_count`, which
    reads per-project assignments (LEG-3599). Retained for callers that still
    need the pool-wide interim sum.
    """
    planet_ids = (
        db.query(player_planets.c.planet_id)
        .filter(player_planets.c.player_id == player_id)
        .all()
    )
    total = 0
    for (planet_id,) in planet_ids:
        total += profession_counts(db, planet_id).get(ProfessionType.SPACE_ENGINEERS, 0)
    return min(total, MAX_CONSTRUCTION_ENGINEERS_PER_PROJECT)


def space_engineers_on_planet(db: Session, planet_id: UUID) -> int:
    """Count of Space Engineers stationed on a planet."""
    return profession_counts(db, planet_id).get(ProfessionType.SPACE_ENGINEERS, 0)


def trade_specialist_credit_multiplier(db: Session, planet_id: UUID) -> float:
    counts = profession_counts(db, planet_id)
    if counts.get(ProfessionType.TRADE_SPECIALISTS, 0) > 0:
        return TRADE_SPECIALIST_CREDIT_MULTIPLIER
    return 1.0


def mining_engineer_ore_multiplier(db: Session, planet_id: UUID) -> float:
    counts = profession_counts(db, planet_id)
    if counts.get(ProfessionType.MINING_ENGINEERS, 0) > 0:
        return MINING_ENGINEER_ORE_MULTIPLIER
    return 1.0


def _max_profession_multiplier_for_station(
    db: Session,
    player_id: UUID,
    station: Optional[Station],
    *,
    planet_multiplier,
) -> float:
    """Best planet-local bonus among player-owned worlds in the station's sector."""
    if station is None or station.sector_id is None:
        return 1.0
    planets = (
        db.query(Planet)
        .join(player_planets, Planet.id == player_planets.c.planet_id)
        .filter(
            player_planets.c.player_id == player_id,
            Planet.sector_id == station.sector_id,
        )
        .all()
    )
    best = 1.0
    for planet in planets:
        best = max(best, planet_multiplier(db, planet.id))
    return best


def space_engineer_repair_multiplier_for_station(
    db: Session, player_id: UUID, station: Optional[Station],
) -> float:
    return _max_profession_multiplier_for_station(
        db, player_id, station, planet_multiplier=space_engineer_repair_multiplier,
    )


def trade_specialist_credit_multiplier_for_station(
    db: Session, player_id: UUID, station: Optional[Station],
) -> float:
    return _max_profession_multiplier_for_station(
        db, player_id, station, planet_multiplier=trade_specialist_credit_multiplier,
    )


def _max_profession_multiplier_for_region(
    db: Session,
    player_id: UUID,
    region_id: Optional[UUID],
    *,
    planet_multiplier,
) -> float:
    """Best planet-local bonus among player-owned worlds in ``region_id``."""
    if region_id is None:
        return 1.0
    planets = (
        db.query(Planet)
        .join(player_planets, Planet.id == player_planets.c.planet_id)
        .filter(
            player_planets.c.player_id == player_id,
            Planet.region_id == region_id,
        )
        .all()
    )
    best = 1.0
    for planet in planets:
        best = max(best, planet_multiplier(db, planet.id))
    return best


def mining_engineer_ore_multiplier_for_region(
    db: Session, player_id: UUID, region_id: Optional[UUID],
) -> float:
    return _max_profession_multiplier_for_region(
        db,
        player_id,
        region_id,
        planet_multiplier=mining_engineer_ore_multiplier,
    )


def terraform_engineer_monthly_habitability(engineer_count: int) -> float:
    if engineer_count <= 0:
        return 0.0
    rate = (engineer_count / 1000.0) * TERRAFORM_ENGINEER_RATE_PER_1K
    return min(TERRAFORM_ENGINEER_MONTHLY_CAP, rate)


def terraform_engineer_bonus_for_tick(engineer_count: int, tick_period_hours: float) -> float:
    """Convert monthly engineer habitability rate into points for one terraform tick."""
    monthly = terraform_engineer_monthly_habitability(engineer_count)
    if monthly <= 0 or tick_period_hours <= 0:
        return 0.0
    hours_per_month = 24.0 * 30.0
    return monthly * (tick_period_hours / hours_per_month)


def training_cost_for(prof: ProfessionType, trainee_count: int) -> TrainingCostPer100:
    """Scale provisional per-100 recipe to ``trainee_count`` colonists."""
    return PROFESSION_TRAINING_COST_PER_100[prof].scale(trainee_count)


def training_costs_per_100_payload() -> Dict[str, Dict[str, int]]:
    return {
        prof.value: PROFESSION_TRAINING_COST_PER_100[prof].as_dict()
        for prof in ProfessionType
    }


class ProfessionService:
    def __init__(self, db: Session):
        self.db = db

    def _assert_owner(self, planet: Planet, player_id: UUID) -> None:
        if planet.owner_id != player_id:
            raise ValueError("not_owner")

    def _assert_training_gate(self, planet: Planet) -> None:
        if (planet.citadel_level or 0) < MIN_CITADEL_FOR_TRAINING:
            raise ValueError("citadel_level_too_low")

    def _building_kind_level(self, planet: Planet, kind: str) -> int:
        """Max operational level of ``kind`` on planet.structures (D#594 — no proxy invent)."""
        from src.services.structures import max_kind_level

        return max_kind_level(getattr(planet, "structures", None) or {}, kind)

    def _assert_profession_training_gate(self, planet: Planet, prof: ProfessionType) -> None:
        self._assert_training_gate(planet)
        if prof == ProfessionType.RESEARCH_SCIENTISTS:
            if (planet.research_level or 0) < MIN_RESEARCH_LAB_FOR_RESEARCH_SCIENTISTS:
                raise ValueError("research_lab_level_too_low")
        elif prof == ProfessionType.SPACE_ENGINEERS:
            if self._building_kind_level(planet, "ORBITAL_SHIPYARD") < MIN_ORBITAL_SHIPYARD_FOR_SPACE_ENGINEERS:
                raise ValueError("orbital_shipyard_level_too_low")
        elif prof == ProfessionType.COMBAT_PILOTS:
            if self._building_kind_level(planet, "MILITARY_ACADEMY") < MIN_MILITARY_ACADEMY_FOR_COMBAT_PILOTS:
                raise ValueError("military_academy_level_too_low")
        elif prof == ProfessionType.TERRAFORM_ENGINEERS:
            if self._building_kind_level(planet, "TERRAFORMING_LAB") < MIN_TERRAFORMING_LAB_FOR_TERRAFORM_ENGINEERS:
                raise ValueError("terraforming_lab_level_too_low")

    def training_eligibility(self, planet: Planet) -> Dict[str, bool]:
        """Per-profession eligibility for GET /planets/{id}/professions (building gates)."""
        citadel_ok = (planet.citadel_level or 0) >= MIN_CITADEL_FOR_TRAINING
        research_lab_ok = (
            (planet.research_level or 0) >= MIN_RESEARCH_LAB_FOR_RESEARCH_SCIENTISTS
        )
        shipyard_ok = (
            self._building_kind_level(planet, "ORBITAL_SHIPYARD")
            >= MIN_ORBITAL_SHIPYARD_FOR_SPACE_ENGINEERS
        )
        academy_ok = (
            self._building_kind_level(planet, "MILITARY_ACADEMY")
            >= MIN_MILITARY_ACADEMY_FOR_COMBAT_PILOTS
        )
        terra_lab_ok = (
            self._building_kind_level(planet, "TERRAFORMING_LAB")
            >= MIN_TERRAFORMING_LAB_FOR_TERRAFORM_ENGINEERS
        )
        out: Dict[str, bool] = {}
        for prof in ProfessionType:
            if prof == ProfessionType.RESEARCH_SCIENTISTS:
                out[prof.value] = citadel_ok and research_lab_ok
            elif prof == ProfessionType.SPACE_ENGINEERS:
                out[prof.value] = citadel_ok and shipyard_ok
            elif prof == ProfessionType.COMBAT_PILOTS:
                out[prof.value] = citadel_ok and academy_ok
            elif prof == ProfessionType.TERRAFORM_ENGINEERS:
                out[prof.value] = citadel_ok and terra_lab_ok
            else:
                out[prof.value] = citadel_ok
        return out

    def _load_player_for_update(self, player_id: UUID) -> Player:
        player = (
            self.db.query(Player)
            .filter(Player.id == player_id)
            .with_for_update()
            .first()
        )
        if player is None:
            raise ValueError("player_not_found")
        return player

    def _apply_training_charge(
        self,
        planet: Planet,
        player: Player,
        cost: TrainingCostPer100,
    ) -> None:
        credits = int(player.credits or 0)
        if credits < cost.credits:
            raise ValueError("insufficient_credits")
        equipment = int(planet.equipment or 0)
        if equipment < cost.equipment:
            raise ValueError("insufficient_equipment")
        organics = int(planet.organics or 0)
        if organics < cost.organics:
            raise ValueError("insufficient_organics")
        player.credits = credits - cost.credits
        planet.equipment = equipment - cost.equipment
        planet.organics = organics - cost.organics

    def advance_queue(self, planet: Planet, *, now: Optional[datetime] = None) -> bool:
        """Lazy-complete due training rows. Returns True if planet state changed."""
        now = now or datetime.now(UTC)
        changed = False
        pending = (
            self.db.query(ProfessionTrainingQueue)
            .filter(
                ProfessionTrainingQueue.planet_id == planet.id,
                ProfessionTrainingQueue.status == ProfessionTrainingStatus.QUEUED.value,
                ProfessionTrainingQueue.completes_at <= now,
            )
            .order_by(ProfessionTrainingQueue.completes_at)
            .all()
        )
        for row in pending:
            prof = _parse_profession(row.profession)
            available = planet.colonists or 0
            trainees = min(row.trainee_count, available)
            if trainees <= 0:
                row.status = ProfessionTrainingStatus.CANCELLED.value
                changed = True
                continue
            planet.colonists = available - trainees
            agg = (
                self.db.query(ColonistProfession)
                .filter(
                    ColonistProfession.planet_id == planet.id,
                    ColonistProfession.profession == prof.value,
                )
                .first()
            )
            if agg is None:
                agg = ColonistProfession(
                    planet_id=planet.id,
                    profession=prof.value,
                    count=0,
                )
                self.db.add(agg)
            agg.count = (agg.count or 0) + trainees
            row.status = ProfessionTrainingStatus.COMPLETED.value
            changed = True
        return changed

    def get_state(self, planet: Planet, player_id: UUID) -> Dict[str, Any]:
        self._assert_owner(planet, player_id)
        self.advance_queue(planet)
        counts = profession_counts(self.db, planet.id)
        queue_rows = (
            self.db.query(ProfessionTrainingQueue)
            .filter(
                ProfessionTrainingQueue.planet_id == planet.id,
                ProfessionTrainingQueue.status == ProfessionTrainingStatus.QUEUED.value,
            )
            .order_by(ProfessionTrainingQueue.queued_at)
            .all()
        )
        return {
            "planet_id": str(planet.id),
            "generic_colonists": planet.colonists or 0,
            "cost_blocked": False,
            "cost_basis": "provisional_per_100",
            "cost_basis_ref": "ADR-0093 item 35 / LEG-DEC-804",
            "training_costs_per_100": training_costs_per_100_payload(),
            "professions": {
                prof.value: counts.get(prof, 0) for prof in ProfessionType
            },
            "training_queue": [
                {
                    "id": str(row.id),
                    "profession": row.profession,
                    "trainee_count": row.trainee_count,
                    "queued_at": row.queued_at.isoformat() if row.queued_at else None,
                    "completes_at": row.completes_at.isoformat() if row.completes_at else None,
                    "status": row.status,
                    "training_days": PROFESSION_TRAINING_DAYS.get(
                        _parse_profession(row.profession), None
                    ),
                }
                for row in queue_rows
            ],
            "training_durations_days": {
                prof.value: days for prof, days in PROFESSION_TRAINING_DAYS.items()
            },
            "training_eligibility": self.training_eligibility(planet),
        }

    def queue_training(
        self,
        planet: Planet,
        player_id: UUID,
        profession: str,
        trainee_count: int,
        *,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        if trainee_count <= 0:
            raise ValueError("invalid_trainee_count")
        self._assert_owner(planet, player_id)
        prof = _parse_profession(profession)
        self._assert_profession_training_gate(planet, prof)
        self.advance_queue(planet, now=now)
        if (planet.colonists or 0) < trainee_count:
            raise ValueError("insufficient_generic_colonists")
        player = self._load_player_for_update(player_id)
        cost = training_cost_for(prof, trainee_count)
        self._apply_training_charge(planet, player, cost)
        now = now or datetime.now(UTC)
        days = PROFESSION_TRAINING_DAYS[prof]
        completes_at = scaled_deadline(days * 24.0, start=now)
        row = ProfessionTrainingQueue(
            planet_id=planet.id,
            owner_player_id=player_id,
            profession=prof.value,
            trainee_count=trainee_count,
            queued_at=now,
            completes_at=completes_at,
            status=ProfessionTrainingStatus.QUEUED.value,
        )
        self.db.add(row)
        self.db.flush()
        return {
            "success": True,
            "cost_blocked": False,
            "cost_charged": True,
            "cost": cost.as_dict(),
            "credits_remaining": player.credits,
            "planet_stockpile": {
                "equipment": planet.equipment or 0,
                "organics": planet.organics or 0,
            },
            "queue_id": str(row.id),
            "profession": prof.value,
            "trainee_count": trainee_count,
            "training_days": days,
            "completes_at": completes_at.isoformat(),
            "message": "Training queued; provisional per-100 costs charged on queue.",
        }
