import React, { useState } from 'react';
import { useGame } from '../../contexts/GameContext';
import { gameAPI } from '../../services/api';
import type { Planet, PlanetDefenses } from '../../types/planetary';
import './defense-configuration.css';

interface DefenseConfigurationProps {
  planet: Planet;
  onUpdate?: (planet: Planet) => void;
  onClose?: () => void;
}

interface DefenseType {
  type: keyof PlanetDefenses;
  name: string;
  icon: string;
  description: string;
  effectiveness: string;
  cost: number;
  maxUnits: number;
}

const DEFENSE_TYPES: DefenseType[] = [
  {
    type: 'turrets',
    name: 'Laser Turrets',
    icon: '🔫',
    description: 'Automated defense turrets that target incoming attackers',
    effectiveness: 'Effective against drones and small ships',
    cost: 500,
    maxUnits: 1000
  },
  {
    type: 'shields',
    name: 'Shield Generators',
    icon: '🛡️',
    description: 'Energy shields that protect against bombardment',
    effectiveness: 'Reduces damage from orbital attacks',
    cost: 1000,
    maxUnits: 500
  },
  {
    type: 'drones',
    name: 'Defense Drones',
    icon: '✈️',
    description: 'Piloted drones that intercept enemy forces',
    effectiveness: 'Versatile defense against all threat types',
    cost: 2000,
    maxUnits: 250
  }
];

