import React, { useState, useEffect } from 'react';
import { api } from '../../utils/auth';
import PageHeader from '../ui/PageHeader';
import './pages.css';

interface Station {
  id: string;
  name: string;
  sector_id: string;
  sector_name?: string;
  station_type: string;
  trade_volume: number;
  max_capacity: number | null;
  security_level: number | null;
  defense_drones?: number | null;
  docking_fee: number;
  tax_rate?: number | null;
  owner_id?: string | null;
  owner_name?: string | null;
  created_at: string;
  is_operational: boolean;
  commodities: string[];
}

const StationsManager: React.FC = () => {
  const [ports, setPorts] = useState<Station[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [filterClass, setFilterClass] = useState('all');
  const [currentPage, setCurrentPage] = useState(1);
  const [itemsPerPage] = useState(100);
  const [total, setTotal] = useState(0);

  // Modal states
  const [selectedPort, setSelectedPort] = useState<Station | null>(null);
  const [showPortModal, setShowPortModal] = useState(false);
  const [showAddModal, setShowAddModal] = useState(false);
  const [modalMode, setModalMode] = useState<'view' | 'edit' | 'add'>('view');

  const fetchPorts = async () => {
    try {
      setLoading(true);
      const offset = (currentPage - 1) * itemsPerPage;
      const response = await api.get('/api/v1/admin/stations', {
        params: { limit: itemsPerPage, offset, ...(searchTerm ? { search: searchTerm } : {}) }
      });
      setPorts(response.data.stations || []);
      setTotal(response.data.total ?? (response.data.stations || []).length);
      setError(null);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to fetch stations');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const handle = setTimeout(fetchPorts, searchTerm ? 300 : 0);
    return () => clearTimeout(handle);
  }, [currentPage, searchTerm]);

  // Handler functions
  const handleViewPort = (port: Station) => {
    setSelectedPort(port);
    setModalMode('view');
    setShowPortModal(true);
  };

  const handleEditPort = (port: Station) => {
    setSelectedPort(port);
    setModalMode('edit');
    setShowPortModal(true);
  };

  const handleDeletePort = async (port: Station) => {
    if (!confirm(`Are you sure you want to delete station "${port.name}"? This action cannot be undone.`)) {
      return;
    }

    try {
      await api.delete(`/api/v1/admin/stations/${port.id}`);
      setPorts(ports.filter(p => p.id !== port.id));
      alert('Station deleted successfully');
    } catch (err: any) {
      alert(`Failed to delete station: ${err.response?.data?.detail || err.message}`);
    }
  };

  const handleAddPort = () => {
    setSelectedPort(null);
    setModalMode('add');
    setShowAddModal(true);
  };

  const closeModal = () => {
    setShowPortModal(false);
    setShowAddModal(false);
    setSelectedPort(null);
  };

  const handlePortUpdated = (updatedPort: Station) => {
    setPorts(ports.map(p => p.id === updatedPort.id ? updatedPort : p));
    closeModal();
  };

  const handlePortAdded = (newPort: Station) => {
    setPorts([...ports, newPort]);
    closeModal();
    fetchPorts(); // Refresh to get updated data
  };

  // Name search is server-side (search param); the type filter operates on the current page.
  const filteredPorts = ports.filter(port =>
    filterClass === 'all' || port.station_type.toLowerCase().includes(filterClass.toLowerCase())
  );

  // Server-side pagination: total reflects the full station count, not this page.
  const totalPages = Math.max(1, Math.ceil(total / itemsPerPage));
  const paginatedPorts = filteredPorts;

  if (loading) {
    return (
      <div className="page-container">
        <PageHeader title="Stations Manager" subtitle="Comprehensive Station Administration" />
        <div className="loading-container">
          <div className="loading-spinner"></div>
          <p>Loading stations...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="page-container">
      <PageHeader title="Stations Manager" subtitle="Comprehensive Station Administration" />
      
      {error && (
        <div className="error-message">
          <p>{error}</p>
          <button onClick={() => setError(null)}>×</button>
        </div>
      )}

      {/* Add New Station Button */}
      <div className="page-actions">
        <button className="add-btn" onClick={handleAddPort}>
          + Add New Station
        </button>
      </div>

      {/* Search and Filter Controls */}
      <div className="table-controls">
        <div className="search-section">
          <input
            type="text"
            placeholder="Search stations by name..."
            value={searchTerm}
            onChange={(e) => { setSearchTerm(e.target.value); setCurrentPage(1); }}
            className="search-input"
          />
        </div>

        <div className="filter-section">
          <select
            value={filterClass}
            onChange={(e) => setFilterClass(e.target.value)}
            className="filter-select"
          >
            <option value="all">All Station Types</option>
            <option value="trade">Trade Stations</option>
            <option value="military">Military Stations</option>
            <option value="research">Research Stations</option>
            <option value="mining">Mining Stations</option>
            <option value="refueling">Refueling Stations</option>
          </select>
        </div>

        <div className="results-info">
          <span>Showing {ports.length.toLocaleString()} of {total.toLocaleString()} stations</span>
        </div>
      </div>

      {/* Stations Table */}
      <div className="crud-table-container">
        <table className="crud-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Sector</th>
              <th>Class</th>
              <th>Trade Volume</th>
              <th>Security</th>
              <th>Docking Fee</th>
              <th>Owner</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {paginatedPorts.map(port => (
              <tr key={port.id}>
                <td className="name-cell">
                  <strong>{port.name}</strong>
                </td>
                <td>{port.sector_name || port.sector_id}</td>
                <td>
                  <span className={`port-class ${port.station_type.toLowerCase().replace(' ', '-')}`}>
                    {port.station_type}
                  </span>
                </td>
                <td>
                  {port.trade_volume.toLocaleString()}
                  {port.max_capacity != null ? ` / ${port.max_capacity.toLocaleString()}` : ''}
                </td>
                <td>
                  {port.security_level != null ? (
                    <span className={`security-level level-${Math.floor(port.security_level / 20)}`}>
                      {port.security_level}
                    </span>
                  ) : (
                    <span className="text-muted">—</span>
                  )}
                </td>
                <td>{port.docking_fee.toLocaleString()} credits</td>
                <td>{port.owner_id ? (port.owner_name || port.owner_id) : 'Independent'}</td>
                <td>
                  <span className={`status ${port.is_operational ? 'operational' : 'offline'}`}>
                    {port.is_operational ? '✓ Operational' : '✗ Offline'}
                  </span>
                </td>
                <td>
                  <div className="action-buttons">
                    <button 
                      className="view-btn" 
                      title="View Details"
                      onClick={() => handleViewPort(port)}
                    >
                      👁️
                    </button>
                    <button 
                      className="edit-btn" 
                      title="Edit Port"
                      onClick={() => handleEditPort(port)}
                    >
                      ✏️
                    </button>
                    <button 
                      className="delete-btn" 
                      title="Delete Port"
                      onClick={() => handleDeletePort(port)}
                    >
                      🗑️
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination (server-side) */}
      {totalPages > 1 && (
        <div className="pagination">
          <button
            onClick={() => setCurrentPage(prev => Math.max(prev - 1, 1))}
            disabled={currentPage === 1}
            className="pagination-btn"
          >
            Previous
          </button>

          <span className="pagination-info">
            Page {currentPage} of {totalPages}
          </span>

          <button
            onClick={() => setCurrentPage(prev => Math.min(prev + 1, totalPages))}
            disabled={currentPage >= totalPages}
            className="pagination-btn"
          >
            Next
          </button>
        </div>
      )}

      {/* Port View/Edit Modal */}
      {showPortModal && selectedPort && (
        <PortModal
          port={selectedPort}
          mode={modalMode as 'view' | 'edit'}
          onClose={closeModal}
          onSave={handlePortUpdated}
        />
      )}

      {/* Add Port Modal */}
      {showAddModal && (
        <AddPortModal
          onClose={closeModal}
          onSave={handlePortAdded}
        />
      )}
    </div>
  );
};

// Port Modal Component for View/Edit
// "Port" is an alias for Station — the backend serves stations as ports.
type Port = Station;

interface PortModalProps {
  port: Port;
  mode: 'view' | 'edit';
  onClose: () => void;
  onSave: (port: Port) => void;
}

const PortModal: React.FC<PortModalProps> = ({ port, mode, onClose, onSave }) => {
  // Only include fields that the backend PATCH actually writes.
  // Excluded: station_type (column is `type`), max_capacity (no column),
  // security_level (stored in defenses JSONB), docking_fee (computed),
  // owner_name (column is owner_id — handled separately below).
  const [formData, setFormData] = useState({
    name: port.name,
    trade_volume: port.trade_volume ?? 0,
    owner_id: port.owner_id ?? ''
  });
  // Read-only display-only values (never sent to backend)
  const displayData = {
    station_type: port.station_type,
    max_capacity: port.max_capacity ?? '',
    security_level: port.security_level ?? '',
    docking_fee: port.docking_fee
  };
  const [saving, setSaving] = useState(false);
  const [players, setPlayers] = useState<Player[]>([]);
  const [loadingPlayers, setLoadingPlayers] = useState(true);

  // Fetch players when modal opens (for edit mode)
  useEffect(() => {
    if (mode === 'edit') {
      const fetchPlayers = async () => {
        try {
          setLoadingPlayers(true);
          const playersResponse = await api.get('/api/v1/admin/players/comprehensive?limit=1000&filter_active=true');
          const playersData = playersResponse.data.players || [];
          
          // Players are already filtered as active by the API
          setPlayers(playersData);
        } catch (err: any) {
          console.error('Failed to fetch players:', err);
        } finally {
          setLoadingPlayers(false);
        }
      };

      fetchPlayers();
    } else {
      setLoadingPlayers(false);
    }
  }, [mode]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (mode === 'view') return;

    try {
      setSaving(true);
      // Only send fields with real backend columns:
      //   name, trade_volume, owner_id (nullable UUID)
      const payload: Record<string, unknown> = {
        name: formData.name,
        trade_volume: formData.trade_volume
      };
      if (formData.owner_id !== '') {
        payload.owner_id = formData.owner_id;
      } else {
        payload.owner_id = null;
      }
      await api.patch(`/api/v1/admin/ports/${port.id}`, payload);
      onSave({ ...port, name: formData.name, trade_volume: formData.trade_volume, owner_id: formData.owner_id || null });
    } catch (err: any) {
      alert(`Failed to update port: ${err.response?.data?.detail || err.message}`);
    } finally {
      setSaving(false);
    }
  };

  const isReadOnly = mode === 'view';

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>{mode === 'view' ? 'View' : 'Edit'} Port: {port.name}</h2>
          <button className="close-btn" onClick={onClose}>×</button>
        </div>
        
        <form onSubmit={handleSubmit} className="modal-form">
          <div className="form-grid">
            {/* Writable fields */}
            <div className="form-group">
              <label>Port Name</label>
              <input
                type="text"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                readOnly={isReadOnly}
                required
              />
            </div>

            <div className="form-group">
              <label>Trade Volume</label>
              <input
                type="number"
                value={formData.trade_volume}
                onChange={(e) => setFormData({ ...formData, trade_volume: parseInt(e.target.value) || 0 })}
                readOnly={isReadOnly}
                min="0"
              />
            </div>

            <div className="form-group">
              <label>Port Owner</label>
              {isReadOnly ? (
                <input
                  type="text"
                  value={port.owner_name || port.owner_id || 'Independent'}
                  readOnly={true}
                />
              ) : loadingPlayers ? (
                <div className="loading-placeholder">Loading players...</div>
              ) : (
                <select
                  value={formData.owner_id}
                  onChange={(e) => setFormData({ ...formData, owner_id: e.target.value })}
                >
                  <option value="">Independent (No Owner)</option>
                  {players.map(player => (
                    <option key={player.id} value={player.id}>
                      {player.username} ({player.credits.toLocaleString()} credits)
                    </option>
                  ))}
                </select>
              )}
            </div>

            {/* Read-only display fields — backend stores these elsewhere or derives them */}
            <div className="form-group">
              <label>Port Class</label>
              <input
                type="text"
                value={displayData.station_type}
                readOnly
                disabled
                title="Read-only — not a writable field (backend stores this as `type`; use a migration or seed script to change)"
              />
            </div>

            <div className="form-group">
              <label>Max Capacity</label>
              <input
                type="text"
                value={displayData.max_capacity !== '' ? String(displayData.max_capacity) : '—'}
                readOnly
                disabled
                title="Read-only — not a writable field (no max_capacity column on Station)"
              />
            </div>

            <div className="form-group">
              <label>Security Level</label>
              <input
                type="text"
                value={displayData.security_level !== '' ? String(displayData.security_level) : '—'}
                readOnly
                disabled
                title="Read-only — not a writable field (backend stores this in defenses JSONB)"
              />
            </div>

            <div className="form-group">
              <label>Docking Fee</label>
              <input
                type="text"
                value={displayData.docking_fee.toLocaleString()}
                readOnly
                disabled
                title="Read-only — not a writable field (computed by the backend; not a stored column)"
              />
            </div>
          </div>
          
          <div className="port-info">
            <h3>Port Information</h3>
            <div className="info-grid">
              <div className="info-item">
                <span className="label">Sector ID:</span>
                <span className="value">{port.sector_id}</span>
              </div>
              <div className="info-item">
                <span className="label">Sector Name:</span>
                <span className="value">{port.sector_name || 'Unknown'}</span>
              </div>
              <div className="info-item">
                <span className="label">Created:</span>
                <span className="value">{new Date(port.created_at).toLocaleDateString()}</span>
              </div>
              <div className="info-item">
                <span className="label">Status:</span>
                <span className="value">{port.is_operational ? '✓ Operational' : '✗ Offline'}</span>
              </div>
            </div>
          </div>
          
          <div className="modal-actions">
            <button type="button" onClick={onClose} className="cancel-btn">
              {mode === 'view' ? 'Close' : 'Cancel'}
            </button>
            {mode === 'edit' && (
              <button type="submit" disabled={saving} className="save-btn">
                {saving ? 'Saving...' : 'Save Changes'}
              </button>
            )}
          </div>
        </form>
      </div>
    </div>
  );
};

// Add Port Modal Component
interface AddPortModalProps {
  onClose: () => void;
  onSave: (port: Station) => void;
}

interface Sector {
  id: string;
  sector_id: number;
  name: string;
  has_port: boolean;
}

interface Player {
  id: string;
  username: string;
  email: string;
  credits: number;
  is_active: boolean;
}

const AddPortModal: React.FC<AddPortModalProps> = ({ onClose, onSave }) => {
  const [formData, setFormData] = useState({
    name: '',
    sector_id: '',
    station_type: 'CLASS_1',
    trade_volume: 1000,
    max_capacity: 5000,
    security_level: 50,
    docking_fee: 100,
    owner_name: ''
  });
  const [saving, setSaving] = useState(false);
  const [sectors, setSectors] = useState<Sector[]>([]);
  const [loadingSectors, setLoadingSectors] = useState(true);
  const [players, setPlayers] = useState<Player[]>([]);
  const [loadingPlayers, setLoadingPlayers] = useState(true);

  // Fetch available sectors and players when modal opens
  useEffect(() => {
    const fetchData = async () => {
      // Fetch sectors
      try {
        setLoadingSectors(true);
        const sectorsResponse = await api.get('/api/v1/admin/sectors?limit=1000');
        const sectorsData = sectorsResponse.data.sectors || [];
        
        // Filter out sectors that already have ports
        const availableSectors = sectorsData.filter((sector: any) => !sector.has_port);
        setSectors(availableSectors);
      } catch (err: any) {
        console.error('Failed to fetch sectors:', err);
        alert('Failed to load sectors. Please try again.');
      } finally {
        setLoadingSectors(false);
      }

      // Fetch players
      try {
        setLoadingPlayers(true);
        const playersResponse = await api.get('/api/v1/admin/players/comprehensive?limit=1000&filter_active=true');
        const playersData = playersResponse.data.players || [];
        
        // Players are already filtered as active by the API
        setPlayers(playersData);
      } catch (err: any) {
        console.error('Failed to fetch players:', err);
        alert('Failed to load players. Please try again.');
      } finally {
        setLoadingPlayers(false);
      }
    };

    fetchData();
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    // Validate sector selection
    if (!formData.sector_id) {
      alert('Please select a sector for the new port.');
      return;
    }
    
    try {
      setSaving(true);
      const response = await api.post('/api/v1/admin/ports', formData);
      onSave(response.data);
    } catch (err: any) {
      alert(`Failed to create port: ${err.response?.data?.detail || err.message}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Add New Port</h2>
          <button className="close-btn" onClick={onClose}>×</button>
        </div>
        
        {(!loadingSectors || !loadingPlayers) && (
          <div className="modal-info">
            {!loadingSectors && (
              <span>{sectors.length} sectors available</span>
            )}
            {!loadingSectors && !loadingPlayers && <span> • </span>}
            {!loadingPlayers && (
              <span>{players.length} active players</span>
            )}
          </div>
        )}
        
        <form onSubmit={handleSubmit} className="modal-form">
          <div className="form-grid">
            <div className="form-group">
              <label>Port Name *</label>
              <input
                type="text"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                required
                placeholder="Enter port name"
              />
            </div>
            
            <div className="form-group">
              <label>Sector *</label>
              {loadingSectors ? (
                <div className="loading-placeholder">Loading sectors...</div>
              ) : (
                <select
                  value={formData.sector_id}
                  onChange={(e) => setFormData({ ...formData, sector_id: e.target.value })}
                  required
                  disabled={sectors.length === 0}
                >
                  <option value="">Select a sector</option>
                  {sectors.map(sector => (
                    <option key={sector.id} value={sector.sector_id}>
                      Sector {sector.sector_id} - {sector.name}
                    </option>
                  ))}
                </select>
              )}
              {!loadingSectors && sectors.length === 0 && (
                <div className="no-sectors-message">
                  No sectors available (all sectors already have ports)
                </div>
              )}
            </div>
            
            <div className="form-group">
              <label>Port Class *</label>
              <select
                value={formData.station_type}
                onChange={(e) => setFormData({ ...formData, station_type: e.target.value })}
                required
              >
                <option value="CLASS_0">CLASS_0 - Sol System</option>
                <option value="CLASS_1">CLASS_1 - Mining Operation</option>
                <option value="CLASS_2">CLASS_2 - Agricultural Center</option>
                <option value="CLASS_3">CLASS_3 - Industrial Hub</option>
                <option value="CLASS_4">CLASS_4 - Distribution Center</option>
                <option value="CLASS_5">CLASS_5 - Collection Hub</option>
                <option value="CLASS_6">CLASS_6 - Mixed Market</option>
                <option value="CLASS_7">CLASS_7 - Resource Exchange</option>
                <option value="CLASS_8">CLASS_8 - Black Hole (Premium Buyer)</option>
                <option value="CLASS_9">CLASS_9 - Nova (Premium Seller)</option>
                <option value="CLASS_10">CLASS_10 - Luxury Market</option>
                <option value="CLASS_11">CLASS_11 - Advanced Tech Hub</option>
              </select>
            </div>
            
            <div className="form-group">
              <label>Trade Volume</label>
              <input
                type="number"
                value={formData.trade_volume}
                onChange={(e) => setFormData({ ...formData, trade_volume: parseInt(e.target.value) })}
                min="0"
              />
            </div>
            
            <div className="form-group">
              <label>Max Capacity</label>
              <input
                type="number"
                value={formData.max_capacity}
                onChange={(e) => setFormData({ ...formData, max_capacity: parseInt(e.target.value) })}
                min="0"
              />
            </div>
            
            <div className="form-group">
              <label>Security Level</label>
              <input
                type="number"
                value={formData.security_level}
                onChange={(e) => setFormData({ ...formData, security_level: parseInt(e.target.value) })}
                min="0"
                max="100"
              />
            </div>
            
            <div className="form-group">
              <label>Docking Fee</label>
              <input
                type="number"
                value={formData.docking_fee}
                onChange={(e) => setFormData({ ...formData, docking_fee: parseInt(e.target.value) })}
                min="0"
              />
            </div>
            
            <div className="form-group">
              <label>Port Owner</label>
              {loadingPlayers ? (
                <div className="loading-placeholder">Loading players...</div>
              ) : (
                <select
                  value={formData.owner_name}
                  onChange={(e) => setFormData({ ...formData, owner_name: e.target.value })}
                >
                  <option value="">Independent (No Owner)</option>
                  {players.map(player => (
                    <option key={player.id} value={player.username}>
                      {player.username} ({player.credits.toLocaleString()} credits)
                    </option>
                  ))}
                </select>
              )}
              {!loadingPlayers && players.length === 0 && (
                <div className="no-players-message">
                  No active players available
                </div>
              )}
            </div>
          </div>
          
          <div className="modal-actions">
            <button type="button" onClick={onClose} className="cancel-btn">
              Cancel
            </button>
            <button type="submit" disabled={saving} className="save-btn">
              {saving ? 'Creating...' : 'Create Port'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default StationsManager;