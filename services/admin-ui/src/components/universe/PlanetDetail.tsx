import React, { useEffect, useState } from 'react';
import { api } from '../../utils/auth';
import { axiosResponseStatus, formatAdminApiError } from '../../utils/adminApiError';
import { useResourceCatalog } from '../../hooks/useResourceCatalog';
import './universe-detail.css';

interface PlanetDetailProps {
  planet: any;
  onBack: () => void;
  onUpdate?: (updatedPlanet: any) => void;
}

/** Fields PlanetDetail may click-edit — must match PlanetUpdateRequest. */
const PATCHABLE_FIELDS = new Set([
  'name',
  'planet_type',
  'defense_level',
  'owner_id',
  // LEG-1489 residual schema fields (tip PlanetUpdateRequest)
  'size',
  'position',
  'gravity',
  'temperature',
  'water_coverage',
  'habitability_score',
  'resource_richness',
]);

const FLOAT_PATCH_FIELDS = new Set([
  'gravity',
  'temperature',
  'water_coverage',
  'resource_richness',
]);

/** Build PATCH body for a PlanetDetail EditableField (tip PlanetUpdateRequest). */
export function buildPlanetPatchPayload(field: string, value: unknown): Record<string, unknown> {
  if (field === 'planet_type') {
    return { type: value };
  }
  if (field === 'owner_id') {
    const raw = String(value ?? '').trim();
    // Tip: explicit null clears ownership (uncolonized); omit is leave-unchanged.
    return { owner_id: raw === '' ? null : raw };
  }
  if (FLOAT_PATCH_FIELDS.has(field)) {
    const n = typeof value === 'number' ? value : parseFloat(String(value));
    return { [field]: Number.isFinite(n) ? n : 0 };
  }
  if (field === 'size' || field === 'position' || field === 'habitability_score' || field === 'defense_level') {
    const n = typeof value === 'number' ? value : parseInt(String(value), 10);
    return { [field]: Number.isFinite(n) ? n : 0 };
  }
  return { [field]: value };
}

type PirateHoldingRow = {
  id: string;
  tier?: string | null;
  owner_player_id?: string | null;
  outlaw_base_id?: string | null;
};

function asIntegerSectorId(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const n = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(n) || !Number.isInteger(n)) return null;
  return n;
}

/** Global integer sector_id from planet payload (LEG-4176 path shape — never a UUID). */
export function resolvePlanetAdminSectorId(planet: unknown): number | null {
  if (planet === null || typeof planet !== 'object') return null;
  const p = planet as { sector_id?: unknown; planet?: { sector_id?: unknown } };
  return asIntegerSectorId(p.sector_id) ?? asIntegerSectorId(p.planet?.sector_id);
}

function asPirateHoldings(data: unknown): PirateHoldingRow[] {
  const holdings = (data as { holdings?: unknown } | null)?.holdings;
  if (!Array.isArray(holdings)) return [];
  return holdings.filter(
    (row): row is PirateHoldingRow =>
      row !== null &&
      typeof row === 'object' &&
      typeof (row as PirateHoldingRow).id === 'string',
  );
}

function formatHoldingOwner(holding: PirateHoldingRow): string {
  const owner = holding.owner_player_id;
  if (owner === null || owner === undefined || String(owner).trim() === '') {
    return 'pirate-controlled';
  }
  return String(owner);
}

function formatHoldingTier(tier: unknown): string {
  if (tier === null || tier === undefined) return '—';
  const s = String(tier).trim();
  return s === '' ? '—' : s;
}

/** Honest inspect placeholder when a GET key is omitted, null, or blank. Never invent. */
function formatHoldingInspectValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'number') {
    return Number.isFinite(value) ? String(value) : '—';
  }
  const s = String(value).trim();
  return s === '' ? '—' : s;
}

