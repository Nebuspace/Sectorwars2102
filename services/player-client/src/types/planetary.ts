// Planetary Management Types

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
}

export type PlanetType = 'terran' | 'oceanic' | 'mountainous' | 'desert' | 'frozen';

export interface ProductionRates {
  fuel: number;
  organics: number;
  equipment: number;
  colonists: number;
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

export interface SpecializationResponse {
  success: boolean;
  specialization: ColonySpecialization;
  bonuses: ColonySpecializationBonus;
}