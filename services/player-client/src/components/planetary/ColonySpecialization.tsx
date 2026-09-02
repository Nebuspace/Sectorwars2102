import React, { useState } from 'react';
import { gameAPI } from '../../services/api';
import type { Planet, ColonySpecialization as ColonySpecializationType } from '../../types/planetary';

export interface SpecializationInfo {
  type: ColonySpecializationType;
  name: string;
  icon: string;
  description: string;
  benefits: string[];
  productionBonuses: {
    fuel?: number;
    organics?: number;
    equipment?: number;
  };
  defenseBonuses?: number;
  researchBonuses?: number;
  requirements: {
    minColonists: number;
    minBuildings: { [key: string]: number };
  };
  recommendedFor: string[];
}

// Benefits below reflect what the gameserver ACTUALLY applies (ADR-0087): the
// production multipliers, the defense multiplier (combat damage-reduction +
// shield HP), and the research-point yield. Specialization is a TRADE-OFF, so
// penalties are shown, not hidden.
export const SPECIALIZATIONS: SpecializationInfo[] = [
  {
    type: 'agricultural',
    name: 'Agricultural Colony',
    icon: '🌾',
    description: 'Food-focused colony: trades industrial output for organics and population growth',
    benefits: [
      '+50% organics production',
      '+20% colonist growth',
      '−20% fuel output',
      '−20% equipment output'
    ],
    productionBonuses: {
      organics: 50
    },
    requirements: {
      minColonists: 10000,
      minBuildings: { farm: 2 }
    },
    recommendedFor: ['Oceanic planets', 'Terran planets', 'High population systems']
  },
  {
    type: 'industrial',
    name: 'Industrial Complex',
    icon: '🏭',
    description: 'Manufacturing hub: maximises equipment output at the cost of food and growth',
    benefits: [
      '+50% equipment production',
      '−10% fuel output',
      '−20% organics output',
      '−10% colonist growth'
    ],
    productionBonuses: {
      equipment: 50
    },
    requirements: {
      minColonists: 15000,
      minBuildings: { factory: 2, mine: 1 }
    },
    recommendedFor: ['Mountainous planets', 'Resource-rich sectors', 'Strategic locations']
  },
  {
    type: 'military',
    name: 'Military Outpost',
    icon: '⚔️',
    description: 'Fortified colony: hardened planetary defenses at the cost of production and growth',
    benefits: [
      '+50% planetary defense (damage reduction + shield HP in combat)',
      '+10% equipment production',
      '−10% fuel & organics output',
      '−20% colonist growth'
    ],
    productionBonuses: {
      equipment: 10
    },
    requirements: {
      minColonists: 20000,
      minBuildings: { defense: 3, factory: 1 }
    },
    recommendedFor: ['Border planets', 'Strategic chokepoints', 'Contested territories']
  },
  {
    type: 'research',
    name: 'Research Station',
    icon: '🔬',
    description: 'Scientific colony: maximises research-point output from its Research Labs',
    benefits: [
      '+50% research-point output from Research Labs (feeds upcoming tech systems)',
      '−20% fuel output',
      '−20% organics output',
      '−10% equipment output',
      '−10% colonist growth'
    ],
    productionBonuses: {},
    requirements: {
      minColonists: 25000,
      minBuildings: { research: 2 }
    },
    recommendedFor: ['Frozen planets', 'Anomaly-rich sectors', 'Peaceful regions']
  },
  {
    type: 'balanced',
    name: 'Balanced Colony',
    icon: '⚖️',
    description: 'Generalist colony: a modest all-round bonus instead of a single specialty',
    benefits: [
      '+10% to all production, defense, and research',
      'Lowest requirement (5,000 colonists)',
      'Re-specialize later as the colony grows'
    ],
    productionBonuses: { fuel: 10, organics: 10, equipment: 10 },
    requirements: {
      minColonists: 5000,
      minBuildings: {}
    },
    recommendedFor: ['New colonies', 'Terran planets', 'General purpose']
  }
];

