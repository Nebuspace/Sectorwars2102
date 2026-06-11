import React, { useState, useCallback } from 'react';
import { gameAPI } from '../../services/api';
import type { Planet } from '../../types/planetary';
import './colonist-allocator.css';

interface ColonistAllocatorProps {
  planet: Planet;
  onUpdate?: (planet: Planet) => void;
  onClose?: () => void;
}

/** Head counts assigned to each production role. */
interface RoleAllocation {
  fuel: number;
  organics: number;
  equipment: number;
}

type RoleKey = keyof RoleAllocation;

/** Base output per colonist per day (canon: 10 units). */
const BASE_OUTPUT_PER_COLONIST = 10;

/**
 * Planet-type production efficiency per role (canon examples:
 * Oceanic 1.5x fuel, Mountainous 1.5x equipment; all others 1.0).
 */
const TYPE_EFFICIENCY: Record<string, Partial<RoleAllocation>> = {
  oceanic: { fuel: 1.5 },
  mountainous: { equipment: 1.5 },
};

const getTypeEfficiency = (planetType: string, role: RoleKey): number => {
  const overrides = TYPE_EFFICIENCY[(planetType || '').toLowerCase()];
  return overrides?.[role] ?? 1.0;
};

const ROLE_META: Array<{ key: RoleKey; icon: string; label: string; cssClass: string; color: string }> = [
  { key: 'fuel', icon: '⛽', label: 'Fuel Production', cssClass: 'fuel', color: '#ff6b6b' },
  { key: 'organics', icon: '🌿', label: 'Organics Production', cssClass: 'organics', color: '#51cf66' },
  { key: 'equipment', icon: '⚙️', label: 'Equipment Production', cssClass: 'equipment', color: '#339af0' },
];

