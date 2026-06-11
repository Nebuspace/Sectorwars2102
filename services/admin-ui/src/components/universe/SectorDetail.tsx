import React, { useState, useEffect } from 'react';
import { api } from '../../utils/auth';

interface SectorDetailProps {
  sector: any;
  onBack: () => void;
  onPortClick: (port: any) => void;
  onPlanetClick: (planet: any) => void;
  onUpdate?: (updatedSector: any) => void;
}

const SectorDetail: React.FC<SectorDetailProps> = ({ sector, onBack, onPortClick, onPlanetClick, onUpdate }) => {
  const [portData, setPortData] = useState<any>(null);
  const [planetData, setPlanetData] = useState<any>(null);
  const [shipsInSector, setShipsInSector] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingField, setEditingField] = useState<string | null>(null);
  const [editValues, setEditValues] = useState<any>({});
  const [isUpdating, setIsUpdating] = useState(false);
  const [showCreatePortModal, setShowCreatePortModal] = useState(false);
  const [showCreatePlanetModal, setShowCreatePlanetModal] = useState(false);

  useEffect(() => {
    loadSectorDetails();
  }, [sector]);

  const loadSectorDetails = async () => {
    setLoading(true);
    console.log('Loading sector details for sector:', sector);
    console.log('Sector has_port:', sector.has_port, 'has_planet:', sector.has_planet);
    
    try {
      // Always try to load port data, regardless of has_port flag
      try {
        const portResponse = await api.get(`/api/v1/admin/sectors/${sector.sector_id}/port`);
        setPortData(portResponse.data);
        console.log('Station data loaded:', portResponse.data);
      } catch (portError) {
        console.log('No port found or error loading port data:', portError);
        setPortData(null);
      }

      // Load planet data if sector has planet
      if (sector.has_planet) {
        try {
          const planetResponse = await api.get(`/api/v1/admin/sectors/${sector.sector_id}/planet`);
          setPlanetData(planetResponse.data);
        } catch (planetError) {
          console.error('Error loading planet data:', planetError);
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
        console.error('Error loading ships data:', shipsError);
        setShipsInSector([]);
      }

    } catch (error) {
      console.error('Error loading sector details:', error);
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
      const value = editValues[field];
      
      // Update sector via API
      await api.patch(`/api/v1/admin/sectors/${sector.id}`, {
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
      alert(`Failed to update ${field}`);
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
      alert('Failed to create port');
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
      alert('Failed to create planet');
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
                          options={['NORMAL', 'NEBULA', 'ASTEROID_FIELD', 'RADIATION_ZONE', 'WARP_STORM']}
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
          </div>
        )}

        {/* Create Station Modal */}
      {showCreatePortModal && (
        <div className="modal-overlay" onClick={() => setShowCreatePortModal(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h3>Create New Station</h3>
            <form onSubmit={(e) => {
              e.preventDefault();
              const formData = new FormData(e.target as HTMLFormElement);
              const portData = {
                name: formData.get('name'),
                port_class: parseInt(formData.get('port_class') as string),
                tax_rate: parseFloat(formData.get('tax_rate') as string),
                defense_fighters: parseInt(formData.get('defense_fighters') as string),
                ore_price: parseInt(formData.get('ore_price') as string),
                organics_price: parseInt(formData.get('organics_price') as string),
                equipment_price: parseInt(formData.get('equipment_price') as string)
              };
              submitCreatePort(portData);
            }}>
              <div className="form-group">
                <label>Station Name:</label>
                <input type="text" name="name" required placeholder="Enter port name" />
              </div>
              <div className="form-group">
                <label>Station Class:</label>
                <select name="port_class" required>
                  <option value="1">Class 1 - Basic Trading Post</option>
                  <option value="2">Class 2 - Standard Station</option>
                  <option value="3">Class 3 - Major Trading Hub</option>
                  <option value="4">Class 4 - Commercial Center</option>
                  <option value="5">Class 5 - Mega Port</option>
                </select>
              </div>
              <div className="form-group">
                <label>Tax Rate (%):</label>
                <input type="number" name="tax_rate" min="0" max="50" step="0.1" defaultValue="5.0" required />
              </div>
              <div className="form-group">
                <label>Defense Drones:</label>
                <input type="number" name="defense_fighters" min="0" max="10000" defaultValue="100" required />
              </div>
              <div className="form-row">
                <div className="form-group">
                  <label>Ore Price:</label>
                  <input type="number" name="ore_price" min="1" max="200" defaultValue="25" required />
                </div>
                <div className="form-group">
                  <label>Organics Price:</label>
                  <input type="number" name="organics_price" min="1" max="200" defaultValue="15" required />
                </div>
                <div className="form-group">
                  <label>Equipment Price:</label>
                  <input type="number" name="equipment_price" min="1" max="200" defaultValue="50" required />
                </div>
              </div>
              <div className="form-actions">
                <button type="button" onClick={() => setShowCreatePortModal(false)}>Cancel</button>
                <button type="submit" disabled={isUpdating}>Create Station</button>
              </div>
            </form>
          </div>
        </div>
      )}
      
      {/* Create Planet Modal */}
      {showCreatePlanetModal && (
        <div className="modal-overlay" onClick={() => setShowCreatePlanetModal(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h3>Create New Planet</h3>
            <form onSubmit={(e) => {
              e.preventDefault();
              const formData = new FormData(e.target as HTMLFormElement);
              const planetData = {
                name: formData.get('name'),
                planet_type: formData.get('planet_type'),
                citadel_level: parseInt(formData.get('citadel_level') as string),
                shield_level: parseInt(formData.get('shield_level') as string),
                drones: parseInt(formData.get('drones') as string),
                breeding_rate: parseFloat(formData.get('breeding_rate') as string)
              };
              submitCreatePlanet(planetData);
            }}>
              <div className="form-group">
                <label>Planet Name:</label>
                <input type="text" name="name" required placeholder="Enter planet name" />
              </div>
              <div className="form-group">
                <label>Planet Type:</label>
                <select name="planet_type" required>
                  <option value="TERRA">Terra - Earth-like planet</option>
                  <option value="M_CLASS">M-Class - Standard habitable</option>
                  <option value="L_CLASS">L-Class - Rocky with thin atmosphere</option>
                  <option value="O_CLASS">O-Class - Ocean planet</option>
                  <option value="K_CLASS">K-Class - Desert/arid planet</option>
                  <option value="H_CLASS">H-Class - Harsh environment</option>
                  <option value="D_CLASS">D-Class - Barren/dead world</option>
                  <option value="C_CLASS">C-Class - Cold/ice planet</option>
                </select>
              </div>
              <div className="form-row">
                <div className="form-group">
                  <label>Citadel Level:</label>
                  <select name="citadel_level" required>
                    <option value="0">0 - No Citadel</option>
                    <option value="1">1 - Basic Fortification</option>
                    <option value="2">2 - Standard Citadel</option>
                    <option value="3">3 - Advanced Citadel</option>
                    <option value="4">4 - Fortress Citadel</option>
                    <option value="5">5 - Maximum Citadel</option>
                  </select>
                </div>
                <div className="form-group">
                  <label>Shield Level:</label>
                  <select name="shield_level" required>
                    <option value="0">0 - No Shields</option>
                    <option value="1">1 - Basic Shields</option>
                    <option value="2">2 - Improved Shields</option>
                    <option value="3">3 - Maximum Shields</option>
                  </select>
                </div>
              </div>
              <div className="form-row">
                <div className="form-group">
                  <label>Defense Drones:</label>
                  <input type="number" name="drones" min="0" max="50000" defaultValue="0" required />
                </div>
                <div className="form-group">
                  <label>Breeding Rate (%):</label>
                  <input type="number" name="breeding_rate" min="0" max="10" step="0.1" defaultValue="2.0" required />
                </div>
              </div>
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