import React, { useState } from 'react';
import { useResourceCatalog } from '../../hooks/useResourceCatalog';
import './universe-detail.css';

interface PlanetDetailProps {
  planet: any;
  onBack: () => void;
  onUpdate?: (updatedPlanet: any) => void;
}

const PlanetDetail: React.FC<PlanetDetailProps> = ({ planet, onBack, onUpdate }) => {
  const { getIcon, getLabel } = useResourceCatalog();
  const [editingField, setEditingField] = useState<string | null>(null);
  const [editValues, setEditValues] = useState<any>({});
  const [isLoading, setIsLoading] = useState(false);

  const handleEdit = (field: string, currentValue: any) => {
    setEditingField(field);
    setEditValues({ ...editValues, [field]: currentValue });
  };

  const handleSave = async (_field: string) => {
    // There is no admin planet-edit endpoint (no PATCH/PUT /admin/planets/{id}).
    // The inline-edit affordance below is disabled (fields render read-only), so
    // this is unreachable; kept as a safe no-op rather than firing a 404 that
    // reads to the operator as a transient "Failed to update" error.
    setEditingField(null);
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
              value={editValues[field] !== undefined ? editValues[field] : value}
              onChange={(e) => setEditValues({ 
                ...editValues, 
                [field]: type === 'number' ? parseInt(e.target.value) || 0 : e.target.value 
              })}
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

    // Read-only: there is no admin planet-edit endpoint, so fields are not
    // click-to-edit (previously this fired a PATCH that 404'd behind a
    // misleading "Failed to update" alert).
    return (
      <span
        className="editable-field"
        title="Read-only — no admin planet-edit endpoint exists yet"
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
                  options={['TERRA', 'M_CLASS', 'L_CLASS', 'O_CLASS', 'K_CLASS', 'H_CLASS', 'D_CLASS', 'C_CLASS']}
                />
              </span>
            </div>
            <div className="info-item">
              <span className="label">Owner:</span>
              <span className="value">
                <EditableField field="owner_name" value={planet.owner_name || 'Uncolonized'} type="text" />
              </span>
            </div>
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