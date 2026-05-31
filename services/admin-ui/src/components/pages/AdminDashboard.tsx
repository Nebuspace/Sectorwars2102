import React, { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { useAdmin } from '../../contexts/AdminContext';
import { useAuth } from '../../contexts/AuthContext';

const AdminDashboard: React.FC = () => {
  const { user } = useAuth();
  const {
    adminStats,
    galaxyState,
    regions,
    users,
    players,
    loadAdminStats,
    loadGalaxyInfo,
    loadRegions,
    loadUsers,
    loadPlayers,
    isLoading,
    error
  } = useAdmin();
  
  useEffect(() => {
    // Only load data if user is authenticated and is admin
    if (user && user.is_admin) {
      loadAdminStats();
      loadGalaxyInfo();
      loadUsers();
      loadPlayers();
    }
  }, [user, loadAdminStats, loadGalaxyInfo, loadUsers, loadPlayers]);
  
  // Load regions when galaxy info is loaded
  useEffect(() => {
    if (galaxyState) {
      loadRegions();
    }
  }, [galaxyState, loadRegions]);
  
  return (
    <div className="page-container">
      <div className="page-header">
        <h1 className="page-title">Universe Administration</h1>
        <p className="page-subtitle">Welcome, {user?.username}</p>
      </div>
      
      {error && (
        <div className="alert alert-error">
          {error}
        </div>
      )}
      
      {isLoading ? (
        <div className="flex flex-col items-center justify-center p-8">
          <div className="loading-spinner mb-4"></div>
          <p className="text-secondary">Loading dashboard data...</p>
        </div>
      ) : (
        <div className="page-content">
          <section className="section">
            <div className="section-header">
              <div>
                <h3 className="section-title">System Statistics</h3>
                <p className="section-subtitle">Overview of system resources and activity</p>
              </div>
            </div>
            <div className="grid grid-auto-fit gap-6">
              <div className="dashboard-stat-card">
                <div className="dashboard-stat-header">
                  <span className="dashboard-stat-icon">👥</span>
                  <h4 className="dashboard-stat-title">Users</h4>
                </div>
                <div className="dashboard-stat-value">{adminStats?.totalUsers ?? '...'}</div>
              </div>
              <div className="dashboard-stat-card">
                <div className="dashboard-stat-header">
                  <span className="dashboard-stat-icon">🎮</span>
                  <h4 className="dashboard-stat-title">Active Players</h4>
                </div>
                <div className="dashboard-stat-value">{adminStats?.activePlayers ?? '...'}</div>
              </div>
              <div className="dashboard-stat-card">
                <div className="dashboard-stat-header">
                  <span className="dashboard-stat-icon">🌌</span>
                  <h4 className="dashboard-stat-title">Sectors</h4>
                </div>
                <div className="dashboard-stat-value">{galaxyState?.statistics?.total_sectors ?? '...'}</div>
              </div>
              <div className="dashboard-stat-card">
                <div className="dashboard-stat-header">
                  <span className="dashboard-stat-icon">🪐</span>
                  <h4 className="dashboard-stat-title">Planets</h4>
                </div>
                <div className="dashboard-stat-value">{galaxyState?.statistics?.planet_count ?? '...'}</div>
              </div>
              <div className="dashboard-stat-card">
                <div className="dashboard-stat-header">
                  <span className="dashboard-stat-icon">🚀</span>
                  <h4 className="dashboard-stat-title">Ships</h4>
                </div>
                <div className="dashboard-stat-value">{adminStats?.totalShips ?? '...'}</div>
              </div>
              <div className="dashboard-stat-card">
                <div className="dashboard-stat-header">
                  <span className="dashboard-stat-icon">🟢</span>
                  <h4 className="dashboard-stat-title">Sessions</h4>
                </div>
                <div className="dashboard-stat-value">{adminStats?.playerSessions ?? '...'}</div>
              </div>
            </div>
          </section>
          
          <section className="section">
            <div className="section-header">
              <div>
                <h3 className="section-title">Galaxy Overview</h3>
                <p className="section-subtitle">Current galaxy state and statistics</p>
              </div>
            </div>
            {galaxyState ? (
              <div className="card">
                <div className="card-header">
                  <h4 className="card-title">{galaxyState.name}</h4>
                  <p className="card-subtitle">
                    Age: {galaxyState.state.age_in_days} days
                  </p>
                </div>
                <div className="card-body">
                  <div className="grid grid-cols-3 gap-6 mb-6">
                    <div className="text-center">
                      <div className="text-2xl font-bold text-primary">{galaxyState.statistics.total_sectors}</div>
                      <div className="text-sm text-tertiary">Total Sectors</div>
                    </div>
                    <div className="text-center">
                      <div className="text-2xl font-bold text-primary">
                        {galaxyState.statistics.discovered_sectors} 
                      </div>
                      <div className="text-sm text-tertiary">
                        Discovered ({Math.round(galaxyState.state.exploration_percentage)}%)
                      </div>
                    </div>
                    <div className="text-center">
                      <div className="text-2xl font-bold text-primary">
                        {galaxyState.state.economic_health}/100
                      </div>
                      <div className="text-sm text-tertiary">Economic Health</div>
                    </div>
                  </div>
                  
                  <div className="mb-6">
                    <h5 className="font-semibold text-primary mb-3">Regions ({regions.length})</h5>
                    <div className="grid grid-cols-1 gap-2">
                      {regions.map((region: any) => (
                        <div key={region.id} className="flex justify-between items-center p-3 bg-secondary rounded">
                          <div>
                            <div className="font-medium">{region.display_name}</div>
                            <div className="text-sm text-tertiary">
                              {region.total_sectors} sectors • {region.region_type}
                            </div>
                          </div>
                          <span className={`badge ${
                            region.region_type === 'central_nexus' ? 'badge-primary' :
                            region.region_type === 'terran_space' ? 'badge-info' :
                            'badge-success'
                          }`}>
                            {region.region_type === 'central_nexus' ? 'Central Nexus' :
                             region.region_type === 'terran_space' ? 'Terran Space' :
                             'Player Region'}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <div className="card">
                <div className="card-body text-center">
                  <p className="text-secondary mb-4">No galaxy has been generated yet.</p>
                  <Link to="/universe/bang" className="btn btn-primary">Generate Galaxy</Link>
                </div>
              </div>
            )}
          </section>
          
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <section className="section">
              <div className="section-header">
                <div>
                  <h3 className="section-title">User Management</h3>
                  <p className="section-subtitle">Account statistics and recent activity</p>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-4 mb-6">
                <div className="text-center">
                  <div className="text-xl font-bold text-primary">{users.length}</div>
                  <div className="text-xs text-tertiary">Total</div>
                </div>
                <div className="text-center">
                  <div className="text-xl font-bold text-primary">{users.filter((u: any) => u.is_active).length}</div>
                  <div className="text-xs text-tertiary">Active</div>
                </div>
                <div className="text-center">
                  <div className="text-xl font-bold text-primary">{users.filter((u: any) => u.is_admin).length}</div>
                  <div className="text-xs text-tertiary">Admins</div>
                </div>
              </div>
              
              <div>
                <h4 className="font-semibold text-primary mb-3">Recent Registrations</h4>
                <div className="space-y-2">
                  {users.slice(0, 5).map((user: any) => (
                    <div key={user.id} className="flex justify-between items-center p-3 bg-secondary rounded">
                      <div>
                        <div className="font-medium">{user.username}</div>
                        <div className="text-sm text-tertiary">{user.email}</div>
                      </div>
                      <div className="flex gap-2">
                        <span className={`badge ${user.is_active ? 'badge-success' : 'badge-gray'}`}>
                          {user.is_active ? 'Active' : 'Inactive'}
                        </span>
                        {user.is_admin && <span className="badge badge-primary">Admin</span>}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </section>
            
            <section className="section">
              <div className="section-header">
                <div>
                  <h3 className="section-title">Player Statistics</h3>
                  <p className="section-subtitle">Active players and their progress</p>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-4 mb-6">
                <div className="text-center">
                  <div className="text-xl font-bold text-primary">{players.length}</div>
                  <div className="text-xs text-tertiary">Players</div>
                </div>
                <div className="text-center">
                  <div className="text-xl font-bold text-primary">
                    {players.reduce((total: number, player: any) => total + (player.ship_count || 0), 0)}
                  </div>
                  <div className="text-xs text-tertiary">Ships</div>
                </div>
                <div className="text-center">
                  <div className="text-xl font-bold text-primary">
                    {players.reduce((total: number, player: any) => total + (player.credits || 0), 0).toLocaleString()}
                  </div>
                  <div className="text-xs text-tertiary">Credits</div>
                </div>
              </div>
              
              <div>
                <h4 className="font-semibold text-primary mb-3">Active Players</h4>
                <div className="table-container">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Player</th>
                        <th>Level</th>
                        <th>Credits</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {players.slice(0, 5).map((player: any) => (
                        <tr key={player.id}>
                          <td>{player.name}</td>
                          <td>{player.level}</td>
                          <td>{player.credits?.toLocaleString()}</td>
                          <td>
                            <button className="btn btn-secondary btn-sm">View</button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </section>
          </div>
          
          <section className="section">
            <div className="section-header">
              <div>
                <h3 className="section-title">Administrative Actions</h3>
                <p className="section-subtitle">Quick access to common admin functions</p>
              </div>
            </div>
            <div className="grid grid-auto-fit gap-4">
              <button className="btn btn-primary">Universe Management</button>
              <button className="btn btn-secondary">Generate Planet</button>
              <button className="btn btn-secondary">Create Port</button>
              <button className="btn btn-secondary">Player Lookup</button>
              <button className="btn btn-secondary">System Logs</button>
              <button className="btn btn-danger">Emergency Reset</button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
};

export default AdminDashboard;