/**
 * useColonySpecialization — shared selection + apply logic for colony
 * specialization. Live UI is SpecializationDrawer
 * (WO-RETIRE-COLONY-SPECIALIZATION-MODAL removed the unused modal shell).
 */

/** Transport collapse copy is not gameserver detail (network-collapse densify). */
const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed) ||
    /^networkerror$/i.test(trimmed)
  );
};

function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

/** Surface GS specialize PUT 400 detail (`detail=str(e)`), else stable fallback. */
export function formatColonySpecializeError(err: unknown): string {
  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  // Network collapse (fetch TypeError / axios transport) is not gameserver copy — use the fallback.
  const hasServerDetail =
    !(err instanceof TypeError) &&
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim()) &&
    !isNetworkCollapseMessage(message);

  if (status === 403) {
    if (hasServerDetail) return message!;
    return 'You do not have permission to specialize this colony.';
  }

  if (status === 429) {
    return 'Colony specialization rate limit exceeded — wait a moment and try again.';
  }

  if (hasServerDetail) return message!;
  return 'Failed to specialize colony';
}

export const useColonySpecialization = (
  planet: Planet,
  onUpdate?: (planet: Planet) => void,
  onClose?: () => void,
) => {
  const [selectedSpec, setSelectedSpec] = useState<ColonySpecializationType | null>(
    planet.specialization || null
  );
  const [changing, setChanging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const currentSpec = SPECIALIZATIONS.find(s => s.type === planet.specialization);
  const selectedSpecInfo = SPECIALIZATIONS.find(s => s.type === selectedSpec);

  // Check if planet meets requirements for a specialization
  const meetsRequirements = (spec: SpecializationInfo): { meets: boolean; missing: string[] } => {
    const missing: string[] = [];

    // Check colonist requirement
    if (planet.colonists < spec.requirements.minColonists) {
      missing.push(`${spec.requirements.minColonists.toLocaleString()} colonists (have ${planet.colonists.toLocaleString()})`);
    }

    // Check building requirements
    Object.entries(spec.requirements.minBuildings).forEach(([buildingType, minLevel]) => {
      const building = planet.buildings.find(b => b.type === buildingType);
      if (!building || building.level < minLevel) {
        const currentLevel = building?.level || 0;
        missing.push(`${buildingType} level ${minLevel} (have level ${currentLevel})`);
      }
    });

    return {
      meets: missing.length === 0,
      missing
    };
  };

  const handleSpecialize = async () => {
    if (!selectedSpec) {
      setError('Please select a specialization');
      return;
    }

    if (selectedSpec === planet.specialization) {
      setError('This colony is already specialized in this area');
      return;
    }

    const selectedInfo = SPECIALIZATIONS.find(s => s.type === selectedSpec)!;
    const requirements = meetsRequirements(selectedInfo);

    if (!requirements.meets) {
      setError(`Missing requirements: ${requirements.missing.join(', ')}`);
      return;
    }

    try {
      setChanging(true);
      setError(null);
      setSuccessMessage(null);

      const response = await gameAPI.planetary.specializePlanet(planet.id, selectedSpec);

      if (response.success) {
        setSuccessMessage(`Colony specialized as ${selectedInfo.name}!`);

        // Update parent component
        if (onUpdate) {
          const updatedPlanet = {
            ...planet,
            specialization: selectedSpec
          };
          onUpdate(updatedPlanet);
        }

        // Close after success
        setTimeout(() => {
          if (onClose) onClose();
        }, 2000);
      }
    } catch (err) {
      setError(formatColonySpecializeError(err));
    } finally {
      setChanging(false);
    }
  };

  return {
    selectedSpec,
    setSelectedSpec,
    changing,
    error,
    successMessage,
    currentSpec,
    selectedSpecInfo,
    meetsRequirements,
    handleSpecialize,
  };
};
