import React, { useState, useEffect } from 'react';
import { api } from '../../utils/auth';
import { formatUniverseAdminError } from '../../utils/universeAdminError';
import './planet-detail-modal.css';

interface Planet {
  id: string;
  name: string;
  sector_id: string;
  sector_name?: string;
  planet_type: string;
  population: number;
  max_population: number;
  defense_level: number;
  resource_production?: number;
  owner_id?: string;
  owner_name?: string;
  created_at: string;
  is_habitable?: boolean;
  atmosphere?: string;
  gravity?: number;
  habitability_score?: number;
  resource_richness?: number;
  genesis_created?: boolean;
  colonized_at?: string;
}

interface PlanetDetailModalProps {
  planet: Planet | null;
  isOpen: boolean;
  onClose: () => void;
  onSave?: (updatedPlanet: Planet) => void;
  mode: 'view' | 'edit';
}

type PirateHoldingRow = {
  id: string;
  tier?: string | null;
  owner_player_id?: string | null;
};

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

function httpStatus(err: unknown): number | undefined {
  return (err as { response?: { status?: number } })?.response?.status;
}

const PlanetDetailModal: React.FC<PlanetDetailModalProps> = ({
  planet,
  isOpen,
  onClose,
  onSave,
  mode
}) => {
  const [editedPlanet, setEditedPlanet] = useState<Planet | null>(null);
  const [isEditing, setIsEditing] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pirateHoldings, setPirateHoldings] = useState<PirateHoldingRow[]>([]);
  const [loadingHoldings, setLoadingHoldings] = useState(false);

  useEffect(() => {
    if (planet) {
      setEditedPlanet({ ...planet });
    }
    setIsEditing(mode === 'edit');
  }, [planet, mode]);

  useEffect(() => {
    const fetchPirateHoldings = async () => {
      if (!planet?.sector_id && planet?.sector_id !== 0) {
        setPirateHoldings([]);
        return;
      }
      setLoadingHoldings(true);
      setError(null);
      try {
        const holdingsResponse = await api.get(
          `/api/v1/admin/sectors/${planet.sector_id}/pirate-holdings`,
        );
        setPirateHoldings(asPirateHoldings(holdingsResponse.data));
      } catch (err) {
        setPirateHoldings([]);
        if (httpStatus(err) !== 404) {
          setError(formatUniverseAdminError(err, 'Failed to load pirate holdings'));
        }
      } finally {
        setLoadingHoldings(false);
      }
    };
    fetchPirateHoldings();
  }, [planet]);

  const handleSave = async () => {
    if (!editedPlanet) return;
    setLoading(true);
    setError(null);
    try {
      await api.patch(`/api/v1/admin/planets/${editedPlanet.id}`, {
        name: editedPlanet.name,
        type: editedPlanet.planet_type,
        habitability_score: editedPlanet.habitability_score,
        resource_richness: editedPlanet.resource_richness,
        gravity: editedPlanet.gravity,
        defense_level: editedPlanet.defense_level,
      });
      setIsEditing(false);
      if (onSave) onSave(editedPlanet);
    } catch (err: unknown) {
      setError(formatUniverseAdminError(err, 'Failed to save planet changes'));
    } finally {
      setLoading(false);
    }
  };

  const handleCancel = () => {
    if (planet) {
      setEditedPlanet({ ...planet });
    }
    setIsEditing(false);
    setError(null);
  };

  const handleInputChange = (field: keyof Planet, value: any) => {
    if (editedPlanet) {
      setEditedPlanet({
        ...editedPlanet,
        [field]: value
      });
    }
  };

  if (!isOpen || !planet || !editedPlanet) {
    return null;
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="planet-detail-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>
            {isEditing ? 'Edit Planet' : 'Planet Details'}: {editedPlanet.name}
          </h2>
          <button className="close-btn" onClick={onClose}>×</button>
        </div>

        {error && (
          <div className="error-message">
            {error}
          </div>
        )}

        <div className="modal-content">
          {/* Basic Information */}
          <div className="section">
            <h3>Basic Information</h3>
            <div className="field-grid">
              <div className="field">
                <label>Name</label>
                {isEditing ? (
                  <input
                    type="text"
                    value={editedPlanet.name}
                    onChange={(e) => handleInputChange('name', e.target.value)}
                  />
                ) : (
                  <span>{editedPlanet.name}</span>
                )}
              </div>

              <div className="field">
                <label>Planet Type</label>
                {isEditing ? (
                  <select
                    value={editedPlanet.planet_type}
                    onChange={(e) => handleInputChange('planet_type', e.target.value)}
                  >
                    <option value="TERRAN">Terran</option>
                    <option value="DESERT">Desert</option>
                    <option value="OCEANIC">Oceanic</option>
                    <option value="ICE">Ice</option>
                    <option value="VOLCANIC">Volcanic</option>
                    <option value="GAS_GIANT">Gas Giant</option>
                    <option value="BARREN">Barren</option>
                    <option value="JUNGLE">Jungle</option>
                    <option value="ARCTIC">Arctic</option>
                    <option value="TROPICAL">Tropical</option>
                    <option value="MOUNTAINOUS">Mountainous</option>
                    <option value="ARTIFICIAL">Artificial</option>
                  </select>
                ) : (
                  <span className={`planet-type ${editedPlanet.planet_type.toLowerCase()}`}>
                    {editedPlanet.planet_type}
                  </span>
                )}
              </div>

              <div className="field">
                <label>Sector</label>
                <span>{editedPlanet.sector_name || editedPlanet.sector_id}</span>
              </div>

              <div className="field">
                <label>Genesis Created</label>
                <span className={`status ${editedPlanet.genesis_created ? 'genesis' : 'natural'}`}>
                  {editedPlanet.genesis_created ? '🧬 Genesis' : '🌍 Natural'}
                </span>
              </div>
            </div>
          </div>

          <div className="section" data-testid="pirate-holdings-panel">
            <h3>Pirate holdings</h3>
            {loadingHoldings ? (
              <p>Loading pirate holdings...</p>
            ) : pirateHoldings.length === 0 ? (
              <p data-testid="pirate-holdings-empty">No pirate holdings in this sector.</p>
            ) : (
              <ul>
                {pirateHoldings.map((holding) => (
                  <li
                    key={holding.id}
                    data-testid={`pirate-holding-row-${holding.id}`}
                  >
                    <span>id: {holding.id}</span>
                    <span>tier: {formatHoldingTier(holding.tier)}</span>
                    <span>owner: {formatHoldingOwner(holding)}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* Population & Colonization */}
          <div className="section">
            <h3>Population & Colonization</h3>
            <div className="field-grid">
              <div className="field">
                <label>Current Population</label>
                {/* PlanetUpdateRequest has no population — display-only */}
                <span title="Not editable — not on PlanetUpdateRequest">
                  {editedPlanet.population.toLocaleString()}
                </span>
              </div>

              <div className="field">
                <label>Max Population</label>
                <span title="Not editable — not on PlanetUpdateRequest">
                  {editedPlanet.max_population.toLocaleString()}
                </span>
              </div>

              <div className="field">
                <label>Owner</label>
                <span>{editedPlanet.owner_name || 'Uncolonized'}</span>
              </div>

              <div className="field">
                <label>Colonized Date</label>
                <span>{editedPlanet.colonized_at ? new Date(editedPlanet.colonized_at).toLocaleDateString() : 'Not colonized'}</span>
              </div>
            </div>
          </div>

          {/* Planet Characteristics */}
          <div className="section">
            <h3>Planet Characteristics</h3>
            <div className="field-grid">
              <div className="field">
                <label>Habitability Score</label>
                {isEditing ? (
                  <input
                    type="number"
                    min="0"
                    max="100"
                    value={editedPlanet.habitability_score || 0}
                    onChange={(e) => handleInputChange('habitability_score', parseInt(e.target.value) || 0)}
                  />
                ) : (
                  <span className={`habitability-score score-${Math.floor((editedPlanet.habitability_score || 0) / 20)}`}>
                    {editedPlanet.habitability_score || 'N/A'}%
                  </span>
                )}
              </div>

              <div className="field">
                <label>Resource Richness</label>
                {isEditing ? (
                  <input
                    type="number"
                    step="0.1"
                    min="0"
                    max="5"
                    value={editedPlanet.resource_richness || 1.0}
                    onChange={(e) => handleInputChange('resource_richness', parseFloat(e.target.value) || 1.0)}
                  />
                ) : (
                  <span className={`resource-richness richness-${Math.floor((editedPlanet.resource_richness || 0) * 2)}`}>
                    {editedPlanet.resource_richness ? `${editedPlanet.resource_richness.toFixed(1)}x` : 'N/A'}
                  </span>
                )}
              </div>

              <div className="field">
                <label>Defense Level</label>
                {isEditing ? (
                  <input
                    type="number"
                    min="0"
                    max="100"
                    value={editedPlanet.defense_level}
                    onChange={(e) => handleInputChange('defense_level', parseInt(e.target.value) || 0)}
                  />
                ) : (
                  <span className={`defense-level level-${Math.floor(editedPlanet.defense_level / 20)}`}>
                    {editedPlanet.defense_level}
                  </span>
                )}
              </div>

              <div className="field">
                <label>Gravity</label>
                {isEditing ? (
                  <input
                    type="number"
                    step="0.1"
                    min="0"
                    max="10"
                    value={editedPlanet.gravity || 1.0}
                    onChange={(e) => handleInputChange('gravity', parseFloat(e.target.value) || 1.0)}
                  />
                ) : (
                  <span>{editedPlanet.gravity || 1.0}g</span>
                )}
              </div>
            </div>
          </div>

          {/* Environmental */}
          <div className="section">
            <h3>Environmental</h3>
            <div className="field-grid">
              <div className="field full-width">
                <label>Atmosphere</label>
                {/* PlanetUpdateRequest has no atmosphere — display-only */}
                <span title="Not editable — not on PlanetUpdateRequest">
                  {editedPlanet.atmosphere || 'No atmosphere data'}
                </span>
              </div>
            </div>
          </div>

          {/* System Information */}
          <div className="section">
            <h3>System Information</h3>
            <div className="field-grid">
              <div className="field">
                <label>Planet ID</label>
                <span className="system-info">{editedPlanet.id}</span>
              </div>

              <div className="field">
                <label>Created Date</label>
                <span>{new Date(editedPlanet.created_at).toLocaleDateString()}</span>
              </div>
            </div>
          </div>
        </div>

        <div className="modal-actions">
          {isEditing ? (
            <>
              <button 
                className="save-btn" 
                onClick={handleSave} 
                disabled={loading}
              >
                {loading ? 'Saving...' : 'Save Changes'}
              </button>
              <button className="cancel-btn" onClick={handleCancel}>
                Cancel
              </button>
            </>
          ) : (
            <>
              <button
                className="edit-btn"
                onClick={() => setIsEditing(true)}
              >
                Edit Planet
              </button>
              <button className="close-btn" onClick={onClose}>
                Close
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
};

export default PlanetDetailModal;