export const ColonistAllocator: React.FC<ColonistAllocatorProps> = ({
  planet,
  onUpdate,
  onClose
}) => {
  const totalColonists = planet.colonists || 0;

  const clampInitial = (value: number): number =>
    Math.max(0, Math.min(value || 0, totalColonists));

  const initialAllocation: RoleAllocation = {
    fuel: clampInitial(planet.allocations?.fuel ?? 0),
    organics: clampInitial(planet.allocations?.organics ?? 0),
    equipment: clampInitial(planet.allocations?.equipment ?? 0),
  };

  const [allocations, setAllocations] = useState<RoleAllocation>(initialAllocation);
  const [tempAllocations, setTempAllocations] = useState<RoleAllocation>(initialAllocation);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const sumAllocated = (allocs: RoleAllocation): number =>
    allocs.fuel + allocs.organics + allocs.equipment;

  const idleColonists = Math.max(0, totalColonists - sumAllocated(tempAllocations));

  /** Projected production per role: colonists_in_role x 10 x planet-type efficiency. */
  const projectedProduction = useCallback((allocs: RoleAllocation): RoleAllocation => ({
    fuel: Math.floor(allocs.fuel * BASE_OUTPUT_PER_COLONIST * getTypeEfficiency(planet.planetType, 'fuel')),
    organics: Math.floor(allocs.organics * BASE_OUTPUT_PER_COLONIST * getTypeEfficiency(planet.planetType, 'organics')),
    equipment: Math.floor(allocs.equipment * BASE_OUTPUT_PER_COLONIST * getTypeEfficiency(planet.planetType, 'equipment')),
  }), [planet.planetType]);

  const previewProduction = projectedProduction(tempAllocations);
  const currentProduction = projectedProduction(allocations);

  const handleSliderChange = (role: RoleKey, requested: number) => {
    setTempAllocations(prev => {
      const othersTotal = sumAllocated(prev) - prev[role];
      // Hard ceiling: head count sum can never exceed available colonists
      const value = Math.max(0, Math.min(requested, totalColonists - othersTotal));
      return { ...prev, [role]: value };
    });
  };

  const handlePresetAllocation = (preset: 'balanced' | 'fuel' | 'organics' | 'equipment' | 'growth') => {
    const fractionsByPreset: Record<typeof preset, [number, number, number]> = {
      balanced: [0.33, 0.33, 0.34],
      fuel: [0.7, 0.15, 0.15],
      organics: [0.15, 0.7, 0.15],
      equipment: [0.15, 0.15, 0.7],
      growth: [0.2, 0.5, 0.2], // leaves ~10% idle for population growth
    };
    const [f, o, e] = fractionsByPreset[preset];
    setTempAllocations({
      fuel: Math.floor(totalColonists * f),
      organics: Math.floor(totalColonists * o),
      equipment: Math.floor(totalColonists * e),
    });
  };

  const handleSave = async () => {
    try {
      setSaving(true);
      setError(null);
      setSuccessMessage(null);

      const response = await gameAPI.planetary.allocateColonists(planet.id, {
        fuel: tempAllocations.fuel,
        organics: tempAllocations.organics,
        equipment: tempAllocations.equipment
      });

      if (response.success) {
        const savedAllocations: RoleAllocation = {
          fuel: response.allocations?.fuel ?? tempAllocations.fuel,
          organics: response.allocations?.organics ?? tempAllocations.organics,
          equipment: response.allocations?.equipment ?? tempAllocations.equipment,
        };
        setAllocations(savedAllocations);
        setTempAllocations(savedAllocations);
        setSuccessMessage('Colonist assignments updated successfully!');

        if (onUpdate) {
          const updatedPlanet = {
            ...planet,
            allocations: response.allocations,
            productionRates: response.productionRates
          };
          onUpdate(updatedPlanet);
        }

        setTimeout(() => setSuccessMessage(null), 3000);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update allocations');
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    setTempAllocations(allocations);
    setError(null);
    setSuccessMessage(null);
  };

  const hasChanges =
    tempAllocations.fuel !== allocations.fuel ||
    tempAllocations.organics !== allocations.organics ||
    tempAllocations.equipment !== allocations.equipment;

  const saveDisabledReason = saving
    ? 'Saving in progress'
    : !hasChanges
      ? 'No changes to save'
      : 'Save colonist assignments';

  const resetDisabledReason = saving
    ? 'Saving in progress'
    : !hasChanges
      ? 'Nothing to reset'
      : 'Discard unsaved changes';

  const getProductionDiff = (role: RoleKey) => {
    const diff = previewProduction[role] - currentProduction[role];
    if (diff === 0) return null;
    return (
      <span className={`production-diff ${diff > 0 ? 'positive' : 'negative'}`}>
        {diff > 0 ? '+' : ''}{diff.toLocaleString()}
      </span>
    );
  };

  const sliderPercent = (value: number): number =>
    totalColonists > 0 ? (value / totalColonists) * 100 : 0;

  return (
    <div className="colonist-allocator">
      <div className="allocator-header">
        <h3>Colonist Assignments - {planet.name}</h3>
        <button className="close-button" onClick={onClose} title="Close allocator">✕</button>
      </div>

      <div className="allocator-content">
        <div className="current-stats">
          <div className="stat-item">
            <span className="stat-label">Colonists:</span>
            <span className="stat-value">
              {totalColonists.toLocaleString()} / {planet.maxColonists.toLocaleString()}
            </span>
          </div>
          <div className="stat-item">
            <span className="stat-label">Assigned:</span>
            <span className="stat-value">{sumAllocated(tempAllocations).toLocaleString()}</span>
          </div>
          <div className="stat-item idle-stat">
            <span className="stat-label">Idle:</span>
            <span className="stat-value idle-value">{idleColonists.toLocaleString()}</span>
          </div>
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

        <div className="preset-buttons">
          <button
            className="preset-button"
            onClick={() => handlePresetAllocation('balanced')}
            title="Distribute colonists evenly across all roles"
          >
            ⚖️ Balanced
          </button>
          <button
            className="preset-button fuel"
            onClick={() => handlePresetAllocation('fuel')}
            title="Assign most colonists to fuel production"
          >
            ⛽ Fuel Focus
          </button>
          <button
            className="preset-button organics"
            onClick={() => handlePresetAllocation('organics')}
            title="Assign most colonists to organics production"
          >
            🌿 Organics Focus
          </button>
          <button
            className="preset-button equipment"
            onClick={() => handlePresetAllocation('equipment')}
            title="Assign most colonists to equipment production"
          >
            ⚙️ Equipment Focus
          </button>
          <button
            className="preset-button growth"
            onClick={() => handlePresetAllocation('growth')}
            title="Keep ~10% of colonists idle to support population growth"
          >
            👥 Growth Focus
          </button>
        </div>

        <div className="allocation-controls">
          {ROLE_META.map(({ key, icon, label, cssClass, color }) => {
            const efficiency = getTypeEfficiency(planet.planetType, key);
            return (
              <div className="allocation-slider" key={key}>
                <div className="slider-header">
                  <span className="resource-label">
                    <span className="resource-icon">{icon}</span> {label}
                    {efficiency !== 1.0 && (
                      <span
                        className="efficiency-badge"
                        title={`${planet.planetType} worlds produce ${efficiency}x ${key} per colonist`}
                      >
                        ×{efficiency}
                      </span>
                    )}
                  </span>
                  <span className="allocation-value">
                    {tempAllocations[key].toLocaleString()}
                  </span>
                </div>
                <input
                  type="range"
                  min="0"
                  max={totalColonists}
                  value={tempAllocations[key]}
                  onChange={(e) => handleSliderChange(key, parseInt(e.target.value))}
                  disabled={totalColonists === 0}
                  title={totalColonists === 0
                    ? 'No colonists on this planet to assign'
                    : `Colonists assigned to ${label.toLowerCase()}`}
                  className={`slider ${cssClass}-slider`}
                  style={{
                    background: `linear-gradient(to right, ${color} 0%, ${color} ${sliderPercent(tempAllocations[key])}%, var(--surface-secondary) ${sliderPercent(tempAllocations[key])}%, var(--surface-secondary) 100%)`
                  }}
                />
                <div className="production-preview">
                  <span className="preview-label">Projected:</span>
                  <span className="preview-value">
                    {previewProduction[key].toLocaleString()}/day
                    {getProductionDiff(key)}
                  </span>
                </div>
              </div>
            );
          })}

          <div className="allocation-slider unused">
            <div className="slider-header">
              <span className="resource-label">
                <span className="resource-icon">💤</span> Idle Colonists
              </span>
              <span className="allocation-value">{idleColonists.toLocaleString()}</span>
            </div>
            <div className="unused-bar">
              <div
                className="unused-fill"
                style={{ width: `${sliderPercent(idleColonists)}%` }}
              />
            </div>
            <div className="unused-note">
              Idle colonists contribute to population growth and colony maintenance
            </div>
          </div>
        </div>

        <div className="allocation-summary">
          <h4>Production Summary</h4>
          <div className="summary-grid">
            <div className="summary-item">
              <span className="summary-label">Workforce Assigned:</span>
              <span className="summary-value">
                {totalColonists > 0
                  ? `${Math.round((sumAllocated(tempAllocations) / totalColonists) * 100)}%`
                  : '0%'}
              </span>
            </div>
            <div className="summary-item">
              <span className="summary-label">Daily Output:</span>
              <span className="summary-value">
                {(previewProduction.fuel + previewProduction.organics + previewProduction.equipment).toLocaleString()} units
              </span>
            </div>
            <div className="summary-item">
              <span className="summary-label">Population Growth:</span>
              <span className="summary-value">
                +{(planet.productionRates?.colonists ?? 0).toLocaleString()}/day
              </span>
            </div>
          </div>
        </div>

        <div className="action-buttons">
          <button
            className="button secondary"
            onClick={handleReset}
            disabled={!hasChanges || saving}
            title={resetDisabledReason}
          >
            Reset
          </button>
          <button
            className="button primary"
            onClick={handleSave}
            disabled={!hasChanges || saving}
            title={saveDisabledReason}
          >
            {saving ? 'Saving...' : 'Save Assignments'}
          </button>
        </div>
      </div>
    </div>
  );
};