export const DefenseConfiguration: React.FC<DefenseConfigurationProps> = ({
  planet,
  onUpdate,
  onClose
}) => {
  const { playerState } = useGame();
  const [defenses, setDefenses] = useState<PlanetDefenses>(planet.defenses);
  const [tempDefenses, setTempDefenses] = useState<PlanetDefenses>(planet.defenses);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // Calculate defense power score
  const calculateDefensePower = (def: PlanetDefenses): number => {
    return def.turrets * 1 + def.shields * 2 + def.drones * 3;
  };

  // Calculate defense effectiveness rating
  const getDefenseRating = (power: number): { rating: string; class: string } => {
    if (power >= 2000) return { rating: 'Impregnable', class: 'excellent' };
    if (power >= 1500) return { rating: 'Fortified', class: 'good' };
    if (power >= 1000) return { rating: 'Protected', class: 'fair' };
    if (power >= 500) return { rating: 'Defended', class: 'poor' };
    return { rating: 'Vulnerable', class: 'critical' };
  };

  const defensePower = calculateDefensePower(tempDefenses);
  const defenseRating = getDefenseRating(defensePower);

  // Calculate total cost
  const calculateTotalCost = (): number => {
    const currentCost = DEFENSE_TYPES.reduce((total, type) => {
      const current = defenses[type.type];
      const target = tempDefenses[type.type];
      const diff = Math.abs(target - current);
      return total + (diff * type.cost);
    }, 0);
    return currentCost;
  };

  const totalCost = calculateTotalCost();

  // Check if player can afford using real credits from game context
  const playerCredits = playerState?.credits ?? 0;
  const canAfford = playerCredits >= totalCost;

  const handleSliderChange = (type: keyof PlanetDefenses, value: number) => {
    setTempDefenses({
      ...tempDefenses,
      [type]: value
    });
  };

  const handlePreset = (preset: 'balanced' | 'turret' | 'shield' | 'drone' | 'max') => {
    const presets = {
      balanced: { turrets: 200, shields: 100, drones: 50 },
      turret: { turrets: 500, shields: 50, drones: 25 },
      shield: { turrets: 100, shields: 300, drones: 25 },
      drone: { turrets: 100, shields: 50, drones: 100 },
      max: { 
        turrets: Math.min(1000, defenses.turrets + 200),
        shields: Math.min(500, defenses.shields + 100),
        drones: Math.min(250, defenses.drones + 50)
      }
    };
    
    setTempDefenses(presets[preset]);
  };

  const handleSave = async () => {
    if (!canAfford) {
      setError(`Insufficient credits. You need ${totalCost.toLocaleString()} credits.`);
      return;
    }

    try {
      setSaving(true);
      setError(null);
      setSuccessMessage(null);

      const response = await gameAPI.planetary.updateDefenses(planet.id, tempDefenses);
      
      if (response.success) {
        setDefenses(response.defenses);
        setSuccessMessage('Planetary defenses updated successfully!');
        
        // Update parent component
        if (onUpdate) {
          const updatedPlanet = {
            ...planet,
            defenses: response.defenses
          };
          onUpdate(updatedPlanet);
        }
        
        // Clear success message after 3 seconds
        setTimeout(() => setSuccessMessage(null), 3000);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update defenses');
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    setTempDefenses(defenses);
    setError(null);
    setSuccessMessage(null);
  };

  const hasChanges = JSON.stringify(tempDefenses) !== JSON.stringify(defenses);

  return (
    <div className="defense-configuration">
      <div className="config-header">
        <h3>Defense Configuration - {planet.name}</h3>
        <button className="close-button" onClick={onClose}>✕</button>
      </div>

      <div className="config-content">
        <div className="defense-overview">
          <div className="overview-stats">
            <div className="stat-item">
              <span className="stat-label">Defense Power:</span>
              <span className="stat-value">{defensePower}</span>
            </div>
            <div className="stat-item">
              <span className="stat-label">Rating:</span>
              <span className={`stat-value rating-${defenseRating.class}`}>
                {defenseRating.rating}
              </span>
            </div>
            <div className="stat-item">
              <span className="stat-label">Configuration Cost:</span>
              <span className={`stat-value ${!canAfford ? 'insufficient' : ''}`}>
                💰 {totalCost.toLocaleString()}
              </span>
            </div>
          </div>

          {planet.underSiege && (
            <div className="siege-warning">
              <span className="warning-icon">🚨</span>
              <span>Warning: Planet is under siege! Defenses are actively engaged.</span>
            </div>
          )}
        </div>

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

        <div className="preset-configs">
          <h4>Quick Configurations</h4>
          <div className="preset-buttons">
            <button 
              className="preset-button"
              onClick={() => handlePreset('balanced')}
              title="Balanced defense against all threats"
            >
              ⚖️ Balanced
            </button>
            <button 
              className="preset-button turret"
              onClick={() => handlePreset('turret')}
              title="Focus on automated turret defense"
            >
              🔫 Turret Focus
            </button>
            <button 
              className="preset-button shield"
              onClick={() => handlePreset('shield')}
              title="Maximize shield protection"
            >
              🛡️ Shield Focus
            </button>
            <button 
              className="preset-button drone"
              onClick={() => handlePreset('drone')}
              title="Emphasize drone squadrons"
            >
              ✈️ Drone Focus
            </button>
            <button 
              className="preset-button max"
              onClick={() => handlePreset('max')}
              title="Upgrade all defenses"
            >
              📈 Upgrade All
            </button>
          </div>
        </div>

        <div className="defense-controls">
          {DEFENSE_TYPES.map(defenseType => {
            const currentValue = tempDefenses[defenseType.type];
            const originalValue = defenses[defenseType.type];
            const diff = currentValue - originalValue;
            const cost = Math.abs(diff) * defenseType.cost;

            return (
              <div key={defenseType.type} className="defense-control">
                <div className="control-header">
                  <div className="defense-info">
                    <span className="defense-icon">{defenseType.icon}</span>
                    <div className="defense-details">
                      <h5>{defenseType.name}</h5>
                      <p className="defense-description">{defenseType.description}</p>
                      <p className="defense-effectiveness">{defenseType.effectiveness}</p>
                    </div>
                  </div>
                  <div className="defense-stats">
                    <span className="current-value">{currentValue} units</span>
                    {diff !== 0 && (
                      <span className={`value-diff ${diff > 0 ? 'positive' : 'negative'}`}>
                        {diff > 0 ? '+' : ''}{diff}
                      </span>
                    )}
                  </div>
                </div>

                <div className="slider-container">
                  <input
                    type="range"
                    min="0"
                    max={defenseType.maxUnits}
                    value={currentValue}
                    onChange={(e) => handleSliderChange(defenseType.type, parseInt(e.target.value))}
                    className="defense-slider"
                    style={{
                      background: `linear-gradient(to right, var(--accent-primary) 0%, var(--accent-primary) ${(currentValue / defenseType.maxUnits) * 100}%, var(--surface-secondary) ${(currentValue / defenseType.maxUnits) * 100}%, var(--surface-secondary) 100%)`
                    }}
                  />
                  <div className="slider-labels">
                    <span>0</span>
                    <span>{defenseType.maxUnits}</span>
                  </div>
                </div>

                {diff !== 0 && (
                  <div className="change-cost">
                    <span className="cost-label">Change cost:</span>
                    <span className="cost-value">💰 {cost.toLocaleString()}</span>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <div className="defense-summary">
          <h4>Defense Analysis</h4>
          <div className="analysis-grid">
            <div className="analysis-item">
              <span className="analysis-label">vs Small Ships:</span>
              <div className="effectiveness-bar">
                <div 
                  className="effectiveness-fill"
                  style={{ width: `${Math.min(100, (tempDefenses.turrets / 5))}%` }}
                />
              </div>
            </div>
            <div className="analysis-item">
              <span className="analysis-label">vs Bombardment:</span>
              <div className="effectiveness-bar">
                <div 
                  className="effectiveness-fill"
                  style={{ width: `${Math.min(100, (tempDefenses.shields / 3))}%` }}
                />
              </div>
            </div>
            <div className="analysis-item">
              <span className="analysis-label">vs Invasion:</span>
              <div className="effectiveness-bar">
                <div 
                  className="effectiveness-fill"
                  style={{ width: `${Math.min(100, (tempDefenses.drones / 1.5))}%` }}
                />
              </div>
            </div>
          </div>
        </div>

        <div className="action-buttons">
          <button
            className="button secondary"
            onClick={handleReset}
            disabled={!hasChanges || saving}
            title={saving ? 'Update in progress' : !hasChanges ? 'Nothing to reset' : 'Discard unsaved changes'}
          >
            Reset
          </button>
          <button
            className="button primary"
            onClick={handleSave}
            disabled={!hasChanges || saving || !canAfford}
            title={
              saving
                ? 'Update in progress'
                : !hasChanges
                  ? 'No changes to apply'
                  : !canAfford
                    ? `Insufficient credits: need ${totalCost.toLocaleString()}, you have ${playerCredits.toLocaleString()}`
                    : `Apply defense changes for ${totalCost.toLocaleString()} credits`
            }
          >
            {saving ? 'Updating...' : `Apply Changes (💰 ${totalCost.toLocaleString()})`}
          </button>
        </div>
      </div>
    </div>
  );
};