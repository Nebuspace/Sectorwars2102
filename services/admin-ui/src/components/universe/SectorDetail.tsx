import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../utils/auth';
import { useToast } from '../../contexts/ToastContext';
import { formatUniverseAdminError } from '../../utils/universeAdminError';

/** Tip PlanetType enum — create options must stay ⊆ this set (no legacy M_CLASS…). */
const PLANET_TYPE_OPTIONS = [
  'TERRAN',
  'DESERT',
  'OCEANIC',
  'ICE',
  'VOLCANIC',
  'GAS_GIANT',
  'BARREN',
  'JUNGLE',
  'ARCTIC',
  'TROPICAL',
  'MOUNTAINOUS',
  'ARTIFICIAL',
] as const;

/** Tip StationType enum for StationCreateRequest.type */
const STATION_TYPE_OPTIONS = [
  'TRADING',
  'MILITARY',
  'INDUSTRIAL',
  'MINING',
  'SCIENTIFIC',
  'SHIPYARD',
  'OUTPOST',
  'BLACK_MARKET',
  'DIPLOMATIC',
  'CORPORATE',
] as const;

/** Tip SectorType enum — select must stay ⊆ this set (NORMAL is not valid; use STANDARD). */
const SECTOR_TYPE_OPTIONS = [
  'STANDARD',
  'NEBULA',
  'ASTEROID_FIELD',
  'BLACK_HOLE',
  'STAR_CLUSTER',
  'VOID',
  'INDUSTRIAL',
  'AGRICULTURAL',
  'FORBIDDEN',
  'WORMHOLE',
  'ANOMALY',
  'RADIATION_ZONE',
  'WARP_STORM',
] as const;

function normalizeControllingFaction(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  const s = String(value).trim();
  if (s === '' || s.toLowerCase() === 'none') return null;
  return s;
}

type PirateHoldingRow = {
  id: string;
  tier?: string | null;
  owner_player_id?: string | null;
  combat_lock_held_by?: string | null;
  captured_at?: string | null;
  current_strength?: number | string | null;
  owner_team_id?: string | number | null;
  region_id?: number | string | null;
  sector_id?: number | string | null;
  outlaw_base_id?: string | null;
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

/** Honest inspect placeholder when a GET key is omitted, null, or blank. Never invent. */
function formatHoldingInspectValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'number') {
    return Number.isFinite(value) ? String(value) : '—';
  }
  const s = String(value).trim();
  return s === '' ? '—' : s;
}

/** Non-empty outlaw_base_id for deep-link; null when absent/blank (LEG-4196). */
function outlawBaseIdForLink(value: string | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  const s = String(value).trim();
  return s === '' ? null : s;
}

interface SectorDetailProps {
  sector: any;
  onBack: () => void;
  onPortClick: (port: any) => void;
  onPlanetClick: (planet: any) => void;
  onUpdate?: (updatedSector: any) => void;
}

