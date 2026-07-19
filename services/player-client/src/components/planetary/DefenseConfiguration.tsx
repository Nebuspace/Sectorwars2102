import React, { useEffect, useState } from 'react';
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
  maxUnits: number;
}

const DEFENSE_TYPES: DefenseType[] = [
  {
    type: 'turrets',
    name: 'Laser Turrets',
    icon: '🔫',
    description: 'Automated defense turrets that target incoming attackers',
    effectiveness: 'Effective against drones and small ships',
    maxUnits: 1000
  },
  {
    type: 'shields',
    name: 'Shield Generators',
    icon: '🛡️',
    description: 'Energy shields that protect against bombardment',
    effectiveness: 'Reduces damage from orbital attacks',
    maxUnits: 500
  },
  {
    type: 'drones',
    name: 'Defense Drones',
    icon: '✈️',
    description: 'Piloted drones that intercept enemy forces',
    effectiveness: 'Versatile defense against all threat types',
    maxUnits: 250
  }
];

// ADR-0076 (Accepted) — Scaled defense pricing. The server charges
// round_to_nearest_10(BASE[unit] × CITADEL_MULT[level] × PLANET_MOD[type])
// per ADDED unit. WO-API-PHASE1 B3: the formula is no longer mirrored here —
// GET /planets/{id}/defenses/pricing returns the server's own computed prices
// (via the EXACT defense_unit_price fn the commit path charges), so this UI
// can never drift out of sync with what a Save will actually cost.

// Maps a UI defense-slot key to the server's DefenseUpdateRequest/pricing
// field name — 'drones' is the canon display name for the 'fighters' column.
const serverKeyFor = (type: keyof PlanetDefenses): 'turrets' | 'shields' | 'fighters' =>
  type === 'drones' ? 'fighters' : type;

type DefensePricing = { turrets: number; shields: number; fighters: number };

