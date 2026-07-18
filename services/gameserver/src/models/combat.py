"""Combat enums (WO-CL3).

This module is now ONLY the combat enums (CombatType / CombatResult) — imported by
combat_service + movement_service. The former model classes here were dead commented-out
duplicates and have been removed: CombatLog lives in combat_log.py; Drone / DroneDeployment /
DroneCombat live in drone.py. (The now-unused SQLAlchemy imports went with them.)
"""
import enum


class CombatType(enum.Enum):
    SHIP_VS_SHIP = "SHIP_VS_SHIP"
    SHIP_VS_PLANET = "SHIP_VS_PLANET"
    SHIP_VS_PORT = "SHIP_VS_PORT"
    SHIP_VS_DRONES = "SHIP_VS_DRONES"
    PLANET_DEFENSE = "PLANET_DEFENSE"
    PORT_DEFENSE = "PORT_DEFENSE"
    # Not a distinct CombatLog.combat_type (sector-drone engagements log as
    # SHIP_VS_DRONES) — tags the +5% sector-defence bonus applied to deployed
    # defender drones' return fire (combat.md#sector-drones). Referenced via
    # its .value as a combat_details entry tag in
    # CombatService._resolve_sector_drone_combat (WO-DRN-COMBAT-RECORD).
    SECTOR_DEFENSE = "SECTOR_DEFENSE"


class CombatResult(enum.Enum):
    ATTACKER_VICTORY = "ATTACKER_VICTORY"
    DEFENDER_VICTORY = "DEFENDER_VICTORY"
    DRAW = "DRAW"
    ATTACKER_FLED = "ATTACKER_FLED"
    DEFENDER_FLED = "DEFENDER_FLED"
    MUTUAL_DESTRUCTION = "MUTUAL_DESTRUCTION"
    ABANDONED = "ABANDONED"  # Combat was started but not completed