const SectorDetail: React.FC<SectorDetailProps> = ({ sector, onBack, onPortClick, onPlanetClick, onUpdate }) => {
  const toast = useToast();
  const [portData, setPortData] = useState<any>(null);
  const [planetData, setPlanetData] = useState<any>(null);
  const [shipsInSector, setShipsInSector] = useState<any[]>([]);
  const [pirateHoldings, setPirateHoldings] = useState<PirateHoldingRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [editingField, setEditingField] = useState<string | null>(null);
  const [editValues, setEditValues] = useState<any>({});
  const [isUpdating, setIsUpdating] = useState(false);
  const [showCreatePortModal, setShowCreatePortModal] = useState(false);
  const [showCreatePlanetModal, setShowCreatePlanetModal] = useState(false);

  useEffect(() => {
    loadSectorDetails();
  }, [sector]);

  const noteLoadFailure = (err: unknown, fallback: string) => {
    const status = (err as { response?: { status?: number } })?.response?.status;
    // 403/429 honesty (LEG-2820) + TypeError/network collapse (LEG-3065).
    // Keep expected 404 silent (port/planet may be absent).
    if (status === 403 || status === 429 || status === undefined) {
      setLoadError(formatUniverseAdminError(err, fallback));
    }
  };

  const loadSectorDetails = async () => {
    setLoading(true);
    setLoadError(null);

    try {
      // Always try to load port data, regardless of has_port flag
      try {
        const portResponse = await api.get(`/api/v1/admin/sectors/${sector.sector_id}/port`);
        setPortData(portResponse.data);
      } catch (portError) {
        noteLoadFailure(portError, 'Failed to load port data');
        setPortData(null);
      }

      // Load planet data if sector has planet
      if (sector.has_planet) {
        try {
          const planetResponse = await api.get(`/api/v1/admin/sectors/${sector.sector_id}/planet`);
          setPlanetData(planetResponse.data);
        } catch (planetError) {
          noteLoadFailure(planetError, 'Failed to load planet data');
          setPlanetData(null);
        }
      } else {
        setPlanetData(null);
      }

      // Load ships in sector
      try {
        const shipsResponse = await api.get(`/api/v1/admin/sectors/${sector.sector_id}/ships`);
        setShipsInSector((shipsResponse.data as any)?.ships || []);
      } catch (shipsError) {
        noteLoadFailure(shipsError, 'Failed to load ships data');
        setShipsInSector([]);
      }

      // Pirate holdings (LEG-4178) — always inspect; 404 → honest empty.
      try {
        const holdingsResponse = await api.get(
          `/api/v1/admin/sectors/${sector.sector_id}/pirate-holdings`,
        );
        setPirateHoldings(asPirateHoldings(holdingsResponse.data));
      } catch (holdingsError) {
        noteLoadFailure(holdingsError, 'Failed to load pirate holdings');
        setPirateHoldings([]);
      }

    } catch (error) {
      noteLoadFailure(error, 'Failed to load sector details');
    } finally {
      setLoading(false);
    }
  };

  const handleEdit = (field: string, currentValue: any) => {
    setEditingField(field);
    setEditValues({ ...editValues, [field]: currentValue });
  };

  const handleSave = async (field: string) => {
    try {
      setIsUpdating(true);
      let value = editValues[field];
      if (field === 'controlling_faction') {
        value = normalizeControllingFaction(value);
      }
      // SectorUpdateRequest: radiation_level / resource_regeneration are ge=0.0
      if (field === 'radiation_level' || field === 'resource_regeneration') {
        const n = Number(value);
        value = Number.isFinite(n) ? Math.max(0, n) : 0;
      }

      // Update sector via API (PUT — matches SectorEditModal; backend only has PUT)
      await api.put(`/api/v1/admin/sectors/${sector.id}`, {
        [field]: value
      });
      
      // Update local state
      const updatedSector = { ...sector, [field]: value };
      if (onUpdate) {
        onUpdate(updatedSector);
      }
      
      setEditingField(null);
    } catch (error) {
      console.error(`Failed to update ${field}:`, error);
      toast.error(formatUniverseAdminError(error, `Failed to update ${field}`));
    } finally {
      setIsUpdating(false);
    }
  };

  const handleCancel = () => {
    setEditingField(null);
    setEditValues({});
  };

  const handleCreatePort = () => {
    setShowCreatePortModal(true);
  };

  const handleCreatePlanet = () => {
    setShowCreatePlanetModal(true);
  };

  const submitCreatePort = async (portData: any) => {
    try {
      setIsUpdating(true);
      await api.post(`/api/v1/admin/sectors/${sector.sector_id}/port`, portData);
      
      // Update sector state
      const updatedSector = { ...sector, has_port: true };
      if (onUpdate) {
        onUpdate(updatedSector);
      }
      
      setShowCreatePortModal(false);
      await loadSectorDetails(); // Reload to get the new port data
    } catch (error) {
      console.error('Failed to create port:', error);
      toast.error(formatUniverseAdminError(error, 'Failed to create station'));
    } finally {
      setIsUpdating(false);
    }
  };

  const submitCreatePlanet = async (planetData: any) => {
    try {
      setIsUpdating(true);
      await api.post(`/api/v1/admin/sectors/${sector.sector_id}/planet`, planetData);
      
      // Update sector state
      const updatedSector = { ...sector, has_planet: true };
      if (onUpdate) {
        onUpdate(updatedSector);
      }
      
      setShowCreatePlanetModal(false);
      await loadSectorDetails(); // Reload to get the new planet data
    } catch (error) {
      console.error('Failed to create planet:', error);
      toast.error(formatUniverseAdminError(error, 'Failed to create planet'));
    } finally {
      setIsUpdating(false);
    }
  };

  const EditableField: React.FC<{
    field: string;
    value: any;
    type?: 'text' | 'number' | 'select' | 'boolean';
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
              disabled={isUpdating}
            >
              {options.map(option => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
          ) : type === 'boolean' ? (
            <select
              value={editValues[field] !== undefined ? editValues[field] : value}
              onChange={(e) => setEditValues({ ...editValues, [field]: e.target.value === 'true' })}
              disabled={isUpdating}
            >
              <option value="true">Yes</option>
              <option value="false">No</option>
            </select>
          ) : (
            <input
              type={type}
              value={editValues[field] !== undefined ? editValues[field] : value}
              onChange={(e) => setEditValues({ 
                ...editValues, 
                [field]: type === 'number' ? parseFloat(e.target.value) || 0 : e.target.value 
              })}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleSave(field);
                if (e.key === 'Escape') handleCancel();
              }}
              disabled={isUpdating}
              autoFocus
            />
          )}
          <div className="edit-actions">
            <button 
              onClick={() => handleSave(field)} 
              disabled={isUpdating}
              className="save-btn"
            >
              ✓
            </button>
            <button 
              onClick={handleCancel} 
              disabled={isUpdating}
              className="cancel-btn"
            >
              ✕
            </button>
          </div>
        </div>
      );
    }

    return (
      <span 
        className="editable-field clickable" 
        onClick={() => handleEdit(field, value)}
        title="Click to edit"
      >
        {type === 'boolean' ? (value ? 'Yes' : 'No') : value}
      </span>
    );
  };

  const getSectorTypeColor = (type: string) => {
    switch (type.toUpperCase()) {
      case 'NEBULA': return '#8B4D8B';
      case 'ASTEROID_FIELD': return '#A67B5B';
      case 'RADIATION_ZONE': return '#FFB347';
      case 'WARP_STORM': return '#6B8BFF';
      default: return '#4B7C4B';
    }
  };

  return (
    <div className="page-container">
      <div className="page-header">
        <div className="flex items-center gap-4">
          <button className="btn btn-secondary" onClick={onBack}>
            ← Back to Universe
          </button>
          <div>
            <h1 className="page-title">Sector {sector.sector_id}: {sector.name}</h1>
            <p className="page-subtitle">Detailed sector information and management</p>
          </div>
        </div>
      </div>

      <div className="page-content">
        {loadError && (
          <div className="error-message" role="alert">
            {loadError}
          </div>
        )}
        {loading ? (
          <div className="loading-state">
            <div className="spinner"></div>
            <p>Loading sector details...</p>
          </div>
        ) : (
          <div className="space-y-6">
            <section className="section">
              <div className="card">
                <div className="card-header">
                  <h3 className="card-title">Sector Information</h3>
                </div>
                <div className="card-body">
                  <div className="grid grid-cols-2 gap-4">
                    <div className="flex justify-between items-center">
                      <span className="font-medium text-muted">Name:</span>
                      <span className="text-primary">
                        <EditableField field="name" value={sector.name} type="text" />
                      </span>
                    </div>
                    <div className="flex justify-between items-center">
                      <span className="font-medium text-muted">Type:</span>
                      <span style={{ color: getSectorTypeColor(sector.type) }}>
                        <EditableField 
                          field="type" 
                          value={sector.type} 
                          type="select"
                          options={[...SECTOR_TYPE_OPTIONS]}
                        />
                      </span>
                    </div>
                    <div className="flex justify-between items-center">
                      <span className="font-medium text-muted">X Coordinate:</span>
                      <span className="font-mono">
                        <EditableField field="x_coord" value={sector.x_coord} type="number" />
                      </span>
                    </div>
                    <div className="flex justify-between items-center">
                      <span className="font-medium text-muted">Y Coordinate:</span>
                      <span className="font-mono">
                        <EditableField field="y_coord" value={sector.y_coord} type="number" />
                      </span>
                    </div>
                    <div className="flex justify-between items-center">
                      <span className="font-medium text-muted">Z Coordinate:</span>
                      <span className="font-mono">
                        <EditableField field="z_coord" value={sector.z_coord} type="number" />
                      </span>
                    </div>
                    <div className="flex justify-between items-center">
                      <span className="font-medium text-muted">Hazard Level:</span>
                      <span className={`font-semibold ${sector.hazard_level > 7 ? 'text-error' : sector.hazard_level > 4 ? 'text-warning' : 'text-success'}`}>
                        <EditableField field="hazard_level" value={sector.hazard_level} type="number" /> / 10
                      </span>
                    </div>
                    <div className="flex justify-between items-center">
                      <span className="font-medium text-muted">Discovered:</span>
                      <span>
                        <EditableField field="is_discovered" value={sector.is_discovered} type="boolean" />
                      </span>
                    </div>
                    <div className="flex justify-between items-center">
                      <span className="font-medium text-muted">Controlling Faction:</span>
                      <span>
                        <EditableField field="controlling_faction" value={sector.controlling_faction || 'None'} type="text" />
                      </span>
                    </div>
                    <div className="flex justify-between items-center col-span-2">
                      <span className="font-medium text-muted">Description:</span>
                      <span className="text-primary flex-1 text-right ml-4">
                        <EditableField
                          field="description"
                          value={sector.description ?? ''}
                          type="text"
                        />
                      </span>
                    </div>
                    <div className="flex justify-between items-center">
                      <span className="font-medium text-muted">Radiation Level:</span>
                      <span className="font-mono">
                        <EditableField
                          field="radiation_level"
                          value={sector.radiation_level ?? 0}
                          type="number"
                        />
                      </span>
                    </div>
                    <div className="flex justify-between items-center">
                      <span className="font-medium text-muted">Resource Regeneration:</span>
                      <span className="font-mono">
                        <EditableField
                          field="resource_regeneration"
                          value={sector.resource_regeneration ?? 0}
                          type="number"
                        />
                      </span>
                    </div>
                    <div className="flex justify-between items-center">
                      <span className="font-medium text-muted">Ships in Sector:</span>
                      <span className="font-semibold">{shipsInSector.length}</span>
                    </div>
                  </div>
                </div>
              </div>
            </section>

          <div className="sector-features">
            {/* Always show port section - either with data or empty state */}
            {sector.has_port && portData ? (
              <div className="feature-card port-card" onClick={() => onPortClick(portData)}>
                <h3>🏪 Station: {portData.name}</h3>
                <div className="feature-info">
                  <p>Class {portData.port_class || portData.class || 'Unknown'} Trading Post</p>
                  <p>Type: {portData.type || 'Unknown'}</p>
                  <p>Tax Rate: {((portData.tax_rate ?? 0) * 100).toFixed(1)}%</p>
                  <p>Defense Level: {portData.defense_level || portData.defense_weapons || 0}</p>
                  <p>Status: {portData.status || 'Unknown'}</p>
                  <button className="view-details">View Station Details →</button>
                </div>
              </div>
            ) : (
              <div className="feature-card empty-card port-creation">
                <h3>🏪 No Station in Sector</h3>
                <div className="feature-info">
                  <p>This sector has no trading port.</p>
                  <button 
                    className="create-feature-btn"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleCreatePort();
                    }}
                  >
                    + Create Station
                  </button>
                </div>
              </div>
            )}

            {sector.has_planet && planetData && (
              <div className="feature-card planet-card" onClick={() => onPlanetClick(planetData)}>
                <h3>🌍 Planet: {planetData.name}</h3>
                <div className="feature-info">
                  <p>Type: {planetData.planet_type}</p>
                  <p>Owner: {planetData.owner_name || 'Uncolonized'}</p>
                  <p>Citadel Level: {planetData.citadel_level}</p>
                  <button className="view-details">View Planet Details →</button>
                </div>
              </div>
            )}
            
            {!sector.has_planet && (
              <div className="feature-card empty-card planet-creation">
                <h3>🌍 No Planet in Sector</h3>
                <div className="feature-info">
                  <p>This sector has no colonizable planet.</p>
                  <button 
                    className="create-feature-btn"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleCreatePlanet();
                    }}
                  >
                    + Create Planet
                  </button>
                </div>
              </div>
            )}

            {sector.has_warp_tunnel && (
              <div className="feature-card warp-card">
                <h3>🌀 Warp Tunnels</h3>
                <div className="feature-info">
                  <p>Connected sectors via quantum tunnels</p>
                  <p className="warp-note">Use navigation computer to view connections</p>
                </div>
              </div>
            )}
          </div>

          {shipsInSector.length > 0 && (
            <div className="ships-panel">
              <h3>Ships in Sector</h3>
              <div className="ships-list">
                {shipsInSector.map((ship: any) => (
                  <div key={ship.id} className="ship-item">
                    <span className="ship-name">{ship.name}</span>
                    <span className="ship-type">{ship.type}</span>
                    <span className="ship-owner">{ship.owner_name}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

            <div className="ships-panel" data-testid="pirate-holdings-panel">
              <h3>Pirate holdings</h3>
              {pirateHoldings.length === 0 ? (
                <p data-testid="pirate-holdings-empty">No pirate holdings in this sector.</p>
              ) : (
                <div className="ships-list">
                  {pirateHoldings.map((holding) => {
                    const outlawBaseId = outlawBaseIdForLink(holding.outlaw_base_id);
                    return (
                    <div
                      key={holding.id}
                      className="ship-item"
                      data-testid={`pirate-holding-row-${holding.id}`}
                    >
                      <span>id: {holding.id}</span>
                      <span>tier: {holding.tier ?? '—'}</span>
                      <span>owner: {formatHoldingOwner(holding)}</span>
                      <span>
                        combat lock:{' '}
                        {holding.combat_lock_held_by && String(holding.combat_lock_held_by).trim() !== ''
                          ? holding.combat_lock_held_by
                          : 'none'}
                      </span>
                      <span>
                        captured_at:{' '}
                        {holding.captured_at && String(holding.captured_at).trim() !== ''
                          ? holding.captured_at
                          : '—'}
                      </span>
                      <span>
                        current_strength: {formatHoldingInspectValue(holding.current_strength)}
                      </span>
                      <span>
                        owner_team_id: {formatHoldingInspectValue(holding.owner_team_id)}
                      </span>
                      <span>region_id: {formatHoldingInspectValue(holding.region_id)}</span>
                      <span>sector_id: {formatHoldingInspectValue(holding.sector_id)}</span>
                      <span>
                        outlaw_base_id:{' '}
                        {outlawBaseId ? (
                          <Link
                            to={`/outlaw-bases/${outlawBaseId}`}
                            data-testid={`pirate-holding-outlaw-base-link-${holding.id}`}
                          >
                            {outlawBaseId}
                          </Link>
                        ) : (
                          formatHoldingInspectValue(holding.outlaw_base_id)
                        )}
                      </span>
                    </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Create Station Modal — StationCreateRequest: name + station_class + type */}
      {showCreatePortModal && (
        <div className="modal-overlay" onClick={() => setShowCreatePortModal(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h3>Create New Station</h3>
            <form onSubmit={(e) => {
              e.preventDefault();
              const formData = new FormData(e.target as HTMLFormElement);
              const portData = {
                name: formData.get('name'),
                station_class: parseInt(formData.get('station_class') as string, 10),
                type: formData.get('type') as string,
              };
              submitCreatePort(portData);
            }}>
              <div className="form-group">
                <label>Station Name:</label>
                <input type="text" name="name" required placeholder="Enter station name" />
              </div>
              <div className="form-group">
                <label>Station Class:</label>
                <select name="station_class" required defaultValue="6">
                  <option value="0">Class 0 - Sol System</option>
                  <option value="1">Class 1 - Mining Operation</option>
                  <option value="2">Class 2 - Agricultural Center</option>
                  <option value="3">Class 3 - Industrial Hub</option>
                  <option value="4">Class 4 - Distribution Center</option>
                  <option value="5">Class 5 - Collection Hub</option>
                  <option value="6">Class 6 - Mixed Market</option>
                  <option value="7">Class 7 - Resource Exchange</option>
                  <option value="8">Class 8 - Black Hole</option>
                  <option value="9">Class 9 - Nova</option>
                  <option value="10">Class 10 - Luxury Market</option>
                  <option value="11">Class 11 - Premium Tech</option>
                </select>
              </div>
              <div className="form-group">
                <label>Station Type:</label>
                <select name="type" required defaultValue="TRADING">
                  {STATION_TYPE_OPTIONS.map((t) => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
              </div>
              <p className="form-note">
                Tax rate, defenses, and commodity prices are set after create on the station detail page.
              </p>
              <div className="form-actions">
                <button type="button" onClick={() => setShowCreatePortModal(false)}>Cancel</button>
                <button type="submit" disabled={isUpdating}>Create Station</button>
              </div>
            </form>
          </div>
        </div>
      )}
      
      {/* Create Planet Modal — PlanetCreateRequest: name + type (no citadel/shield/drones/breeding) */}
      {showCreatePlanetModal && (
        <div className="modal-overlay" onClick={() => setShowCreatePlanetModal(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h3>Create New Planet</h3>
            <form onSubmit={(e) => {
              e.preventDefault();
              const formData = new FormData(e.target as HTMLFormElement);
              const planetData = {
                name: formData.get('name'),
                type: formData.get('type') as string,
              };
              submitCreatePlanet(planetData);
            }}>
              <div className="form-group">
                <label>Planet Name:</label>
                <input type="text" name="name" required placeholder="Enter planet name" />
              </div>
              <div className="form-group">
                <label>Planet Type:</label>
                <select name="type" required defaultValue="TERRAN">
                  {PLANET_TYPE_OPTIONS.map((t) => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
              </div>
              <p className="form-note">
                Citadel, shields, drones, and breeding are not part of create — configure after create if the API supports them.
              </p>
              <div className="form-actions">
                <button type="button" onClick={() => setShowCreatePlanetModal(false)}>Cancel</button>
                <button type="submit" disabled={isUpdating}>Create Planet</button>
              </div>
            </form>
          </div>
        </div>
      )}
      </div>
    </div>
  );
};

export default SectorDetail;