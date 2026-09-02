// Planetary Management Types

/** Server-stamped when the production tick wasted output at the storage cap. */
export interface OverflowWarning {
  resources: Partial<Record<'fuel' | 'organics' | 'equipment', number>>;
  cap?: number;
  at?: string;
}

/** Server-stamped when colonist food consumption exceeded organics on hand. */
export interface StarvationWarning {
  food_deficit: number;
  colonists_lost: number;
  at: string;
}

export interface PlanetHabitability {
  score?: number;
  effectiveMaxColonists?: number;
  growthMultiplier?: number;
  moraleBonus?: number;
}

/** Present on owned-planet DTO when terraforming_active is true; otherwise null/absent. */
export interface PlanetTerraforming {
  active?: boolean;
  /** Target habitability score for the active project. */
  target?: number;
  /** 0–100 progress toward the target habitability. */
  progress?: number;
  startedAt?: string | null;
}

export interface Planet {
  id: string;
  name: string;
  sectorId: string;
  sectorName: string;
  planetType: PlanetType;
  colonists: number;
  maxColonists: number;
  productionRates: ProductionRates;
  allocations: ResourceAllocations;
  buildings: Building[];
  defenses: PlanetDefenses;
  underSiege: boolean;
  siegeDetails?: SiegeDetails;
  specialization?: ColonySpecialization;
  // Genesis formation state (forming planets are still terraforming).
  formationStatus?: string | null;
  formationStartedAt?: string | null;
  formationCompleteAt?: string | null;
  /** Owned-planets / detail DTO — habitability score bundle. */
  habitability?: PlanetHabitability;
  /** Active terraforming project telemetry; null when idle. */
  terraforming?: PlanetTerraforming | null;
  /** Last tick storage overflow (cleared when healthy). */
  overflowWarning?: OverflowWarning | null;
  /** Last tick food-deficit starvation event (cleared when healthy). */
  starvationWarning?: StarvationWarning | null;
}

// Canonical 12-value set (matches gameserver's PlanetType enum and the vista
// engine contract). Single source of truth lives in src/vista/contract.ts.
import type { PlanetType as _PlanetType } from '../vista/contract';
export type PlanetType = _PlanetType;

export interface ProductionRates {
  fuel: number;
  organics: number;
  equipment: number;
  colonists: number;
  research: number;
}

export interface ResourceAllocations {
  fuel: number;
  organics: number;
  equipment: number;
  unused: number;
}

export interface Building {
  type: BuildingType;
  level: number;
  upgrading: boolean;
  completionTime?: string;
  // Server-authoritative cost for the NEXT level (current -> current+1),
  // computed server-side via the exact fn the upgrade commit path charges
  // (WO-API-PHASE1 B4). Absent once the building is already at the
  // server's level cap -- there is no next level to price.
  nextUpgradeCost?: {
    credits: number;
    resources: {
      equipment: number;
    };
  };
}

export type BuildingType = 'factory' | 'farm' | 'mine' | 'defense' | 'research';

export interface PlanetDefenses {
  turrets: number;
  shields: number;
  drones: number;
}

export interface SiegeDetails {
  attackerId: string;
  attackerName: string;
  phase: SiegePhase;
  startTime: string;
  estimatedPhaseCompletion?: string;
  defenseEffectiveness?: number;
  casualties?: {
    colonists: number;
    drones: number;
  };
}

export type SiegePhase = 'orbital' | 'bombardment' | 'invasion';

export type ColonySpecialization = 'agricultural' | 'industrial' | 'military' | 'research' | 'balanced';

export interface ColonySpecializationBonus {
  production: Partial<ProductionRates>;
  defense: number;
  research: number;
}

export interface GenesisDeployment {
  sectorId: string;
  planetName: string;
  planetType: PlanetType;
}

export interface BuildingUpgrade {
  buildingType: BuildingType;
  targetLevel: number;
}

export interface DefenseConfiguration {
  turrets?: number;
  shields?: number;
  drones?: number;
}

// API Response Types
export interface PlanetsResponse {
  planets: Planet[];
  totalPlanets: number;
}

export interface AllocationResponse {
  success: boolean;
  allocations: ResourceAllocations;
  productionRates: ProductionRates;
}

export interface BuildingUpgradeResponse {
  success: boolean;
  buildingType: string;
  newLevel: number;
  completionTime: string;
  cost: {
    credits: number;
    resources: Partial<ProductionRates>;
  };
}

export interface DefenseUpdateResponse {
  success: boolean;
  defenses: PlanetDefenses;
  defensePower: number;
}

export interface GenesisDeploymentResponse {
  success: boolean;
  planetId: string;
  deploymentTime: number;
  genesisDevicesRemaining: number;
}

// Server-authoritative quote for a (tier, registration) pair (WO-API-B2).
// Sourced from the same cost function the deploy route charges from, so
// total_cost here is guaranteed to equal what a deploy of the same inputs
// would charge for the current player.
export interface GenesisQuoteResponse {
  tier: 'basic' | 'enhanced' | 'advanced';
  registration: 'clandestine' | 'registered' | 'chartered';
  device_cost: number;
  registration_fee: number;
  total_cost: number;
  player_credits: number;
  can_afford: boolean;
  reputation_gate: {
    required: number;
    current: number;
    met: boolean;
  };
}

export interface SpecializationResponse {
  success: boolean;
  specialization: ColonySpecialization;
  bonuses: ColonySpecializationBonus;
}