export const DefenseConfiguration: React.FC<DefenseConfigurationProps> = ({
  planet,
  onUpdate,
  onClose
}) => {
  const { playerState, refreshPlayerState } = useGame();
  const [defenses, setDefenses] = useState<PlanetDefenses>(planet.defenses);
  const [tempDefenses, setTempDefenses] = useState<PlanetDefenses>(planet.defenses);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // Server-authoritative per-unit prices (WO-API-PHASE1 B3). null while
  // loading or if the fetch failed — there is no safe client-side fallback
  // price to guess (the server is the ONLY source of truth), so a null price
  // blocks Save rather than risk understating the real charge.
  const [unitPrices, setUnitPrices] = useState<DefensePricing | null>(null);
  const [pricingError, setPricingError] = useState<boolean>(false);

  useEffect(() => {
    let cancelled = false;
    setUnitPrices(null);
    setPricingError(false);
    (async () => {
      try {
        const pricing = await gameAPI.planetary.getDefensePricing(planet.id);
        if (cancelled) return;
        const valid =
          pricing &&
          typeof pricing.turrets === 'number' &&
          typeof pricing.shields === 'number' &&
          typeof pricing.fighters === 'number';
        if (valid) {
          setUnitPrices({ turrets: pricing.turrets, shields: pricing.shields, fighters: pricing.fighters });
        } else {
          setPricingError(true);
        }
      } catch {
        // Owner-only endpoint can 403, or the network can hiccup.
        if (!cancelled) setPricingError(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [planet.id]);

  const unitPriceFor = (defenseType: DefenseType): number | null =>
    unitPrices ? unitPrices[serverKeyFor(defenseType.type)] : null;

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

  // Calculate total cost. null while pricing hasn't loaded — there is no
  // safe client-side price to sum, so the caller must treat null as "unknown,
  // block Save" rather than as free.
  const calculateTotalCost = (): number | null => {
    if (!unitPrices) return null;
    return DEFENSE_TYPES.reduce((total, type) => {
      const current = defenses[type.type];
      const target = tempDefenses[type.type];
      // Only ADDED units cost credits — the server charges for increases only
      // (reducing defenses is free, no refund). Mirror that here so the shown
      // cost matches what is actually deducted. unitPrices is the server's
      // own ADR-0076-scaled price for THIS planet, not a flat base.
      const added = Math.max(0, target - current);
      return total + (added * unitPrices[serverKeyFor(type.type)]);
    }, 0);
  };

  const totalCost = calculateTotalCost();

  // Check if player can afford using real credits from game context.
  // Unknown pricing (still loading / failed) is never "affordable".
  const playerCredits = playerState?.credits ?? 0;
  const canAfford = totalCost !== null && playerCredits >= totalCost;

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
    if (totalCost === null) {
      setError('Defense pricing is unavailable right now — try reopening this panel.');
      return;
    }
    if (!canAfford) {
      setError(`Insufficient credits. You need ${totalCost.toLocaleString()} credits.`);
      return;
    }

    try {
      setSaving(true);
      setError(null);
      setSuccessMessage(null);

      // The backend's DefenseUpdateRequest accepts turrets/shields/fighters —
      // it has no 'drones' field (sending one is silently discarded). The
      // canon name is "drones" (defense.md); the storage column is
      // defense_fighters, and the response maps it back to 'drones'.
      const payload: { turrets: number; shields: number; fighters: number } = {
        turrets: tempDefenses.turrets,
        shields: tempDefenses.shields,
        fighters: tempDefenses.drones
      };
      const response = await gameAPI.planetary.updateDefenses(planet.id, payload);
      
      if (response.success) {
        setDefenses(response.defenses);
        setSuccessMessage('Planetary defenses updated successfully!');
        // Reflect the credit deduction in the cockpit balance immediately
        // (the server charged response.creditsSpent) instead of waiting for
        // the next background poll.
        void refreshPlayerState();
        
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
              <span className={`stat-value ${totalCost !== null && !canAfford ? 'insufficient' : ''}`}>
                💰 {totalCost !== null ? totalCost.toLocaleString() : '—'}
              </span>
            </div>
          </div>

          {pricingError && (
            <div
              className="defense-pricing-caveat"
              style={{
                marginTop: 'var(--space-sm)',
                fontSize: '0.75rem',
                color: 'var(--warning-color)',
                fontStyle: 'italic'
              }}
            >
              <span className="caveat-icon" style={{ marginRight: 'var(--space-xs)' }}>⚠️</span>
              <span>
                Unable to load defense pricing for this colony — try reopening
                this panel. Changes cannot be saved until pricing is available.
              </span>
            </div>
          )}

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
            // Server-authoritative ADR-0076-scaled per-unit price for THIS
            // planet (WO-API-PHASE1 B3). Only added units cost credits
            // (matches the server + the total); `diff` is still used below
            // for the +/- direction badge. null while pricing is unavailable.
            const unitPrice = unitPriceFor(defenseType);
            const cost = unitPrice !== null ? Math.max(0, diff) * unitPrice : null;

            return (
              <div key={defenseType.type} className="defense-control">
                <div className="control-header">
                  <div className="defense-info">
                    <span className="defense-icon">{defenseType.icon}</span>
                    <div className="defense-details">
                      <h5>{defenseType.name}</h5>
                      <p className="defense-description">{defenseType.description}</p>
                      <p className="defense-effectiveness">{defenseType.effectiveness}</p>
                      <p
                        className="defense-unit-price"
                        style={{
                          margin: 'var(--space-xs) 0 0 0',
                          fontSize: '0.8125rem',
                          fontWeight: 600,
                          color: 'var(--warning-color)'
                        }}
                      >
                        💰 {unitPrice !== null ? `${unitPrice.toLocaleString()} cr / unit` : 'price unavailable'}
                      </p>
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
                    <span className="cost-value">💰 {cost !== null ? cost.toLocaleString() : '—'}</span>
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
                  : totalCost === null
                    ? 'Defense pricing unavailable — cannot save right now'
                    : !canAfford
                      ? `Insufficient credits: need ${totalCost.toLocaleString()}, you have ${playerCredits.toLocaleString()}`
                      : `Apply defense changes for ${totalCost.toLocaleString()} credits`
            }
          >
            {saving ? 'Updating...' : `Apply Changes (💰 ${totalCost !== null ? totalCost.toLocaleString() : '—'})`}
          </button>
        </div>
      </div>
    </div>
  );
};