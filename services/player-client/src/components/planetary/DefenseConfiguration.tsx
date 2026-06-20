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
  /** ADR-0076 BASE per-unit cost before citadel/planet-type scaling. */
  baseCost: number;
  maxUnits: number;
}

const DEFENSE_TYPES: DefenseType[] = [
  {
    type: 'turrets',
    name: 'Laser Turrets',
    icon: '🔫',
    description: 'Automated defense turrets that target incoming attackers',
    effectiveness: 'Effective against drones and small ships',
    baseCost: 500,
    maxUnits: 1000
  },
  {
    type: 'shields',
    name: 'Shield Generators',
    icon: '🛡️',
    description: 'Energy shields that protect against bombardment',
    effectiveness: 'Reduces damage from orbital attacks',
    baseCost: 1000,
    maxUnits: 500
  },
  {
    type: 'drones',
    name: 'Defense Drones',
    icon: '✈️',
    description: 'Piloted drones that intercept enemy forces',
    effectiveness: 'Versatile defense against all threat types',
    baseCost: 2000,
    maxUnits: 250
  }
];

// ─── ADR-0076 (Accepted) — Scaled defense pricing ────────────────────────────
// The SERVER charges per ADDED unit:
//   price = round_to_nearest_10(BASE[unit] × CITADEL_MULT[level] × PLANET_MOD[type])
// This client MUST mirror the SAME formula or the "you can afford this" gate
// becomes a lie (the affordability check is computed against these numbers).
// Source of truth mirrored 1:1 from
// services/gameserver/src/services/planetary_service.py (defense_unit_price).

// Citadel-level price multiplier. citadel_level <= 1 or null -> 1.0; > 5 -> 3.0.
const CITADEL_MULT: Record<number, number> = { 1: 1.0, 2: 1.25, 3: 1.6, 4: 2.2, 5: 3.0 };

// Planet-type price multiplier, keyed by the server PlanetType enum NAME.
// Terran/Oceanic 0.75 · Mountainous/Arctic 1.0 · Desert/Volcanic 1.25 ·
// Gas Giant/Barren 1.5. Any type NOT listed falls back to 1.0 — this default is
// NO-CANON (ADR-0076 names only these eight types), so it is FLAGGED in the UI
// (ICE, JUNGLE, TROPICAL, ARTIFICIAL resolve to 1.0 by this fallback). The
// client's own planetType union also uses 'frozen' for the ARCTIC world, which
// normalizes to ARCTIC (1.0) below.
const PLANET_MOD: Record<string, number> = {
  TERRAN: 0.75,
  OCEANIC: 0.75,
  MOUNTAINOUS: 1.0,
  ARCTIC: 1.0,
  DESERT: 1.25,
  VOLCANIC: 1.25,
  GAS_GIANT: 1.5,
  BARREN: 1.5,
};
const PLANET_MOD_DEFAULT = 1.0; // NO-CANON fallback (flagged)

// Normalize an arbitrary runtime planetType string (the payload is inconsistent:
// 'terran', 'TERRAN', 'PlanetType.TERRAN', 'frozen', …) to a server enum NAME.
// Mirrors planetary_service.defense_unit_price's lenient string match.
const normalizeTypeKey = (planetType: string | null | undefined): string => {
  const raw = (planetType || '')
    .toString()
    .toUpperCase()
    .replace('PLANETTYPE.', '')
    .replace(/[\s-]/g, '_')
    .trim();
  // The client's narrow union calls the ARCTIC world 'frozen' (and CSS treats
  // frozen/glacial/arctic as one ice family); fold those onto ARCTIC for pricing.
  if (raw === 'FROZEN' || raw === 'GLACIAL') return 'ARCTIC';
  return raw;
};

// True only when the resolved planet type is an explicitly-priced ADR-0076 type.
// A type that lands on PLANET_MOD_DEFAULT is the NO-CANON path we surface a note for.
const isCanonPlanetType = (planetType: string | null | undefined): boolean =>
  Object.prototype.hasOwnProperty.call(PLANET_MOD, normalizeTypeKey(planetType));

const planetTypeMod = (planetType: string | null | undefined): number => {
  const key = normalizeTypeKey(planetType);
  return PLANET_MOD[key] ?? PLANET_MOD_DEFAULT;
};

const citadelMult = (citadelLevel: number | null | undefined): number => {
  const level = citadelLevel || 0;
  if (level <= 1) return CITADEL_MULT[1];
  if (level >= 5) return CITADEL_MULT[5];
  return CITADEL_MULT[level];
};

// ADR-0076 scaled per-unit price. Rounding is HALF-UP to the nearest 10,
// computed with integer arithmetic to match the server EXACTLY:
//   server: int((raw + 5) // 10) * 10
// e.g. L1 turret Terran = round(500×1.0×0.75)=380 (375 -> 380);
//      L5 turret Gas = 500×3.0×1.5 = 2250; L5 fighter Gas = 2000×3.0×1.5 = 9000.
const defenseUnitPrice = (
  baseCost: number,
  citadelLevel: number | null | undefined,
  planetType: string | null | undefined
): number => {
  const raw = baseCost * citadelMult(citadelLevel) * planetTypeMod(planetType);
  // Math.floor matches Python's // floor division for the non-negative `raw`
  // these inputs always produce.
  return Math.floor((raw + 5) / 10) * 10;
};

