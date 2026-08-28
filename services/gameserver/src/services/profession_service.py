"""
Colonist profession kernel (LEG-2253 / FEATURES/planets/professions.md).

Training durations and numeric bonus multipliers are canon-backed. Training
*purchase/charge* remains DECISION-NEEDED — queueing never deducts credits or
stockpile commodities until those TBD cells are ruled.
"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from src.core.game_time import scaled_deadline
from src.models.colonist_profession import (
    ColonistProfession,
    PROFESSION_TRAINING_DAYS,
    ProfessionType,
)
from src.models.planet import Planet, player_planets
from src.models.station import Station
from src.models.profession_training_queue import (
    ProfessionTrainingQueue,
    ProfessionTrainingStatus,
)

# Citadel L3+ (Colony phase) required to operate training (professions.md).
MIN_CITADEL_FOR_TRAINING = 3

# Numeric bonus multipliers from professions.md (non-TBD cells only).
# Mining Engineers ``fuel`` covers planetary fuel_ore; harvest ``ore`` is wired
# via ``mining_engineer_harvest_multiplier`` (mining.md:83).
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
TRADE_SPECIALIST_CREDIT_MULTIPLIER = 1.25  # professions.md L57
MINING_ENGINEER_HARVEST_MULTIPLIER = 1.30  # professions.md L34 / mining.md:83


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


def trade_specialist_credit_multiplier(db: Session, planet_id: UUID) -> float:
    counts = profession_counts(db, planet_id)
    if counts.get(ProfessionType.TRADE_SPECIALISTS, 0) > 0:
        return TRADE_SPECIALIST_CREDIT_MULTIPLIER
    return 1.0


def mining_engineer_multiplier(db: Session, planet_id: UUID) -> float:
    counts = profession_counts(db, planet_id)
    if counts.get(ProfessionType.MINING_ENGINEERS, 0) > 0:
        return MINING_ENGINEER_HARVEST_MULTIPLIER
    return 1.0


def mining_engineer_harvest_multiplier(
    db: Session, player_id: UUID, region_id: Optional[UUID]
) -> float:
    """Best planet-local Mining Engineers bonus in ``region_id`` (mining.md:83)."""
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
        best = max(best, mining_engineer_multiplier(db, planet.id))
    return best


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


class ProfessionService:
    def __init__(self, db: Session):
        self.db = db

    def _assert_owner(self, planet: Planet, player_id: UUID) -> None:
        if planet.owner_id != player_id:
            raise ValueError("not_owner")

    def _assert_training_gate(self, planet: Planet) -> None:
        if (planet.citadel_level or 0) < MIN_CITADEL_FOR_TRAINING:
            raise ValueError("citadel_level_too_low")

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
            "cost_blocked": True,
            "cost_block_reason": "DECISION-NEEDED: profession training costs/caps not yet ruled",
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
        self._assert_training_gate(planet)
        prof = _parse_profession(profession)
        self.advance_queue(planet, now=now)
        if (planet.colonists or 0) < trainee_count:
            raise ValueError("insufficient_generic_colonists")
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
            "cost_blocked": True,
            "cost_charged": False,
            "queue_id": str(row.id),
            "profession": prof.value,
            "trainee_count": trainee_count,
            "training_days": days,
            "completes_at": completes_at.isoformat(),
            "message": (
                "Training queued without charge — profession cost magnitudes remain "
                "DECISION-NEEDED (LEG-DEC-484 / D#501)."
            ),
        }
