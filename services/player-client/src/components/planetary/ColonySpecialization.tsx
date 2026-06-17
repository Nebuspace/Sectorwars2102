import React, { useState } from 'react';
import { gameAPI } from '../../services/api';
import type { Planet, ColonySpecialization as ColonySpecializationType } from '../../types/planetary';
import './colony-specialization.css';

interface ColonySpecializationProps {
  planet: Planet;
  onUpdate?: (planet: Planet) => void;
  onClose?: () => void;
}

interface SpecializationInfo {
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
const SPECIALIZATIONS: SpecializationInfo[] = [
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

export const ColonySpecialization: React.FC<ColonySpecializationProps> = ({ 
  planet, 
  onUpdate,
  onClose 
}) => {
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
      setError(err instanceof Error ? err.message : 'Failed to specialize colony');
    } finally {
      setChanging(false);
    }
  };

  return (
    <div className="colony-specialization">
      <div className="specialization-header">
        <h3>Colony Specialization - {planet.name}</h3>
        <button className="close-button" onClick={onClose}>✕</button>
      </div>

      <div className="specialization-content">
        {currentSpec && (
          <div className="current-specialization">
            <h4>Current Specialization</h4>
            <div className="current-spec-info">
              <span className="spec-icon">{currentSpec.icon}</span>
              <div className="spec-details">
                <h5>{currentSpec.name}</h5>
                <p>{currentSpec.description}</p>
              </div>
            </div>
          </div>
        )}

        {error && (
          <div className="error-message">
            <span className="error-icon">⚠️</span>
            {error}
          </div>
        )}

        {successMessage && (
          <div className="success-message">
            <span className="success-icon">✅</span>
            {successMessage}
          </div>
        )}

        <div className="planet-info">
          <div className="info-item">
            <span className="info-label">Planet Type:</span>
            <span className="info-value">{planet.planetType}</span>
          </div>
          <div className="info-item">
            <span className="info-label">Population:</span>
            <span className="info-value">{planet.colonists.toLocaleString()}</span>
          </div>
          <div className="info-item">
            <span className="info-label">Buildings:</span>
            <span className="info-value">
              {planet.buildings.map(b => `${b.type} L${b.level}`).join(', ')}
            </span>
          </div>
        </div>

        <div className="specializations-grid">
          <h4>Available Specializations</h4>
          {SPECIALIZATIONS.map(spec => {
            const requirements = meetsRequirements(spec);
            const isSelected = selectedSpec === spec.type;
            const isCurrent = planet.specialization === spec.type;

            return (
              <div
                key={spec.type}
                className={`specialization-card ${isSelected ? 'selected' : ''} ${!requirements.meets ? 'unavailable' : ''} ${isCurrent ? 'current' : ''}`}
                onClick={() => requirements.meets && !isCurrent && setSelectedSpec(spec.type)}
              >
                <div className="spec-header">
                  <span className="spec-icon">{spec.icon}</span>
                  <div className="spec-title">
                    <h5>{spec.name}</h5>
                    {isCurrent && <span className="current-badge">Current</span>}
                  </div>
                </div>

                <p className="spec-description">{spec.description}</p>

                <div className="spec-benefits">
                  <h6>Benefits:</h6>
                  <ul>
                    {spec.benefits.map((benefit, index) => (
                      <li key={index}>{benefit}</li>
                    ))}
                  </ul>
                </div>

                <div className="spec-bonuses">
                  {Object.entries(spec.productionBonuses).length > 0 && (
                    <div className="bonus-group">
                      <span className="bonus-label">Production:</span>
                      {Object.entries(spec.productionBonuses).map(([resource, bonus]) => (
                        <span key={resource} className={`bonus ${resource}`}>
                          {resource === 'fuel' && '⛽'}
                          {resource === 'organics' && '🌿'}
                          {resource === 'equipment' && '⚙️'}
                          +{bonus}%
                        </span>
                      ))}
                    </div>
                  )}
                  {spec.defenseBonuses && (
                    <div className="bonus-group">
                      <span className="bonus-label">Defense:</span>
                      <span className="bonus defense">🛡️ +{spec.defenseBonuses}%</span>
                    </div>
                  )}
                  {spec.researchBonuses && (
                    <div className="bonus-group">
                      <span className="bonus-label">Research:</span>
                      <span className="bonus research">🔬 +{spec.researchBonuses}%</span>
                    </div>
                  )}
                </div>

                <div className="spec-requirements">
                  <h6>Requirements:</h6>
                  {!requirements.meets ? (
                    <ul className="missing-requirements">
                      {requirements.missing.map((req, index) => (
                        <li key={index} className="missing">{req}</li>
                      ))}
                    </ul>
                  ) : (
                    <span className="requirements-met">✓ All requirements met</span>
                  )}
                </div>

                <div className="recommended-for">
                  <span className="recommended-label">Best for:</span>
                  <span className="recommended-value">{spec.recommendedFor.join(', ')}</span>
                </div>
              </div>
            );
          })}
        </div>

        {selectedSpec && selectedSpec !== planet.specialization && (
          <div className="specialization-summary">
            <h4>Specialization Summary</h4>
            <div className="summary-content">
              <p>
                Change specialization from <strong>{currentSpec?.name || 'None'}</strong> to{' '}
                <strong>{selectedSpecInfo?.name}</strong>?
              </p>
              <div className="warning-note">
                <span className="warning-icon">⚠️</span>
                <p>Changing specialization will reset production bonuses. The new bonuses will take effect immediately.</p>
              </div>
            </div>
          </div>
        )}

        <div className="action-buttons">
          <button
            className="button secondary"
            onClick={onClose}
            disabled={changing}
          >
            Cancel
          </button>
          {selectedSpec && selectedSpec !== planet.specialization && (
            <button
              className="button primary"
              onClick={handleSpecialize}
              disabled={changing || !meetsRequirements(selectedSpecInfo!).meets}
            >
              {changing ? 'Specializing...' : 'Specialize Colony'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};