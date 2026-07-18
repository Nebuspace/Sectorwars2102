import React, { useState, useEffect, useCallback } from 'react';
import PageHeader from '../ui/PageHeader';
import { api } from '../../utils/auth';
import FleetHealthReport from '../charts/FleetHealthReport';
import FleetOperationsTab from '../fleet/FleetOperationsTab';
import { useToast, useConfirm } from '../../contexts/ToastContext';
import './fleet-management.css';

interface Ship {
  id: string;
  name: string;
  ship_type: string;
  owner_id: string;
  owner_name: string;
  current_sector_id: number;
  maintenance_rating: number;
  cargo_used: number;
  cargo_capacity: number;
  is_active: boolean;
  created_at: string;
}

interface ShipFormData {
  name: string;
  ship_type: string;
  owner_id: string;
  current_sector_id: number;
}

interface Player {
  id: string;
  username: string;
}

interface FleetStats {
  total_ships: number;
  ships_by_type: { [key: string]: number };
  average_maintenance: number;
  inactive_ships: number;
  total_cargo_capacity: number;
}

const SHIP_TYPES = [
  'LIGHT_FREIGHTER',
  'MEDIUM_FREIGHTER', 
  'HEAVY_FREIGHTER',
  'BATTLESHIP',
  'CRUISER',
  'DESTROYER',
  'FIGHTER'
];