const PlanetDetail: React.FC<PlanetDetailProps> = ({ planet, onBack, onUpdate }) => {
  const { getIcon, getLabel } = useResourceCatalog();
  const [editingField, setEditingField] = useState<string | null>(null);
  const [editValues, setEditValues] = useState<any>({});
  const [isLoading, setIsLoading] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [pirateHoldings, setPirateHoldings] = useState<PirateHoldingRow[]>([]);
  const [holdingsError, setHoldingsError] = useState<string | null>(null);
  const sectorId = resolvePlanetAdminSectorId(planet);

  useEffect(() => {
    if (sectorId === null) {
      setPirateHoldings([]);
      setHoldingsError(null);
      return;
    }

    let cancelled = false;
    setHoldingsError(null);
    (async () => {
      try {
        const holdingsResponse = await api.get(
          `/api/v1/admin/sectors/${sectorId}/pirate-holdings`,
        );
        if (!cancelled) {
          setPirateHoldings(asPirateHoldings(holdingsResponse.data));
        }
      } catch (err: unknown) {
        if (cancelled) return;
        setPirateHoldings([]);
        if (axiosResponseStatus(err) !== 404) {
          setHoldingsError(
            formatAdminApiError(err, {
              fallback: 'Failed to load pirate holdings',
              scopeHint: 'admin.universe.manage',
            }),
          );
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [sectorId]);

  const handleSave = async (field: string) => {
    const newValue = editValues[field];
    if (newValue === undefined) {
      setEditingField(null);
      return;
    }
    if (!PATCHABLE_FIELDS.has(field)) {
      setEditingField(null);
      return;
    }
    const payload = buildPlanetPatchPayload(field, newValue);
    setIsLoading(true);
    setSaveError(null);
    try {
      await api.patch(`/api/v1/admin/planets/${planet.id}`, payload);
      if (onUpdate) {
        const local =
          field === 'owner_id'
            ? { owner_id: (payload as { owner_id: string | null }).owner_id }
            : { [field]: newValue };
        onUpdate({ ...planet, ...local });
      }
      setEditingField(null);
      setEditValues({});
    } catch (err: unknown) {
      setSaveError(
        formatAdminApiError(err, {
          fallback: `Failed to update ${field}`,
          scopeHint: 'admin.universe.manage',
        })
      );
      setEditingField(null);
    } finally {
      setIsLoading(false);
    }
  };

  const handleCancel = () => {
    setEditingField(null);
    setEditValues({});
  };

  const EditableField: React.FC<{
    field: string;
    value: any;
    type?: 'text' | 'number' | 'select';
    options?: string[];
  }> = ({ field, value, type = 'text', options }) => {
    const isEditing = editingField === field;
    
    if (isEditing) {
      return (
        <div className="editable-field editing">
          {type === 'select' && options ? (
            <select
              value={editValues[field] || value}
              onChange={(e) => setEditValues({ ...editValues, [field]: e.target.value })}
              disabled={isLoading}
            >
              {options.map(option => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
          ) : (
            <input
              type={type}
              step={FLOAT_PATCH_FIELDS.has(field) ? 'any' : undefined}
              value={editValues[field] !== undefined ? editValues[field] : value}
              onChange={(e) =>
                setEditValues({
                  ...editValues,
                  [field]:
                    type === 'number'
                      ? FLOAT_PATCH_FIELDS.has(field)
                        ? parseFloat(e.target.value) || 0
                        : parseInt(e.target.value, 10) || 0
                      : e.target.value,
                })
              }
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleSave(field);
                if (e.key === 'Escape') handleCancel();
              }}
              disabled={isLoading}
              autoFocus
            />
          )}
          <div className="edit-actions">
            <button 
              onClick={() => handleSave(field)} 
              disabled={isLoading}
              className="save-btn"
            >
              ✓
            </button>
            <button 
              onClick={handleCancel} 
              disabled={isLoading}
              className="cancel-btn"
            >
              ✕
            </button>
          </div>
        </div>
      );
    }

    if (!PATCHABLE_FIELDS.has(field)) {
      return (
        <span
          className="editable-field read-only"
          title="Not editable via admin planet PATCH"
        >
          {value}
        </span>
      );
    }

    return (
      <span
        className="editable-field clickable"
        title="Click to edit"
        style={{ cursor: 'pointer' }}
        onClick={() => {
          setEditingField(field);
          setEditValues((prev: any) => ({ ...prev, [field]: value }));
        }}
      >
        {value}
      </span>
    );
  };
  const getPlanetTypeInfo = (type: string) => {
    // Keyed on the canonical backend PlanetType enum (services/gameserver/.../models/planet.py):
    // TERRAN/DESERT/OCEANIC/ICE/VOLCANIC/GAS_GIANT/BARREN/JUNGLE/ARCTIC/TROPICAL/
    // MOUNTAINOUS/ARTIFICIAL — NOT the legacy M_CLASS/L_CLASS labels, which never
    // matched the stored value (so every planet previously fell back to "Terra").
    const typeInfo: { [key: string]: { name: string; description: string; color: string; icon: string } } = {
      'TERRAN': {
        name: 'Terran',
        description: 'Earth-like world — optimal all-round production; the Capital welcome type',
        color: '#4a7c59',
        icon: '🌍'
      },
      'OCEANIC': {
        name: 'Oceanic',
        description: 'Ocean world — excellent organics, habitable',
        color: '#2e6f9e',
        icon: '🌊'
      },
      'DESERT': {
        name: 'Desert',
        description: 'Arid world — moderate ore, low organics',
        color: '#daa520',
        icon: '🏜️'
      },
      'ICE': {
        name: 'Ice',
        description: 'Frozen world — challenging colonization, fuel/ice',
        color: '#b0e0e6',
        icon: '❄️'
      },
      'VOLCANIC': {
        name: 'Volcanic',
        description: 'Volcanic world — strong equipment / ore output',
        color: '#cd5c5c',
        icon: '🌋'
      },
      'GAS_GIANT': {
        name: 'Gas Giant',
        description: 'Gas giant — fuel harvesting; not colonizable',
        color: '#d8a657',
        icon: '🪐'
      },
      'BARREN': {
        name: 'Barren',
        description: 'Barren / dead world — minimal production',
        color: '#696969',
        icon: '🌑'
      },
      'JUNGLE': {
        name: 'Jungle',
        description: 'Lush jungle world — high organics, habitable',
        color: '#4f8f3a',
        icon: '🌴'
      },
      'ARCTIC': {
        name: 'Arctic',
        description: 'Cold polar world',
        color: '#cfe8f3',
        icon: '🧊'
      },
      'TROPICAL': {
        name: 'Tropical',
        description: 'Warm habitable world — strong organics',
        color: '#3fa66a',
        icon: '🏝️'
      },
      'MOUNTAINOUS': {
        name: 'Mountainous',
        description: 'Rugged highland world — good ore',
        color: '#8b7355',
        icon: '⛰️'
      },
      'ARTIFICIAL': {
        name: 'Artificial',
        description: 'Constructed / artificial world',
        color: '#8a8fa3',
        icon: '🛰️'
      }
    };
    const key = (type || '').toUpperCase();
    return typeInfo[key] || {
      name: type || 'Unknown',
      description: 'Unclassified planet type',
      color: '#6b7280',
      icon: '🪐'
    };
  };

  const typeInfo = getPlanetTypeInfo(planet.planet_type);
  const colonists = planet.colonists || { fuel: 0, organics: 0, equipment: 0 };
  const production = planet.production || { fuel: 0, organics: 0, equipment: 0 };

  return (
    <div className="planet-detail">
      <div className="detail-header">
        <button className="back-button" onClick={onBack}>
          ← Back to Sector
        </button>
        <h2>{typeInfo.icon} {planet.name}</h2>
        <div className="planet-type" style={{ backgroundColor: typeInfo.color }}>
          {typeInfo.name} Planet
        </div>
      </div>

      <div className="detail-content">
        {saveError ? (
          <div className="admin-save-error" role="alert">
            {saveError}
          </div>
        ) : null}
        {holdingsError ? (
          <div className="admin-save-error" role="alert">
            {holdingsError}
          </div>
        ) : null}
        <div className="planet-overview">
          <h3>Planet Overview</h3>
          <div className="info-grid">
            <div className="info-item">
              <span className="label">Name:</span>
              <span className="value">
                <EditableField field="name" value={planet.name} type="text" />
              </span>
            </div>
            <div className="info-item">
              <span className="label">Planet Type:</span>
              <span className="value">
                <EditableField 
                  field="planet_type" 
                  value={planet.planet_type} 
                  type="select"
                  options={['TERRAN', 'DESERT', 'OCEANIC', 'ICE', 'VOLCANIC', 'GAS_GIANT', 'BARREN', 'JUNGLE', 'ARCTIC', 'TROPICAL', 'MOUNTAINOUS', 'ARTIFICIAL']}
                />
              </span>
            </div>
            <div className="info-item">
              <span className="label">Owner ID:</span>
              <span className="value">
                <EditableField
                  field="owner_id"
                  value={planet.owner_id || ''}
                  type="text"
                />
              </span>
            </div>
            {planet.owner_name ? (
              <div className="info-item">
                <span className="label">Owner name:</span>
                <span className="value">
                  <EditableField field="owner_name" value={planet.owner_name} type="text" />
                </span>
              </div>
            ) : null}
            <div className="info-item">
              <span className="label">Citadel Level:</span>
              <span className="value">
                <EditableField 
                  field="citadel_level" 
                  value={planet.citadel_level} 
                  type="select"
                  options={['0', '1', '2', '3', '4', '5']}
                /> / 5
              </span>
            </div>
            <div className="info-item">
              <span className="label">Shield Level:</span>
              <span className="value">
                <EditableField 
                  field="shield_level" 
                  value={planet.shield_level} 
                  type="select"
                  options={['0', '1', '2', '3']}
                /> / 3
              </span>
            </div>
            <div className="info-item">
              <span className="label">Defense Drones:</span>
              <span className="value">
                <EditableField field="drones" value={planet.drones || 0} type="number" />
              </span>
            </div>
            <div className="info-item">
              <span className="label">Breeding Rate:</span>
              <span className="value">
                <EditableField field="breeding_rate" value={planet.breeding_rate} type="number" />% per day
              </span>
            </div>
            <div className="info-item">
              <span className="label">Size:</span>
              <span className="value">
                <EditableField field="size" value={planet.size ?? 1} type="number" />
              </span>
            </div>
            <div className="info-item">
              <span className="label">Position:</span>
              <span className="value">
                <EditableField field="position" value={planet.position ?? 1} type="number" />
              </span>
            </div>
            <div className="info-item">
              <span className="label">Gravity:</span>
              <span className="value">
                <EditableField field="gravity" value={planet.gravity ?? 1} type="number" />
              </span>
            </div>
            <div className="info-item">
              <span className="label">Temperature:</span>
              <span className="value">
                <EditableField field="temperature" value={planet.temperature ?? 20} type="number" />
              </span>
            </div>
            <div className="info-item">
              <span className="label">Water coverage:</span>
              <span className="value">
                <EditableField field="water_coverage" value={planet.water_coverage ?? 0} type="number" />%
              </span>
            </div>
            <div className="info-item">
              <span className="label">Habitability:</span>
              <span className="value">
                <EditableField
                  field="habitability_score"
                  value={planet.habitability_score ?? 0}
                  type="number"
                />
              </span>
            </div>
            <div className="info-item">
              <span className="label">Resource richness:</span>
              <span className="value">
                <EditableField
                  field="resource_richness"
                  value={planet.resource_richness ?? 1}
                  type="number"
                />
              </span>
            </div>
            <div className="info-item">
              <span className="label">Defense level:</span>
              <span className="value">
                <EditableField
                  field="defense_level"
                  value={planet.defense_level ?? 0}
                  type="number"
                />
              </span>
            </div>
          </div>
          <p className="planet-description">{typeInfo.description}</p>
        </div>

        <div className="colonist-section">
          <h3>Colonist Distribution</h3>
          <div className="colonist-grid">
            <div className="colonist-card fuel">
              <h4>{getIcon('fuel')} {getLabel('fuel')} Colonists</h4>
              <div className="colonist-info">
                <div className="count">
                  <EditableField field="colonists.fuel" value={colonists.fuel} type="number" />
                </div>
                <div className="capacity">Max: {(planet.colonistCapacity?.fuel || 5000).toLocaleString()}</div>
                <div className="percentage">
                  {Math.round((colonists.fuel / (planet.colonistCapacity?.fuel || 5000)) * 100)}% capacity
                </div>
              </div>
            </div>
            <div className="colonist-card organics">
              <h4>{getIcon('organics')} {getLabel('organics')} Colonists</h4>
              <div className="colonist-info">
                <div className="count">
                  <EditableField field="colonists.organics" value={colonists.organics} type="number" />
                </div>
                <div className="capacity">Max: {(planet.colonistCapacity?.organics || 5000).toLocaleString()}</div>
                <div className="percentage">
                  {Math.round((colonists.organics / (planet.colonistCapacity?.organics || 5000)) * 100)}% capacity
                </div>
              </div>
            </div>
            <div className="colonist-card equipment">
              <h4>{getIcon('equipment')} {getLabel('equipment')} Colonists</h4>
              <div className="colonist-info">
                <div className="count">
                  <EditableField field="colonists.equipment" value={colonists.equipment} type="number" />
                </div>
                <div className="capacity">Max: {(planet.colonistCapacity?.equipment || 5000).toLocaleString()}</div>
                <div className="percentage">
                  {Math.round((colonists.equipment / (planet.colonistCapacity?.equipment || 5000)) * 100)}% capacity
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="production-section">
          <h3>Production Rates</h3>
          <div className="production-grid">
            <div className="production-item">
              <span className="resource-icon">⛏️</span>
              <span className="resource-name">Ore</span>
              <div className="production-bar">
                <div className="bar-fill" style={{ width: `${(production.ore || 0) * 10}%` }}></div>
              </div>
              <span className="production-value">
                <EditableField field="production.ore" value={production.ore || 0} type="number" />/10
              </span>
            </div>
            <div className="production-item">
              <span className="resource-icon">🌾</span>
              <span className="resource-name">Organics</span>
              <div className="production-bar">
                <div className="bar-fill" style={{ width: `${(production.organics || 0) * 10}%` }}></div>
              </div>
              <span className="production-value">
                <EditableField field="production.organics" value={production.organics || 0} type="number" />/10
              </span>
            </div>
            <div className="production-item">
              <span className="resource-icon">🔧</span>
              <span className="resource-name">Equipment</span>
              <div className="production-bar">
                <div className="bar-fill" style={{ width: `${(production.equipment || 0) * 10}%` }}></div>
              </div>
              <span className="production-value">
                <EditableField field="production.equipment" value={production.equipment || 0} type="number" />/10
              </span>
            </div>
          </div>
        </div>

        <div className="planet-defenses">
          <h3>Planetary Defenses</h3>
          <div className="defense-grid">
            <div className="defense-item">
              <div className="defense-icon">🏰</div>
              <div className="defense-info">
                <h4>Citadel</h4>
                <p>Level {planet.citadel_level}</p>
                <p className="defense-desc">
                  {getCitadelDescription(planet.citadel_level)}
                </p>
              </div>
            </div>
            <div className="defense-item">
              <div className="defense-icon">🛡️</div>
              <div className="defense-info">
                <h4>Shields</h4>
                <p>Level {planet.shield_level}</p>
                <p className="defense-desc">
                  {getShieldDescription(planet.shield_level)}
                </p>
              </div>
            </div>
            <div className="defense-item">
              <div className="defense-icon">🤖</div>
              <div className="defense-info">
                <h4>Drones</h4>
                <p>{planet.drones || 0} deployed</p>
                <p className="defense-desc">
                  Automated defense drones protect the planet
                </p>
              </div>
            </div>
          </div>
        </div>

        <div className="ships-panel" data-testid="pirate-holdings-panel">
          <h3>Pirate holdings</h3>
          {sectorId === null ? (
            <p data-testid="pirate-holdings-unavailable">
              Pirate holdings cannot be loaded: sector id is missing or not an integer.
            </p>
          ) : pirateHoldings.length === 0 ? (
            <p data-testid="pirate-holdings-empty">No pirate holdings in this sector.</p>
          ) : (
            <div className="ships-list">
              {pirateHoldings.map((holding) => (
                <div
                  key={holding.id}
                  className="ship-item"
                  data-testid={`pirate-holding-row-${holding.id}`}
                >
                  <span>id: {holding.id}</span>
                  <span>tier: {formatHoldingTier(holding.tier)}</span>
                  <span>owner: {formatHoldingOwner(holding)}</span>
                  <span>
                    outlaw_base_id: {formatHoldingInspectValue(holding.outlaw_base_id)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {!planet.owner_id && (
          <div className="colonization-info">
            <h3>Colonization Requirements</h3>
            <ul>
              <li>Transport colonists from Terra (Sol System)</li>
              <li>Minimum 50 colonists of any type to establish colony</li>
              <li>Different planet types have varying production capabilities</li>
              <li>Build citadels and shields to protect your investment</li>
            </ul>
          </div>
        )}
      </div>
    </div>
  );
};

// Helper functions
const getCitadelDescription = (level: number): string => {
  const descriptions = [
    'No citadel - planet is undefended',
    'Basic fortification provides minimal defense',
    'Standard citadel with improved defensive capabilities',
    'Advanced citadel with strong defensive systems',
    'Fortress citadel with powerful defensive arrays',
    'Maximum citadel - nearly impregnable defenses'
  ];
  return descriptions[level] || descriptions[0];
};

const getShieldDescription = (level: number): string => {
  const descriptions = [
    'No shields - vulnerable to all attacks',
    'Basic shields provide 33% damage reduction',
    'Improved shields provide 66% damage reduction',
    'Maximum shields provide 99% damage reduction'
  ];
  return descriptions[level] || descriptions[0];
};

export default PlanetDetail;