export const DefenseConfiguration: React.FC<DefenseConfigurationProps> = ({
  planet,
  onUpdate,
  onClose
}) => {
  const { playerState, refreshPlayerState, getCitadelInfo } = useGame();
  const [defenses, setDefenses] = useState<PlanetDefenses>(planet.defenses);
  const [tempDefenses, setTempDefenses] = useState<PlanetDefenses>(planet.defenses);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // ADR-0076 pricing inputs. planetType is on the planet payload; citadel_level
  // is NOT — it lives behind GET /planets/{id}/citadel, so fetch it on mount.
  // Until it resolves (or if it fails — e.g. unbuilt citadel / transient error),
  // citadelLevel stays null, which the formula treats as the L1 (×1.0) baseline.
  // That null->L1 fallback exactly matches the server's own clamp, so the shown
  // price is correct for any L0/L1 planet and is the safe lower bound otherwise.
  const [citadelLevel, setCitadelLevel] = useState<number | null>(null);
  const [citadelError, setCitadelError] = useState<boolean>(false);

  useEffect(() => {
    let cancelled = false;
    setCitadelLevel(null);
    setCitadelError(false);
    (async () => {
      try {
        const info = await getCitadelInfo(planet.id);
        if (cancelled) return;
        const level = typeof info?.citadel_level === 'number' ? info.citadel_level : null;
        setCitadelLevel(level);
      } catch {
        // Owner-only endpoint can 400, or the network can hiccup. Fall back to
        // the L1/×1.0 baseline (with a note in the UI) rather than guessing high.
        if (!cancelled) setCitadelError(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [planet.id, getCitadelInfo]);

  // Resolved scaling multipliers + a per-type/per-unit price table for this planet.
  const planetMod = planetTypeMod(planet.planetType);
  const planetTypeIsCanon = isCanonPlanetType(planet.planetType);
  const effectiveCitadelMult = citadelMult(citadelLevel);
  // True while we genuinely don't know the citadel level (still loading or the
  // fetch failed) AND it could be > L1 — i.e. the shown price may understate.
  const citadelLevelUnknown = citadelLevel === null;
  const unitPriceFor = (defenseType: DefenseType): number =>
    defenseUnitPrice(defenseType.baseCost, citadelLevel, planet.planetType);

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
      // Only ADDED units cost credits — the server charges for increases only
      // (reducing defenses is free, no refund). Mirror that here so the shown
      // cost matches what is actually deducted. The per-unit price is the
      // ADR-0076 scaled price (base × citadelMult × planetTypeMod), NOT the
      // flat base, so the affordability gate matches the server's charge.
      const added = Math.max(0, target - current);
      return total + (added * unitPriceFor(type));
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
              <span className={`stat-value ${!canAfford ? 'insufficient' : ''}`}>
                💰 {totalCost.toLocaleString()}
              </span>
            </div>
          </div>

          {/* ADR-0076 pricing context: the per-unit price scales with citadel
              level and planet type. Surface the modifiers so the cost isn't a
              mystery, and flag the NO-CANON planet-type fallback + any
              unknown-citadel baseline assumption. */}
          <div
            className="defense-pricing-note"
            style={{
              marginTop: 'var(--space-sm)',
              fontSize: '0.75rem',
              color: 'var(--text-tertiary)'
            }}
          >
            <span className="pricing-label" style={{ color: 'var(--text-secondary)' }}>
              Pricing modifiers:
            </span>{' '}
            <span className="pricing-mod">
              citadel ×{effectiveCitadelMult.toLocaleString(undefined, { maximumFractionDigits: 2 })}
              {citadelLevelUnknown
                ? ' (L1 baseline assumed)'
                : ` (L${citadelLevel})`}
            </span>
            {' · '}
            <span className="pricing-mod">
              {planet.planetType || 'unknown'} ×
              {planetMod.toLocaleString(undefined, { maximumFractionDigits: 2 })}
            </span>
          </div>

          {citadelLevelUnknown && (
            <div
              className="defense-pricing-caveat"
              style={{
                marginTop: 'var(--space-xs)',
                fontSize: '0.75rem',
                color: 'var(--text-tertiary)',
                fontStyle: 'italic'
              }}
            >
              <span className="caveat-icon" style={{ marginRight: 'var(--space-xs)' }}>ℹ️</span>
              <span>
                {citadelError
                  ? 'Citadel level unavailable — showing the L1 (×1.0) baseline price. The actual charge may be higher if this colony has a higher citadel level.'
                  : 'Loading citadel level — showing the L1 (×1.0) baseline price for now.'}
              </span>
            </div>
          )}

          {!planetTypeIsCanon && (
            <div
              className="defense-pricing-caveat"
              style={{
                marginTop: 'var(--space-xs)',
                fontSize: '0.75rem',
                color: 'var(--warning-color)',
                fontStyle: 'italic'
              }}
            >
              <span className="caveat-icon" style={{ marginRight: 'var(--space-xs)' }}>⚠️</span>
              <span>
                NO-CANON: planet type “{planet.planetType || 'unknown'}” has no
                ADR-0076 price modifier; defaulting to ×1.0.
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
            // ADR-0076 scaled per-unit price for THIS planet (base × citadelMult
            // × planetTypeMod, rounded to nearest 10). Only added units cost
            // credits (matches the server + the total); `diff` is still used
            // below for the +/- direction badge.
            const unitPrice = unitPriceFor(defenseType);
            const cost = Math.max(0, diff) * unitPrice;

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
                        💰 {unitPrice.toLocaleString()} cr / unit
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