import React, { useState, useEffect } from 'react';
import { api } from '../../utils/auth';
import PageHeader from '../ui/PageHeader';
import { useToast, useConfirm } from '../../contexts/ToastContext';
import './pages.css';

interface WarpTunnel {
  id: string;
  name: string;
  origin_sector_id: number;
  destination_sector_id: number;
  origin_sector_name?: string;
  destination_sector_name?: string;
  type?: string;
  status?: string;
  stability?: number;
  energy_cost?: number;
  travel_time?: number;
  turn_cost?: number;
  max_ship_size?: string;
  is_bidirectional?: boolean;
  is_active?: boolean;
  created_at?: string;
  total_traversals?: number;
}

const WarpTunnelsManager: React.FC = () => {
  const toast = useToast();
  const confirm = useConfirm();
  const [warpTunnels, setWarpTunnels] = useState<WarpTunnel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [filterStatus, setFilterStatus] = useState('all');
  const [currentPage, setCurrentPage] = useState(1);
  const [itemsPerPage] = useState(20);

  // Modal states
  const [selectedTunnel, setSelectedTunnel] = useState<WarpTunnel | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [modalMode, setModalMode] = useState<'view' | 'edit'>('view');
  const [saving, setSaving] = useState(false);

  const fetchWarpTunnels = async () => {
    try {
      setLoading(true);
      const response = await api.get('/api/v1/admin/warp-tunnels');
      setWarpTunnels(response.data.warp_tunnels || []);
      setError(null);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to fetch warp tunnels');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchWarpTunnels();
  }, []);

  const handleViewTunnel = (tunnel: WarpTunnel) => {
    setSelectedTunnel(tunnel);
    setModalMode('view');
    setShowModal(true);
  };

  const handleEditTunnel = (tunnel: WarpTunnel) => {
    setSelectedTunnel(tunnel);
    setModalMode('edit');
    setShowModal(true);
  };

  const handleMaintenanceToggle = async (tunnel: WarpTunnel) => {
    const newActive = !tunnel.is_active;
    const action = newActive ? 'reactivate' : 'put into maintenance mode';
    if (!(await confirm({
      title: 'Update Tunnel Status',
      message: `Are you sure you want to ${action} tunnel "${tunnel.name}"?`,
      confirmLabel: newActive ? 'Reactivate' : 'Set Maintenance',
    }))) {
      return;
    }

    try {
      // Backend PUT /warp-tunnels/{id} takes a `status` enum, not `is_active`.
      // ACTIVE = operational; MAINTENANCE = temporarily offline.
      await api.put(`/api/v1/admin/warp-tunnels/${tunnel.id}`, {
        status: newActive ? 'ACTIVE' : 'MAINTENANCE'
      });
      setWarpTunnels(warpTunnels.map(t =>
        t.id === tunnel.id ? { ...t, is_active: newActive, status: newActive ? 'ACTIVE' : 'MAINTENANCE' } : t
      ));
    } catch (err: any) {
      toast.error(`Failed to update tunnel: ${err.response?.data?.detail || err.message}`);
    }
  };

  const handleDeleteTunnel = async (tunnel: WarpTunnel) => {
    if (!(await confirm({
      title: 'Delete Tunnel',
      message: `Are you sure you want to delete tunnel "${tunnel.name}"? This action cannot be undone.`,
      danger: true,
      confirmLabel: 'Delete',
    }))) {
      return;
    }

    try {
      await api.delete(`/api/v1/admin/warp-tunnels/${tunnel.id}`);
      setWarpTunnels(warpTunnels.filter(t => t.id !== tunnel.id));
    } catch (err: any) {
      toast.error(`Failed to delete tunnel: ${err.response?.data?.detail || err.message}`);
    }
  };

  const closeModal = () => {
    setShowModal(false);
    setSelectedTunnel(null);
  };

  const handleTunnelSaved = async (updatedData: Partial<WarpTunnel> & { is_active?: boolean; max_ship_size?: string }) => {
    if (!selectedTunnel) return;
    setSaving(true);
    try {
      // Translate the form fields to the backend PUT contract
      // (WarpTunnelUpdateRequest): it accepts stability, energy_cost,
      // is_bidirectional and a `status` enum — but not `is_active` or
      // `max_ship_size`, so we map/drop those before sending.
      // Drop fields the backend PUT doesn't accept: is_active (mapped to status
      // enum), max_ship_size (not in WarpTunnelUpdateRequest). The same stripped
      // object is used for both the payload and the optimistic state merge so
      // max_ship_size is never transiently shown as saved.
      // _dropSize is destructured solely to exclude max_ship_size from `rest`
      // (no varsIgnorePattern is configured for this codebase's TS override).
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      const { is_active, max_ship_size: _dropSize, ...rest } = updatedData;
      const payload: Record<string, unknown> = { ...rest };
      if (is_active !== undefined) {
        payload.status = is_active ? 'ACTIVE' : 'MAINTENANCE';
      }
      await api.put(`/api/v1/admin/warp-tunnels/${selectedTunnel.id}`, payload);
      setWarpTunnels(warpTunnels.map(t =>
        t.id === selectedTunnel.id ? { ...t, ...rest, ...(is_active !== undefined ? { is_active } : {}) } : t
      ));
      closeModal();
    } catch (err: any) {
      toast.error(`Failed to update tunnel: ${err.response?.data?.detail || err.message}`);
    } finally {
      setSaving(false);
    }
  };

  // Filter and search logic
  const filteredWarpTunnels = warpTunnels.filter(tunnel => {
    const matchesSearch = tunnel.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
                         tunnel.origin_sector_name?.toLowerCase().includes(searchTerm.toLowerCase()) ||
                         tunnel.destination_sector_name?.toLowerCase().includes(searchTerm.toLowerCase()) ||
                         `Sector ${tunnel.origin_sector_id}`.toLowerCase().includes(searchTerm.toLowerCase()) ||
                         `Sector ${tunnel.destination_sector_id}`.toLowerCase().includes(searchTerm.toLowerCase());
    
    const tunnelType = (tunnel.type || '').toUpperCase();
    const matchesFilter = filterStatus === 'all' ||
                         (filterStatus === 'active' && tunnel.is_active) ||
                         (filterStatus === 'inactive' && !tunnel.is_active) ||
                         (filterStatus === 'bidirectional' && tunnel.is_bidirectional) ||
                         (filterStatus === 'unidirectional' && !tunnel.is_bidirectional) ||
                         (filterStatus === 'natural' && tunnelType === 'NATURAL') ||
                         (filterStatus === 'artificial' && tunnelType === 'ARTIFICIAL') ||
                         (filterStatus === 'other' && tunnelType !== 'NATURAL' && tunnelType !== 'ARTIFICIAL');

    return matchesSearch && matchesFilter;
  });

  // Breakdown by type for the summary banner. NATURAL = bang-generated
  // inter-region gates and any naturally-occurring tunnels; ARTIFICIAL =
  // player-created tunnels (e.g. Warp Jumper). Anything else (STANDARD,
  // QUANTUM, ANCIENT, UNSTABLE, ONE_WAY) rolls into "Other".
  const typeCounts = warpTunnels.reduce(
    (acc, t) => {
      const k = (t.type || '').toUpperCase();
      if (k === 'NATURAL') acc.natural += 1;
      else if (k === 'ARTIFICIAL') acc.artificial += 1;
      else acc.other += 1;
      return acc;
    },
    { natural: 0, artificial: 0, other: 0 },
  );

  // Pagination
  const totalPages = Math.ceil(filteredWarpTunnels.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const paginatedWarpTunnels = filteredWarpTunnels.slice(startIndex, startIndex + itemsPerPage);

  const formatStability = (stability: number): string => {
    // Backend returns stability as a 0-1 float; convert to percentage with 1 decimal
    const pct = stability <= 1 ? stability * 100 : stability;
    return `${pct.toFixed(1)}%`;
  };

  const getStabilityColor = (stability: number) => {
    // Normalise to 0-100 for threshold comparison
    const pct = stability <= 1 ? stability * 100 : stability;
    if (pct >= 90) return 'high';
    if (pct >= 70) return 'medium';
    if (pct >= 50) return 'low';
    return 'critical';
  };

  if (loading) {
    return (
      <div className="page-container">
        <PageHeader title="Warp Tunnels Manager" subtitle="Special cross-region gates + premium / quantum / player-built tunnels. (For the in-region sector-adjacency graph players normally traverse, see the Sectors page.)" />
        <div className="loading-container">
          <div className="loading-spinner"></div>
          <p>Loading warp tunnels...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="page-container">
      <PageHeader title="Warp Tunnels Manager" subtitle="Special cross-region gates + premium / quantum / player-built tunnels. (For the in-region sector-adjacency graph players normally traverse, see the Sectors page.)" />
      
      {error && (
        <div className="error-message">
          <p>{error}</p>
          <button onClick={() => setError(null)}>×</button>
        </div>
      )}

      {/* Search and Filter Controls */}
      <div className="table-controls">
        <div className="search-section">
          <input
            type="text"
            placeholder="Search warp tunnels or sectors..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="search-input"
          />
        </div>
        
        <div className="filter-section">
          <select
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
            className="filter-select"
          >
            <option value="all">All Tunnels</option>
            <option value="natural">Natural</option>
            <option value="artificial">Artificial</option>
            <option value="other">Other (Quantum/Ancient/…)</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
            <option value="bidirectional">Bidirectional</option>
            <option value="unidirectional">Unidirectional</option>
          </select>
        </div>

        <div className="results-info">
          <span>{filteredWarpTunnels.length} of {warpTunnels.length} warp tunnels</span>
        </div>
      </div>

      {/* Type breakdown banner: makes the Natural-vs-Artificial split
          legible at a glance without clicking the filter. */}
      <div className="warp-type-banner">
        <span className="warp-type-chip warp-type-natural" title="Naturally-occurring tunnels and bang-generated inter-region gates">
          <span className="warp-type-dot" /> Natural <strong>{typeCounts.natural}</strong>
        </span>
        <span className="warp-type-chip warp-type-artificial" title="Player-created tunnels (e.g. Warp Jumper-built)">
          <span className="warp-type-dot" /> Artificial <strong>{typeCounts.artificial}</strong>
        </span>
        {typeCounts.other > 0 && (
          <span className="warp-type-chip warp-type-other" title="STANDARD / QUANTUM / ANCIENT / UNSTABLE / ONE_WAY">
            <span className="warp-type-dot" /> Other <strong>{typeCounts.other}</strong>
          </span>
        )}
      </div>

      {/* Warp Tunnels Table */}
      <div className="crud-table-container">
        <table className="crud-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Route</th>
              <th>Stability</th>
              <th>Energy Cost</th>
              <th>Travel Time</th>
              <th>Max Ship Size</th>
              <th>Direction</th>
              <th>Status</th>
              <th>Usage</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {paginatedWarpTunnels.map(tunnel => (
              <tr key={tunnel.id}>
                <td className="name-cell">
                  <strong>{tunnel.name}</strong>
                </td>
                <td>
                  <div className="route-info">
                    <span className="sector-name">{tunnel.origin_sector_name || `Sector ${tunnel.origin_sector_id}`}</span>
                    <span className="route-arrow">{tunnel.is_bidirectional ? '↔' : '→'}</span>
                    <span className="sector-name">{tunnel.destination_sector_name || `Sector ${tunnel.destination_sector_id}`}</span>
                  </div>
                </td>
                <td>
                  <span className={`stability ${getStabilityColor(tunnel.stability || 0)}`}>
                    {formatStability(tunnel.stability || 0)}
                  </span>
                </td>
                <td>{(tunnel.energy_cost || 0).toLocaleString()} units</td>
                <td>{tunnel.travel_time || 0} turns</td>
                <td>
                  <span className={`ship-size ${tunnel.max_ship_size?.toLowerCase() || 'unknown'}`}>
                    {tunnel.max_ship_size || 'Unknown'}
                  </span>
                </td>
                <td>
                  <span className={`direction ${tunnel.is_bidirectional ? 'bidirectional' : 'unidirectional'}`}>
                    {tunnel.is_bidirectional ? 'Bidirectional' : 'Unidirectional'}
                  </span>
                </td>
                <td>
                  <span className={`status ${tunnel.is_active ? 'active' : 'inactive'}`}>
                    {tunnel.is_active ? '✓ Active' : '✗ Inactive'}
                  </span>
                </td>
                <td>{(tunnel.total_traversals || 0).toLocaleString()}</td>
                <td>
                  <div className="action-buttons">
                    <button className="view-btn" type="button" onClick={() => handleViewTunnel(tunnel)}>View</button>
                    <button className="edit-btn" type="button" onClick={() => handleEditTunnel(tunnel)}>Edit</button>
                    <button className="maintenance-btn" type="button" onClick={() => handleMaintenanceToggle(tunnel)}>
                      {tunnel.is_active ? 'Maintain' : 'Activate'}
                    </button>
                    <button className="delete-btn" type="button" onClick={() => handleDeleteTunnel(tunnel)}>Delete</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
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
            disabled={currentPage === totalPages}
            className="pagination-btn"
          >
            Next
          </button>
        </div>
      )}

      {/* Tunnel View/Edit Modal */}
      {showModal && selectedTunnel && (
        <WarpTunnelModal
          tunnel={selectedTunnel}
          mode={modalMode}
          onClose={closeModal}
          onSave={handleTunnelSaved}
          saving={saving}
          formatStability={formatStability}
          getStabilityColor={getStabilityColor}
        />
      )}
    </div>
  );
};

// Warp Tunnel Modal Component for View/Edit
interface WarpTunnelModalProps {
  tunnel: WarpTunnel;
  mode: 'view' | 'edit';
  onClose: () => void;
  onSave: (data: Partial<WarpTunnel>) => void;
  saving: boolean;
  formatStability: (s: number) => string;
  getStabilityColor: (s: number) => string;
}

const WarpTunnelModal: React.FC<WarpTunnelModalProps> = ({ tunnel, mode, onClose, onSave, saving, formatStability, getStabilityColor: _getStabilityColor }) => {
  const [formData, setFormData] = useState({
    stability: tunnel.stability || 0,
    energy_cost: tunnel.energy_cost || 0,
    is_active: tunnel.is_active ?? true,
    is_bidirectional: tunnel.is_bidirectional ?? false,
  });

  const isReadOnly = mode === 'view';

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (isReadOnly) return;
    onSave(formData);
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>{mode === 'view' ? 'View' : 'Edit'} Tunnel: {tunnel.name}</h2>
          <button className="close-btn" onClick={onClose}>x</button>
        </div>

        <form onSubmit={handleSubmit} className="modal-form">
          <div className="form-grid">
            <div className="form-group">
              <label>Tunnel Name</label>
              <input type="text" value={tunnel.name} readOnly />
            </div>

            <div className="form-group">
              <label>Type</label>
              <input type="text" value={tunnel.type || 'Standard'} readOnly />
            </div>

            <div className="form-group">
              <label>Origin Sector</label>
              <input type="text" value={tunnel.origin_sector_name || `Sector ${tunnel.origin_sector_id}`} readOnly />
            </div>

            <div className="form-group">
              <label>Destination Sector</label>
              <input type="text" value={tunnel.destination_sector_name || `Sector ${tunnel.destination_sector_id}`} readOnly />
            </div>

            <div className="form-group">
              <label>Stability</label>
              {isReadOnly ? (
                <input type="text" value={formatStability(tunnel.stability || 0)} readOnly />
              ) : (
                <input
                  type="number"
                  min="0"
                  max="1"
                  step="0.01"
                  value={formData.stability}
                  onChange={(e) => setFormData({ ...formData, stability: parseFloat(e.target.value) })}
                />
              )}
            </div>

            <div className="form-group">
              <label>Energy Cost</label>
              <input
                type="number"
                value={formData.energy_cost}
                onChange={(e) => setFormData({ ...formData, energy_cost: parseInt(e.target.value) })}
                readOnly={isReadOnly}
                min="0"
              />
            </div>

            <div className="form-group">
              <label>Max Ship Size</label>
              {/* Display-only: WarpTunnelUpdateRequest has no max_ship_size */}
              <input
                type="text"
                value={tunnel.max_ship_size || 'Unknown'}
                readOnly
                title="Not editable — backend update contract omits this field"
              />
            </div>

            <div className="form-group">
              <label>Active</label>
              {isReadOnly ? (
                <input type="text" value={tunnel.is_active ? 'Yes' : 'No'} readOnly />
              ) : (
                <select
                  value={formData.is_active ? 'true' : 'false'}
                  onChange={(e) => setFormData({ ...formData, is_active: e.target.value === 'true' })}
                >
                  <option value="true">Active</option>
                  <option value="false">Inactive (Maintenance)</option>
                </select>
              )}
            </div>
          </div>

          {/* Additional info section (view mode) */}
          <div className="port-info">
            <h3>Tunnel Information</h3>
            <div className="info-grid">
              <div className="info-item">
                <span className="label">Direction:</span>
                <span className="value">{tunnel.is_bidirectional ? 'Bidirectional' : 'Unidirectional'}</span>
              </div>
              <div className="info-item">
                <span className="label">Travel Time:</span>
                <span className="value">{tunnel.travel_time || 0} turns</span>
              </div>
              <div className="info-item">
                <span className="label">Total Traversals:</span>
                <span className="value">{(tunnel.total_traversals || 0).toLocaleString()}</span>
              </div>
              <div className="info-item">
                <span className="label">Created:</span>
                <span className="value">{tunnel.created_at ? new Date(tunnel.created_at).toLocaleDateString() : 'Unknown'}</span>
              </div>
            </div>
          </div>

          <div className="modal-actions">
            <button type="button" onClick={onClose} className="cancel-btn">
              {isReadOnly ? 'Close' : 'Cancel'}
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

export default WarpTunnelsManager;