const FleetManagement: React.FC = () => {
  const toast = useToast();
  const confirm = useConfirm();
  const [ships, setShips] = useState<Ship[]>([]);
  const [players, setPlayers] = useState<Player[]>([]);
  const [stats, setStats] = useState<FleetStats | null>(null);
  const [selectedShip, setSelectedShip] = useState<Ship | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  // Filters
  const [searchTerm, setSearchTerm] = useState('');
  const [typeFilter, setTypeFilter] = useState<string>('all');
  const [ownerFilter, setOwnerFilter] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [sectorFilter, setSectorFilter] = useState<string>('');
  
  // Pagination
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalCount, setTotalCount] = useState(0);
  const limit = 50;
  
  // Forms
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [showEditForm, setShowEditForm] = useState(false);
  const [showTeleportForm, setShowTeleportForm] = useState(false);
  const [formData, setFormData] = useState<ShipFormData>({
    name: '',
    ship_type: SHIP_TYPES[0],
    owner_id: '',
    current_sector_id: 1
  });
  const [teleportSector, setTeleportSector] = useState<number>(1);

  const fetchShips = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      
      const params = new URLSearchParams({
        page: page.toString(),
        limit: limit.toString()
      });
      
      if (typeFilter !== 'all') params.append('filter_type', typeFilter);
      if (ownerFilter) params.append('filter_owner', ownerFilter);
      if (sectorFilter) params.append('filter_sector', sectorFilter);
      
      const response = await api.get(`/api/v1/admin/ships/comprehensive?${params}`);
      const data = response.data as any;
      
      setShips(data.ships || []);
      setTotalCount(data.total_count || 0);
      setTotalPages(data.total_pages || 1);
      
      // Calculate stats
      if (data.ships && data.ships.length > 0) {
        const shipsByType: { [key: string]: number } = {};
        let totalMaintenance = 0;
        let inactiveCount = 0;
        let totalCargo = 0;
        
        data.ships.forEach((ship: Ship) => {
          shipsByType[ship.ship_type] = (shipsByType[ship.ship_type] || 0) + 1;
          totalMaintenance += ship.maintenance_rating;
          if (!ship.is_active) inactiveCount++;
          totalCargo += ship.cargo_capacity;
        });
        
        setStats({
          total_ships: data.ships.length,
          ships_by_type: shipsByType,
          average_maintenance: totalMaintenance / data.ships.length,
          inactive_ships: inactiveCount,
          total_cargo_capacity: totalCargo
        });
      }
      
    } catch (error) {
      console.error('Error fetching ships:', error);
      setError('Failed to fetch fleet data');
      setShips([]);
      setStats(null);
    } finally {
      setLoading(false);
    }
  }, [page, typeFilter, ownerFilter, sectorFilter]);

  const fetchPlayers = useCallback(async () => {
    try {
      const response = await api.get('/api/v1/admin/players/comprehensive?limit=1000');
      const data = response.data as any;
      setPlayers(data.players || []);
    } catch (error) {
      console.error('Error fetching players:', error);
    }
  }, []);

  useEffect(() => {
    fetchShips();
  }, [fetchShips]);

  useEffect(() => {
    fetchPlayers();
  }, [fetchPlayers]);

  const handleCreateShip = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.post('/api/v1/admin/ships', formData);
      setShowCreateForm(false);
      setFormData({
        name: '',
        ship_type: SHIP_TYPES[0],
        owner_id: '',
        current_sector_id: 1
      });
      fetchShips();
    } catch (error) {
      console.error('Error creating ship:', error);
      toast.error('Failed to create ship');
    }
  };

  const handleUpdateShip = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedShip) return;
    
    try {
      await api.put(`/api/v1/admin/ships/${selectedShip.id}`, formData);
      setShowEditForm(false);
      setSelectedShip(null);
      fetchShips();
    } catch (error) {
      console.error('Error updating ship:', error);
      toast.error('Failed to update ship');
    }
  };

  const handleDeleteShip = async (shipId: string) => {
    if (!(await confirm({
      title: 'Delete Ship',
      message: 'Are you sure you want to delete this ship? This action cannot be undone.',
      danger: true,
      confirmLabel: 'Delete',
    }))) {
      return;
    }

    try {
      await api.delete(`/api/v1/admin/ships/${shipId}`);
      fetchShips();
    } catch (error) {
      console.error('Error deleting ship:', error);
      toast.error('Failed to delete ship');
    }
  };

  const handleTeleportShip = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedShip) return;
    
    try {
      await api.post(`/api/v1/admin/ships/${selectedShip.id}/teleport`, {
        target_sector_id: teleportSector
      });
      setShowTeleportForm(false);
      setSelectedShip(null);
      setTeleportSector(1);
      fetchShips();
    } catch (error) {
      console.error('Error teleporting ship:', error);
      toast.error('Failed to teleport ship');
    }
  };

  const openEditForm = (ship: Ship) => {
    setSelectedShip(ship);
    setFormData({
      name: ship.name,
      ship_type: ship.ship_type,
      owner_id: ship.owner_id,
      current_sector_id: ship.current_sector_id
    });
    setShowEditForm(true);
  };

  const openTeleportForm = (ship: Ship) => {
    setSelectedShip(ship);
    setTeleportSector(ship.current_sector_id);
    setShowTeleportForm(true);
  };

  const filteredShips = ships.filter(ship => {
    const matchesSearch = ship.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
                         ship.owner_name.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesStatus = statusFilter === 'all' || 
                         (statusFilter === 'active' && ship.is_active) ||
                         (statusFilter === 'inactive' && !ship.is_active);
    
    return matchesSearch && matchesStatus;
  });

  if (loading && ships.length === 0) {
    return (
      <div className="page-container">
        <PageHeader 
          title="Fleet Management" 
          subtitle="Manage ships across the galaxy"
        />
        <div className="page-content">
          <div className="loading-container text-center py-12">
            <div className="loading-spinner mx-auto mb-4"></div>
            <span>Loading fleet data...</span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="page-container">
      <PageHeader 
        title="Fleet Management" 
        subtitle="Manage ships across the galaxy"
      />
      
      <div className="page-content">
        {error && (
          <div className="alert alert-error mb-6">
            <div className="flex items-center gap-3">
              <span>⚠️</span>
              <span className="flex-1">{error}</span>
              <button onClick={fetchShips} className="btn btn-sm">Retry</button>
            </div>
          </div>
        )}
        
        {/* Fleet Controls */}
        <section className="section">
          <div className="section-header">
            <h3 className="section-title">🛸 Fleet Management</h3>
            <p className="section-subtitle">Search, filter, and manage all ships</p>
          </div>
          
          <div className="card">
            <div className="card-body">
              {/* Search and Filters */}
              <div className="flex flex-wrap items-center gap-4 mb-6">
                <div className="flex-1 min-w-64">
                  <input
                    type="text"
                    className="form-input"
                    placeholder="Search ships by name or owner..."
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                  />
                </div>
                
                <select 
                  value={typeFilter} 
                  onChange={(e) => setTypeFilter(e.target.value)}
                  className="form-select"
                >
                  <option value="all">All Types</option>
                  {SHIP_TYPES.map(type => (
                    <option key={type} value={type}>{type.replace('_', ' ')}</option>
                  ))}
                </select>
                
                <input
                  type="text"
                  className="form-input"
                  placeholder="Filter by owner..."
                  value={ownerFilter}
                  onChange={(e) => setOwnerFilter(e.target.value)}
                />
                
                <input
                  type="number"
                  className="form-input"
                  placeholder="Filter by sector..."
                  value={sectorFilter}
                  onChange={(e) => setSectorFilter(e.target.value)}
                />
                
                <select 
                  value={statusFilter} 
                  onChange={(e) => setStatusFilter(e.target.value)}
                  className="form-select"
                >
                  <option value="all">All Status</option>
                  <option value="active">Active</option>
                  <option value="inactive">Inactive</option>
                </select>
                
                <button 
                  onClick={() => setShowCreateForm(true)}
                  className="btn btn-primary"
                >
                  + Create Ship
                </button>
                
                <button onClick={fetchShips} className="btn btn-outline">
                  🔄 Refresh
                </button>
              </div>

              {/* Ships Table */}
              <div className="table-container">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Type</th>
                      <th>Owner</th>
                      <th>Sector</th>
                      <th>Maintenance</th>
                      <th>Cargo</th>
                      <th>Status</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredShips.map(ship => (
                      <tr key={ship.id} className={!ship.is_active ? 'opacity-50' : ''}>
                        <td className="font-medium">{ship.name}</td>
                        <td>{ship.ship_type.replace('_', ' ')}</td>
                        <td>{ship.owner_name}</td>
                        <td>{ship.current_sector_id}</td>
                        <td>
                          <div className="flex items-center gap-2">
                            <div className="w-16 h-2 bg-gray-200 rounded-full overflow-hidden">
                              <div 
                                className={`h-full rounded-full ${
                                  ship.maintenance_rating < 30 ? 'bg-red-500' :
                                  ship.maintenance_rating < 70 ? 'bg-yellow-500' : 'bg-green-500'
                                }`}
                                style={{ width: `${ship.maintenance_rating}%` }}
                              />
                            </div>
                            <span className="text-sm">{ship.maintenance_rating.toFixed(1)}%</span>
                          </div>
                        </td>
                        <td className="font-mono text-sm">{ship.cargo_used} / {ship.cargo_capacity}</td>
                        <td>
                          <span className={`badge ${ship.is_active ? 'badge-success' : 'badge-secondary'}`}>
                            {ship.is_active ? 'Active' : 'Inactive'}
                          </span>
                        </td>
                        <td>
                          <div className="flex items-center gap-1">
                            <button 
                              onClick={() => openEditForm(ship)}
                              className="btn btn-xs btn-outline"
                              title="Edit Ship"
                              aria-label="Edit Ship"
                            >
                              ✏️
                            </button>
                            <button 
                              onClick={() => openTeleportForm(ship)}
                              className="btn btn-xs btn-outline"
                              title="Teleport Ship"
                              aria-label="Teleport Ship"
                            >
                              🌀
                            </button>
                            <button 
                              onClick={() => handleDeleteShip(ship.id)}
                              className="btn btn-xs btn-error"
                              title="Delete Ship"
                              aria-label="Delete Ship"
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
              
              {/* Pagination */}
              {totalPages > 1 && (
                <div className="pagination">
                  <button 
                    onClick={() => setPage(page - 1)} 
                    disabled={page === 1}
                    className="btn btn-sm btn-outline"
                  >
                    ← Previous
                  </button>
                  <div className="text-sm">
                    Page {page} of {totalPages} ({totalCount} ships)
                  </div>
                  <button 
                    onClick={() => setPage(page + 1)} 
                    disabled={page === totalPages}
                    className="btn btn-sm btn-outline"
                  >
                    Next →
                  </button>
                </div>
              )}
            </div>
          </div>
        </section>

        {/* Fleet Statistics */}
        {stats && (
          <section className="section">
            <div className="section-header">
              <h3 className="section-title">🚀 Fleet Statistics</h3>
              <p className="section-subtitle">Secondary overview — primary ship actions are above (Scroll-Law)</p>
            </div>
            
            <div className="grid grid-auto-fit gap-6">
              <div className="dashboard-stat-card">
                <div className="dashboard-stat-header">
                  <span className="dashboard-stat-icon">🚀</span>
                  <h4 className="dashboard-stat-title">Total Ships</h4>
                </div>
                <div className="dashboard-stat-value">{totalCount.toLocaleString()}</div>
                <div className="dashboard-stat-description">All ships in galaxy</div>
              </div>
              
              <div className="dashboard-stat-card">
                <div className="dashboard-stat-header">
                  <span className="dashboard-stat-icon">🔧</span>
                  <h4 className="dashboard-stat-title">Avg Maintenance</h4>
                </div>
                <div className="dashboard-stat-value">{stats.average_maintenance.toFixed(1)}%</div>
                <div className="dashboard-stat-description">Of {stats.total_ships} ships on this page</div>
              </div>
              
              <div className="dashboard-stat-card stat-warning">
                <div className="dashboard-stat-header">
                  <span className="dashboard-stat-icon">⚠️</span>
                  <h4 className="dashboard-stat-title">Inactive Ships</h4>
                </div>
                <div className="dashboard-stat-value">{stats.inactive_ships}</div>
                <div className="dashboard-stat-description">Of {stats.total_ships} ships on this page</div>
              </div>
              
              <div className="dashboard-stat-card">
                <div className="dashboard-stat-header">
                  <span className="dashboard-stat-icon">📦</span>
                  <h4 className="dashboard-stat-title">Total Cargo</h4>
                </div>
                <div className="dashboard-stat-value">{stats.total_cargo_capacity.toLocaleString()}</div>
                <div className="dashboard-stat-description">Of {stats.total_ships} ships on this page</div>
              </div>
            </div>
          </section>
        )}

        {/* Fleet Health Report (rescued: GET /admin/ships/health-report) */}
        <section className="section">
          <div className="section-header">
            <h3 className="section-title">🩺 Fleet Health</h3>
            <p className="section-subtitle">Condition and status analysis across the entire fleet</p>
          </div>
          <div className="card">
            <div className="card-body">
              <FleetHealthReport />
            </div>
          </div>
        </section>

        {/* Fleet Operations (admin fleet/battle endpoints: /admin/fleets/*) */}
        <section className="section">
          <div className="section-header">
            <h3 className="section-title">⚔️ Fleet Operations</h3>
            <p className="section-subtitle">Active fleets, recent battles, and live admin battle interventions</p>
          </div>
          <FleetOperationsTab />
        </section>
      </div>

      {/* Create Ship Modal */}
      {showCreateForm && (
        <div className="modal-overlay" onClick={() => setShowCreateForm(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3 className="modal-title">Create New Ship</h3>
              <button onClick={() => setShowCreateForm(false)} className="btn btn-sm btn-ghost">×</button>
            </div>
            <div className="modal-body">
              <form onSubmit={handleCreateShip} className="space-y-4">
                <div className="form-group">
                  <label className="form-label">Ship Name</label>
                  <input
                    type="text"
                    className="form-input"
                    value={formData.name}
                    onChange={(e) => setFormData({...formData, name: e.target.value})}
                    required
                  />
                </div>
                
                <div className="form-group">
                  <label className="form-label">Ship Type</label>
                  <select
                    className="form-select"
                    value={formData.ship_type}
                    onChange={(e) => setFormData({...formData, ship_type: e.target.value})}
                    required
                  >
                    {SHIP_TYPES.map(type => (
                      <option key={type} value={type}>{type.replace('_', ' ')}</option>
                    ))}
                  </select>
                </div>
                
                <div className="form-group">
                  <label className="form-label">Owner</label>
                  <select
                    className="form-select"
                    value={formData.owner_id}
                    onChange={(e) => setFormData({...formData, owner_id: e.target.value})}
                    required
                  >
                    <option value="">Select Player</option>
                    {players.map(player => (
                      <option key={player.id} value={player.id}>{player.username}</option>
                    ))}
                  </select>
                </div>
                
                <div className="form-group">
                  <label className="form-label">Starting Sector</label>
                  <input
                    type="number"
                    className="form-input"
                    min="1"
                    value={formData.current_sector_id}
                    onChange={(e) => setFormData({...formData, current_sector_id: parseInt(e.target.value)})}
                    required
                  />
                </div>
                
                <div className="modal-footer">
                  <button type="button" onClick={() => setShowCreateForm(false)} className="btn btn-outline">
                    Cancel
                  </button>
                  <button type="submit" className="btn btn-primary">
                    Create Ship
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}
      
      {/* Edit Ship Modal */}
      {showEditForm && selectedShip && (
        <div className="modal-overlay" onClick={() => setShowEditForm(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3 className="modal-title">Edit Ship: {selectedShip.name}</h3>
              <button onClick={() => setShowEditForm(false)} className="btn btn-sm btn-ghost">×</button>
            </div>
            <div className="modal-body">
              <form onSubmit={handleUpdateShip} className="space-y-4">
                <div className="form-group">
                  <label className="form-label">Ship Name</label>
                  <input
                    type="text"
                    className="form-input"
                    value={formData.name}
                    onChange={(e) => setFormData({...formData, name: e.target.value})}
                    required
                  />
                </div>
                
                <div className="form-group">
                  <label className="form-label">Owner</label>
                  <select
                    className="form-select"
                    value={formData.owner_id}
                    onChange={(e) => setFormData({...formData, owner_id: e.target.value})}
                    required
                  >
                    {players.map(player => (
                      <option key={player.id} value={player.id}>{player.username}</option>
                    ))}
                  </select>
                </div>
                
                <div className="form-group">
                  <label className="form-label">Current Sector</label>
                  <input
                    type="number"
                    className="form-input"
                    min="1"
                    value={formData.current_sector_id}
                    onChange={(e) => setFormData({...formData, current_sector_id: parseInt(e.target.value)})}
                    required
                  />
                </div>
                
                <div className="modal-footer">
                  <button type="button" onClick={() => setShowEditForm(false)} className="btn btn-outline">
                    Cancel
                  </button>
                  <button type="submit" className="btn btn-primary">
                    Update Ship
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}
      
      {/* Teleport Ship Modal */}
      {showTeleportForm && selectedShip && (
        <div className="modal-overlay" onClick={() => setShowTeleportForm(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3 className="modal-title">Teleport Ship: {selectedShip.name}</h3>
              <button onClick={() => setShowTeleportForm(false)} className="btn btn-sm btn-ghost">×</button>
            </div>
            <div className="modal-body">
              <form onSubmit={handleTeleportShip} className="space-y-4">
                <div className="alert alert-info">
                  <div>
                    <strong>Current Sector:</strong> {selectedShip.current_sector_id}
                  </div>
                </div>
                
                <div className="form-group">
                  <label className="form-label">Target Sector</label>
                  <input
                    type="number"
                    className="form-input"
                    min="1"
                    value={teleportSector}
                    onChange={(e) => setTeleportSector(parseInt(e.target.value))}
                    required
                  />
                </div>
                
                <div className="modal-footer">
                  <button type="button" onClick={() => setShowTeleportForm(false)} className="btn btn-outline">
                    Cancel
                  </button>
                  <button type="submit" className="btn btn-warning">
                    Teleport Ship
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default FleetManagement;