import React, { useState, useEffect, useCallback, useMemo } from 'react';
import PageHeader from '../ui/PageHeader';
import PlayerSearchAndFilter from './components/PlayerSearchAndFilter';
import PlayerDetailEditor from '../admin/PlayerDetailEditor';
import BulkOperationPanel from '../admin/BulkOperationPanel';
import PlayerAssetManager from '../admin/PlayerAssetManager';
import EmergencyOperationsPanel from '../admin/EmergencyOperationsPanel';
import { api } from '../../utils/auth';
import {
  PlayerModel,
  PlayerFilters,
  PlayerAnalyticsState
} from '../../types/playerManagement';
import './player-analytics.css';

const PlayerAnalytics: React.FC = () => {
  const [state, setState] = useState<PlayerAnalyticsState>({
    players: [],
    selectedPlayer: null,
    totalCount: 0,
    currentPage: 1,
    metrics: null,
    editMode: false,
    unsavedChanges: false,
    loading: true,
    errors: [],
    filters: {
      search: '',
      status: 'all',
      team: null,
      minCredits: null,
      maxCredits: null,
      lastLoginAfter: null,
      lastLoginBefore: null,
      reputationFilter: null,
      hasShips: null,
      hasPlanets: null,
      hasPorts: null,
      onlineOnly: false,
      suspiciousActivity: false
    },
    sortBy: 'credits',
    sortOrder: 'desc',
    pageSize: 20,
    realTimeUpdates: false,
    selectedPlayers: [],
    showBulkOperations: false,
    showEmergencyOps: false,
    showAssetManager: false,
    showActivityMonitor: false
  });

  // Column visibility state
  const [visibleColumns, setVisibleColumns] = useState({
    player: true,
    status: true,
    credits: true,
    assets: true,
    location: false, // Hidden by default for space
    activity: false, // Hidden by default for space
    lastLogin: true,
    turns: true,
    actions: true
  });

  // Regions for location display
  const [regions, setRegions] = useState<any[]>([]);

  // Whether the real-time analytics endpoint responded; when false, the
  // analytics-backed metric cards render demoted (no invented substitutes).
  const [analyticsAvailable, setAnalyticsAvailable] = useState(true);

  useEffect(() => {
    fetchPlayerData();
    fetchRegions();
  }, [state.currentPage, state.filters, state.sortBy, state.sortOrder]);

  // Auto-refresh when real-time updates are enabled
  useEffect(() => {
    if (state.realTimeUpdates) {
      const interval = setInterval(fetchPlayerData, 30000); // 30 seconds
      return () => clearInterval(interval);
    }
  }, [state.realTimeUpdates]);

  const fetchPlayerData = useCallback(async () => {
    try {
      setState(prev => ({ ...prev, loading: true, errors: [] }));
      
      // Build query parameters
      const params = new URLSearchParams({
        page: state.currentPage.toString(),
        limit: state.pageSize.toString(),
        sort_by: state.sortBy,
        sort_order: state.sortOrder,
        include_assets: 'true',
        include_activity: 'true'
      });
      
      // Add filters
      if (state.filters.search) params.append('search', state.filters.search);
      if (state.filters.status !== 'all') params.append('filter_status', state.filters.status);
      if (state.filters.team) params.append('filter_team', state.filters.team);
      if (state.filters.minCredits) params.append('min_credits', state.filters.minCredits.toString());
      if (state.filters.maxCredits) params.append('max_credits', state.filters.maxCredits.toString());
      if (state.filters.onlineOnly) params.append('online_only', 'true');
      if (state.filters.suspiciousActivity) params.append('suspicious_only', 'true');
      
      const response = await api.get(`/api/v1/admin/players/comprehensive?${params}`);
      const rawData = response.data as any;
      
      // Transform the API response to match our expected format
      const transformedPlayers = (rawData.players || []).map((player: any) => ({
        ...player,
        status: player.is_active ? 'active' : 'inactive',
        assets: {
          ships_count: player.ships_count || 0,
          planets_count: player.planets_count || 0,
          stations_count: player.stations_count || 0,
          total_value: 0 // Will be calculated later
        },
        activity: {
          last_login: player.last_login || player.created_at,
          session_count_today: 0,
          actions_today: 0,
          total_trade_volume: 0,
          combat_rating: 0,
          suspicious_activity: false
        }
      }));
      
      // Fetch real-time analytics separately. The endpoint wraps its payload
      // in a {success, data, timestamp} envelope, so the metrics live at
      // response.data.data. On failure the analytics-backed cards are demoted
      // rather than silently substituting page-local (current page only) sums.
      let analyticsData: any = {};
      let analyticsOk = true;
      try {
        const analyticsResponse = await api.get('/api/v1/admin/analytics/real-time');
        analyticsData = (analyticsResponse.data as any)?.data ?? {};
      } catch (analyticsError) {
        console.warn('Analytics API unavailable:', analyticsError);
        analyticsOk = false;
      }
      setAnalyticsAvailable(analyticsOk);

      setState(prev => ({
        ...prev,
        players: transformedPlayers,
        totalCount: rawData.total_count || transformedPlayers.length,
        metrics: {
          total_active_players: analyticsData.total_active_players || 0,
          total_credits_circulation: analyticsData.total_credits_circulation || 0,
          average_session_time: analyticsData.average_session_time || 0,
          new_players_today: analyticsData.new_players_today || 0,
          player_retention_rate: analyticsData.player_retention_rate_7d || 0,
          players_online_now: analyticsData.players_online_now || 0,
          total_players: analyticsData.total_players || 0,
          banned_players: transformedPlayers.filter((p: any) => p.status === 'banned').length,
          suspicious_activity_alerts: analyticsData.suspicious_activity_alerts || 0
        },
        loading: false
      }));
    } catch (error) {
      console.error('Failed to fetch player data:', error);
      setState(prev => ({
        ...prev,
        loading: false,
        errors: [{ field: 'fetch', message: 'Failed to load player data' }]
      }));
    }
  }, [state.currentPage, state.pageSize, state.sortBy, state.sortOrder, state.filters]);

  const fetchRegions = useCallback(async () => {
    try {
      const response = await api.get('/api/v1/admin/regions');
      setRegions((response.data as any)?.regions || []);
    } catch (error) {
      console.error('Failed to fetch regions:', error);
    }
  }, []);

  // Helper function to get region name from ID
  const getRegionName = useCallback((regionId: string | null) => {
    if (!regionId) return 'Unknown Region';
    const region = regions.find(r => r.id === regionId);
    return region?.display_name || region?.name || 'Unknown Region';
  }, [regions]);

  // Player management UI handlers. Analytics data is wired to the live admin
  // API (players/comprehensive + analytics/real-time), with graceful demotion
  // when the real-time endpoint is unavailable rather than invented values.

  // UI event handlers
  const handleFiltersChange = useCallback((newFilters: PlayerFilters) => {
    setState(prev => ({
      ...prev,
      filters: newFilters,
      currentPage: 1 // Reset to first page when filters change
    }));
  }, []);

  const handleSortChange = useCallback((sortBy: string, sortOrder: 'asc' | 'desc') => {
    setState(prev => ({ ...prev, sortBy: sortBy as any, sortOrder }));
  }, []);

  const handlePageChange = useCallback((page: number) => {
    setState(prev => ({ ...prev, currentPage: page }));
  }, []);

  const handlePlayerSelect = useCallback((playerId: string, selected: boolean) => {
    setState(prev => ({
      ...prev,
      selectedPlayers: selected 
        ? [...prev.selectedPlayers, playerId]
        : prev.selectedPlayers.filter(id => id !== playerId)
    }));
  }, []);

  const handleSelectAll = useCallback((selected: boolean) => {
    setState(prev => ({
      ...prev,
      selectedPlayers: selected ? prev.players.map(p => p.id) : []
    }));
  }, []);

  const openPlayerDetail = useCallback((player: PlayerModel) => {
    setState(prev => ({ ...prev, selectedPlayer: player, editMode: false }));
  }, []);

  const closePlayerDetail = useCallback(() => {
    setState(prev => ({ 
      ...prev, 
      selectedPlayer: null, 
      editMode: false,
      unsavedChanges: false,
      showAssetManager: false,
      showActivityMonitor: false,
      showEmergencyOps: false
    }));
  }, []);

  const toggleEditMode = useCallback(() => {
    setState(prev => ({ ...prev, editMode: !prev.editMode }));
  }, []);

  const toggleRealTimeUpdates = useCallback(() => {
    setState(prev => ({ ...prev, realTimeUpdates: !prev.realTimeUpdates }));
  }, []);

  // Computed values
  const totalPages = useMemo(() => Math.ceil(state.totalCount / state.pageSize), [state.totalCount, state.pageSize]);
  const hasSelectedPlayers = state.selectedPlayers.length > 0;
  const allPlayersSelected = state.selectedPlayers.length === state.players.length && state.players.length > 0;

  // Error handling
  const clearErrors = useCallback(() => {
    setState(prev => ({ ...prev, errors: [] }));
  }, []);

  return (
    <div className="page-container player-analytics" style={{ maxWidth: '1200px' }}>
      <PageHeader 
        title="Players" 
        subtitle="Comprehensive player management and monitoring"
      />
      
      <div className="page-content">
        {/* Error Display */}
        {state.errors.length > 0 && (
          <div className="alert alert-error mb-6">
            <div className="flex items-center gap-3">
              <span>⚠️</span>
              <div className="flex-1">
                {state.errors.map((error, index) => (
                  <div key={index}>
                    {error.field}: {error.message}
                  </div>
                ))}
              </div>
              <button onClick={clearErrors} className="btn btn-sm">×</button>
            </div>
          </div>
        )}
        
        {state.loading ? (
          <div className="loading-container text-center py-12">
            <div className="loading-spinner mx-auto mb-4"></div>
            <span>Loading enhanced player data...</span>
          </div>
        ) : (
          <div className="space-y-4">
            {/* Enhanced Player Metrics */}
            {state.metrics && (
              <section className="section">
                <div className="section-header">
                  <div>
                    <h3 className="section-title">📊 Player Metrics</h3>
                    <p className="section-subtitle">Real-time player analytics and performance indicators</p>
                  </div>
                </div>
                
                <div className="grid grid-auto-fit-sm gap-4">
                  <div className={`dashboard-stat-card${analyticsAvailable ? '' : ' stat-not-tracked'}`} data-variant="primary">
                    <div className="dashboard-stat-header">
                      <span className="dashboard-stat-icon">👥</span>
                      <h4 className="dashboard-stat-title">Active Players</h4>
                    </div>
                    <div className="dashboard-stat-value">{analyticsAvailable ? state.metrics.total_active_players.toLocaleString() : <>&mdash;</>}</div>
                    <div className="dashboard-stat-description">{analyticsAvailable ? `Online: ${state.metrics.players_online_now}` : 'Analytics endpoint unavailable'}</div>
                  </div>

                  <div className={`dashboard-stat-card${analyticsAvailable ? '' : ' stat-not-tracked'}`}>
                    <div className="dashboard-stat-header">
                      <span className="dashboard-stat-icon">💰</span>
                      <h4 className="dashboard-stat-title">Total Credits</h4>
                    </div>
                    <div className="dashboard-stat-value">{analyticsAvailable ? state.metrics.total_credits_circulation.toLocaleString() : <>&mdash;</>}</div>
                    <div className="dashboard-stat-description">{analyticsAvailable ? 'In Circulation' : 'Analytics endpoint unavailable'}</div>
                  </div>

                  <div className="dashboard-stat-card stat-not-tracked">
                    <div className="dashboard-stat-header">
                      <span className="dashboard-stat-icon">⏱️</span>
                      <h4 className="dashboard-stat-title">Session Time</h4>
                    </div>
                    <div className="dashboard-stat-value">&mdash;</div>
                    <div className="dashboard-stat-description">No session tracking yet</div>
                  </div>

                  <div className={`dashboard-stat-card${analyticsAvailable ? '' : ' stat-not-tracked'}`}>
                    <div className="dashboard-stat-header">
                      <span className="dashboard-stat-icon">🆕</span>
                      <h4 className="dashboard-stat-title">New Players</h4>
                    </div>
                    <div className="dashboard-stat-value">{analyticsAvailable ? state.metrics.new_players_today : <>&mdash;</>}</div>
                    <div className="dashboard-stat-description">{analyticsAvailable ? 'Today' : 'Analytics endpoint unavailable'}</div>
                  </div>

                  <div className="dashboard-stat-card stat-not-tracked">
                    <div className="dashboard-stat-header">
                      <span className="dashboard-stat-icon">📈</span>
                      <h4 className="dashboard-stat-title">Retention Rate</h4>
                    </div>
                    <div className="dashboard-stat-value">&mdash;</div>
                    <div className="dashboard-stat-description">No retention telemetry surfaced yet</div>
                  </div>

                  <div className={`dashboard-stat-card${analyticsAvailable ? '' : ' stat-not-tracked'}`} data-variant="warning">
                    <div className="dashboard-stat-header">
                      <span className="dashboard-stat-icon">🚨</span>
                      <h4 className="dashboard-stat-title">Security Alerts</h4>
                    </div>
                    <div className="dashboard-stat-value">{analyticsAvailable ? state.metrics.suspicious_activity_alerts : <>&mdash;</>}</div>
                    <div className="dashboard-stat-description">{analyticsAvailable ? 'Suspicious Activity' : 'Analytics endpoint unavailable'}</div>
                  </div>
                </div>
              </section>
            )}

            {/* Enhanced Player Controls */}
            <section className="section">
              <div className="card">
                <div className="card-body">
                  <PlayerSearchAndFilter
                    filters={state.filters}
                    onFiltersChange={handleFiltersChange}
                    loading={state.loading}
                  />
                  
                  <div className="flex flex-wrap items-center justify-between gap-4 mt-4">
                    <div className="flex items-center gap-3">
                      <button 
                        onClick={toggleRealTimeUpdates}
                        className={`btn btn-sm ${state.realTimeUpdates ? 'btn-error' : 'btn-outline'}`}
                      >
                        {state.realTimeUpdates ? '🔴' : '⚪'} Real-time
                      </button>
                      
                      <button onClick={fetchPlayerData} className="btn btn-sm btn-outline">
                        🔄 Refresh
                      </button>
                    </div>
                    
                    {hasSelectedPlayers && (
                      <div className="flex items-center gap-3">
                        <span className="text-sm text-muted">
                          {state.selectedPlayers.length} selected
                        </span>
                        <button 
                          onClick={() => setState(prev => ({ ...prev, showBulkOperations: true }))}
                          className="btn btn-sm btn-primary"
                        >
                          📋 Bulk Operations
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </section>

            {/* Enhanced Players Table */}
            <section className="section">
              <div className="section-header" style={{ marginBottom: 'var(--space-4)', paddingBottom: 'var(--space-2)' }}>
                <div>
                  <h3 className="section-title" style={{ fontSize: 'var(--font-size-lg)', margin: 0 }}>👥 Player Management</h3>
                  <p className="section-subtitle" style={{ margin: '0', fontSize: 'var(--font-size-sm)' }}>
                    Showing {state.players.length} of {state.totalCount.toLocaleString()} players
                  </p>
                </div>
                
                <div className="flex items-center gap-3">
                  <select 
                    value={state.sortBy} 
                    onChange={(e) => handleSortChange(e.target.value, state.sortOrder)}
                    className="form-select form-select-sm"
                  >
                    <option value="credits">Sort by Credits</option>
                    <option value="last_login">Sort by Last Login</option>
                    <option value="username">Sort by Username</option>
                    <option value="turns">Sort by Turns</option>
                    <option value="created_at">Sort by Created</option>
                  </select>
                  <button 
                    onClick={() => handleSortChange(state.sortBy, state.sortOrder === 'asc' ? 'desc' : 'asc')}
                    className="btn btn-sm btn-outline"
                  >
                    {state.sortOrder === 'asc' ? '↑' : '↓'}
                  </button>
                  
                  {/* Column Visibility Toggle */}
                  <div className="dropdown">
                    <button className="btn btn-sm btn-outline">
                      📋 Columns
                    </button>
                    <div className="dropdown-menu" style={{ minWidth: '180px', padding: 'var(--space-2)' }}>
                      <label className="flex items-center gap-2 p-1">
                        <input
                          type="checkbox"
                          checked={visibleColumns.location}
                          onChange={(e) => setVisibleColumns(prev => ({ ...prev, location: e.target.checked }))}
                          className="form-checkbox"
                        />
                        <span className="text-sm">Location</span>
                      </label>
                      <label className="flex items-center gap-2 p-1">
                        <input
                          type="checkbox"
                          checked={visibleColumns.activity}
                          onChange={(e) => setVisibleColumns(prev => ({ ...prev, activity: e.target.checked }))}
                          className="form-checkbox"
                        />
                        <span className="text-sm">Activity</span>
                      </label>
                      <label className="flex items-center gap-2 p-1">
                        <input
                          type="checkbox"
                          checked={visibleColumns.assets}
                          onChange={(e) => setVisibleColumns(prev => ({ ...prev, assets: e.target.checked }))}
                          className="form-checkbox"
                        />
                        <span className="text-sm">Assets</span>
                      </label>
                    </div>
                  </div>
                </div>
              </div>
              
              <div className="card" style={{ margin: 0, padding: 0 }}>
                <div className="card-body" style={{ padding: 0 }}>
                  <div className="table-container">
                    <table className="table">
                      <thead>
                        <tr>
                          <th style={{width: '24px'}}>
                            <input 
                              type="checkbox" 
                              checked={allPlayersSelected}
                              onChange={(e) => handleSelectAll(e.target.checked)}
                              className="form-checkbox"
                            />
                          </th>
                          {visibleColumns.player && <th>Player</th>}
                          {visibleColumns.status && <th>Status</th>}
                          {visibleColumns.credits && <th>Credits</th>}
                          {visibleColumns.assets && <th>Assets</th>}
                          {visibleColumns.location && <th>Location</th>}
                          {visibleColumns.activity && <th>Activity</th>}
                          {visibleColumns.lastLogin && <th>Last Login</th>}
                          {visibleColumns.turns && <th>Turns</th>}
                          {visibleColumns.actions && <th>Actions</th>}
                        </tr>
                      </thead>
                      <tbody>
                        {state.players.map((player) => (
                          <tr 
                            key={player.id} 
                            className={`cursor-pointer hover:bg-hover ${state.selectedPlayers.includes(player.id) ? 'bg-primary-50' : ''} ${player.activity.suspicious_activity ? 'border-l-4 border-warning' : ''}`}
                            onClick={() => openPlayerDetail(player)}
                          >
                            <td onClick={(e) => e.stopPropagation()}>
                              <input 
                                type="checkbox" 
                                checked={state.selectedPlayers.includes(player.id)}
                                onChange={(e) => handlePlayerSelect(player.id, e.target.checked)}
                                className="form-checkbox"
                              />
                            </td>
                            {visibleColumns.player && (
                              <td>
                                <div className="flex items-center gap-2">
                                  <div>
                                    <div className="font-medium">{player.username}</div>
                                    <div className="text-sm text-muted">{player.id.slice(0, 8)}</div>
                                  </div>
                                  {player.activity.suspicious_activity && <span className="text-warning">⚠️</span>}
                                </div>
                              </td>
                            )}
                            {visibleColumns.status && (
                              <td>
                                <span className={`badge ${
                                  player.status === 'active' ? 'badge-success' : 
                                  player.status === 'banned' ? 'badge-error' : 'badge-secondary'
                                }`}>
                                  {player.status}
                                </span>
                              </td>
                            )}
                            {visibleColumns.credits && (
                              <td className="font-mono">{player.credits.toLocaleString()}</td>
                            )}
                            {visibleColumns.assets && (
                              <td>
                                <div className="flex items-center gap-2 text-sm">
                                  <span>🚀 {player.assets.ships_count}</span>
                                  <span>🌍 {player.assets.planets_count}</span>
                                  <span>🏪 {player.assets.stations_count}</span>
                                </div>
                              </td>
                            )}
                            {visibleColumns.location && (
                              <td>
                                <span className="text-sm">
                                  {getRegionName(player.current_region_id)}, Sector {player.current_sector_id || 'Unknown'}
                                </span>
                              </td>
                            )}
                            {visibleColumns.activity && (
                              <td>
                                <div className="text-sm">
                                  <div>Actions: {player.activity.actions_today}</div>
                                  <div>Combat: {player.activity.combat_rating}</div>
                                </div>
                              </td>
                            )}
                            {visibleColumns.lastLogin && (
                              <td className="text-sm">{player.activity.last_login ? new Date(player.activity.last_login).toLocaleDateString() : '—'}</td>
                            )}
                            {visibleColumns.turns && (
                              <td>
                                <span className={`font-mono ${player.turns < 10 ? 'text-warning' : ''}`}>
                                  {player.turns}
                                </span>
                              </td>
                            )}
                            {visibleColumns.actions && (
                              <td onClick={(e) => e.stopPropagation()}>
                                <div className="flex items-center gap-1">
                                  <button 
                                    className="btn btn-xs btn-outline"
                                    onClick={() => openPlayerDetail(player)}
                                    title="View Details"
                                  >
                                    👁️
                                  </button>
                                  <button 
                                    className="btn btn-xs btn-outline"
                                    onClick={() => {
                                      setState(prev => ({ ...prev, selectedPlayer: player, editMode: true }));
                                    }}
                                    title="Edit Player"
                                  >
                                    ✏️
                                  </button>
                                  <button 
                                    className="btn btn-xs btn-error"
                                    onClick={() => {
                                      setState(prev => ({ ...prev, selectedPlayer: player, showEmergencyOps: true }));
                                    }}
                                    title="Emergency Operations"
                                  >
                                    🚨
                                  </button>
                                </div>
                              </td>
                            )}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  
                  {/* Enhanced Pagination */}
                  {state.totalCount > 0 && (
                    <div className="pagination" style={{ 
                      backgroundColor: 'var(--surface-primary)', 
                      padding: 'var(--space-4)', 
                      borderTop: '1px solid var(--border-light)' 
                    }}>
                      <div className={`flex items-center gap-4 w-full ${totalPages > 1 ? 'justify-between' : 'justify-center'}`}>
                        {/* Page Navigation */}
                        {totalPages > 1 && (
                          <div className="flex items-center gap-2">
                            <button 
                              onClick={() => handlePageChange(state.currentPage - 1)}
                              disabled={state.currentPage === 1}
                              className="btn btn-sm btn-outline"
                            >
                              ← Previous
                            </button>
                            
                            {/* Page Numbers */}
                            <div className="flex items-center gap-1">
                              {(() => {
                                const pages = [];
                                const startPage = Math.max(1, state.currentPage - 2);
                                const endPage = Math.min(totalPages, state.currentPage + 2);
                                
                                if (startPage > 1) {
                                  pages.push(
                                    <button key={1} onClick={() => handlePageChange(1)} className="btn btn-xs btn-outline">1</button>
                                  );
                                  if (startPage > 2) {
                                    pages.push(<span key="start-ellipsis" className="text-sm text-muted px-2">...</span>);
                                  }
                                }
                                
                                for (let i = startPage; i <= endPage; i++) {
                                  pages.push(
                                    <button 
                                      key={i} 
                                      onClick={() => handlePageChange(i)}
                                      className={`btn btn-xs ${i === state.currentPage ? 'btn-primary' : 'btn-outline'}`}
                                    >
                                      {i}
                                    </button>
                                  );
                                }
                                
                                if (endPage < totalPages) {
                                  if (endPage < totalPages - 1) {
                                    pages.push(<span key="end-ellipsis" className="text-sm text-muted px-2">...</span>);
                                  }
                                  pages.push(
                                    <button key={totalPages} onClick={() => handlePageChange(totalPages)} className="btn btn-xs btn-outline">{totalPages}</button>
                                  );
                                }
                                
                                return pages;
                              })()}
                            </div>
                            
                            <button 
                              onClick={() => handlePageChange(state.currentPage + 1)}
                              disabled={state.currentPage === totalPages}
                              className="btn btn-sm btn-outline"
                            >
                              Next →
                            </button>
                          </div>
                        )}
                        
                        {/* Page Info and Size Selector */}
                        <div className="flex items-center gap-4">
                          <span className="text-sm text-muted">
                            Showing {((state.currentPage - 1) * state.pageSize) + 1}-{Math.min(state.currentPage * state.pageSize, state.totalCount)} of {state.totalCount.toLocaleString()}
                          </span>
                          <select 
                            value={state.pageSize} 
                            onChange={(e) => setState(prev => ({ ...prev, pageSize: parseInt(e.target.value), currentPage: 1 }))}
                            className="form-select form-select-sm"
                          >
                            <option value="10">10 per page</option>
                            <option value="20">20 per page</option>
                            <option value="25">25 per page</option>
                            <option value="50">50 per page</option>
                            <option value="100">100 per page</option>
                          </select>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </section>
          </div>
        )}

        {/* Enhanced Player Detail Modal */}
        {state.selectedPlayer && !state.editMode && !state.showAssetManager && !state.showEmergencyOps && (
          <div className="modal-overlay" onClick={closePlayerDetail}>
            <div className="modal modal-lg" onClick={(e) => e.stopPropagation()}>
              <div className="modal-header">
                <h3 className="modal-title">Player Details: {state.selectedPlayer.username}</h3>
                <div className="flex items-center gap-2">
                  <button onClick={toggleEditMode} className="btn btn-sm btn-primary">
                    ✏️ Edit
                  </button>
                  <button className="btn btn-sm btn-ghost" onClick={closePlayerDetail}>×</button>
                </div>
              </div>
              
              <div className="modal-body">
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                  <div className="space-y-4">
                    <h4 className="text-lg font-semibold">Account Information</h4>
                    <div className="space-y-3">
                      <div>
                        <div className="text-sm text-muted">User ID</div>
                        <div className="font-mono text-sm">{state.selectedPlayer.id}</div>
                      </div>
                      <div>
                        <div className="text-sm text-muted">Username</div>
                        <div className="font-medium">{state.selectedPlayer.username}</div>
                      </div>
                      <div>
                        <div className="text-sm text-muted">Email</div>
                        <div>{state.selectedPlayer.email}</div>
                      </div>
                      <div>
                        <div className="text-sm text-muted">Status</div>
                        <span className={`badge ${
                          state.selectedPlayer.status === 'active' ? 'badge-success' : 
                          state.selectedPlayer.status === 'banned' ? 'badge-error' : 'badge-secondary'
                        }`}>
                          {state.selectedPlayer.status}
                        </span>
                      </div>
                      <div>
                        <div className="text-sm text-muted">Account Created</div>
                        <div>{new Date(state.selectedPlayer.created_at).toLocaleDateString()}</div>
                      </div>
                      <div>
                        <div className="text-sm text-muted">Last Login</div>
                        <div>{state.selectedPlayer.activity.last_login ? new Date(state.selectedPlayer.activity.last_login).toLocaleString() : '—'}</div>
                      </div>
                    </div>
                  </div>
                  
                  <div className="space-y-4">
                    <h4 className="text-lg font-semibold">Game Statistics</h4>
                    <div className="space-y-3">
                      <div>
                        <div className="text-sm text-muted">Credits</div>
                        <div className="font-mono text-lg">{state.selectedPlayer.credits.toLocaleString()}</div>
                      </div>
                      <div>
                        <div className="text-sm text-muted">Current Location</div>
                        <div>
                          {getRegionName(state.selectedPlayer.current_region_id)}, Sector {state.selectedPlayer.current_sector_id || 'Unknown'}
                        </div>
                      </div>
                      <div>
                        <div className="text-sm text-muted">Turns Remaining</div>
                        <div>{state.selectedPlayer.turns}</div>
                      </div>
                      <div>
                        <div className="text-sm text-muted">Combat Rating</div>
                        <div>{state.selectedPlayer.activity.combat_rating}</div>
                      </div>
                      <div>
                        <div className="text-sm text-muted">Trade Volume</div>
                        <div className="font-mono">{state.selectedPlayer.activity.total_trade_volume.toLocaleString()}</div>
                      </div>
                      <div>
                        <div className="text-sm text-muted">Team</div>
                        <div>{state.selectedPlayer.team_id || 'None'}</div>
                      </div>
                    </div>
                  </div>
                  
                  <div className="space-y-4">
                    <h4 className="text-lg font-semibold">Assets & Inventory</h4>
                    <div className="space-y-3">
                      <div>
                        <div className="text-sm text-muted">Ships Owned</div>
                        <div>{state.selectedPlayer.assets.ships_count}</div>
                      </div>
                      <div>
                        <div className="text-sm text-muted">Planets Owned</div>
                        <div>{state.selectedPlayer.assets.planets_count}</div>
                      </div>
                      <div>
                        <div className="text-sm text-muted">Ports Owned</div>
                        <div>{state.selectedPlayer.assets.stations_count}</div>
                      </div>
                      <div>
                        <div className="text-sm text-muted">Current Ship</div>
                        <div>{state.selectedPlayer.current_ship_id || 'None'}</div>
                      </div>
                      <div>
                        <div className="text-sm text-muted">Total Asset Value</div>
                        <div className="font-mono">{state.selectedPlayer.assets.total_value.toLocaleString()}</div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              
              <div className="modal-footer">
                <div className="flex gap-3">
                  <button 
                    className="btn btn-outline"
                    onClick={() => setState(prev => ({ ...prev, showAssetManager: true }))}
                  >
                    🏭 Manage Assets
                  </button>
                  <button 
                    className="btn btn-error"
                    onClick={() => setState(prev => ({ ...prev, showEmergencyOps: true }))}
                  >
                    🚨 Emergency Ops
                  </button>
                  <button 
                    className="btn btn-primary"
                    onClick={toggleEditMode}
                  >
                    ✏️ Edit Player
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}
        
        {/* Player Detail Editor Modal */}
        {state.selectedPlayer && state.editMode && (
          <div className="modal-overlay" onClick={() => setState(prev => ({ ...prev, editMode: false }))}>
            <PlayerDetailEditor
              player={state.selectedPlayer}
              onClose={() => setState(prev => ({ ...prev, editMode: false, selectedPlayer: null }))}
              onSave={(updatedPlayer) => {
                // Update the player in the list
                setState(prev => ({
                  ...prev,
                  players: prev.players.map(p => p.id === updatedPlayer.id ? updatedPlayer : p),
                  selectedPlayer: updatedPlayer,
                  editMode: false
                }));
              }}
            />
          </div>
        )}
        
        {state.showBulkOperations && (
          <div className="modal-overlay" onClick={() => setState(prev => ({ ...prev, showBulkOperations: false }))}>
            <BulkOperationPanel
              selectedPlayers={state.selectedPlayers.map(id => state.players.find(p => p.id === id)!).filter(Boolean)}
              onClose={() => setState(prev => ({ ...prev, showBulkOperations: false, selectedPlayers: [] }))}
              onComplete={(operation, results) => {
                console.log(`Bulk operation ${operation} completed:`, results);
                // Refresh the player data after bulk operation
                fetchPlayerData();
                // Clear selection after operation
                setState(prev => ({ ...prev, selectedPlayers: [] }));
              }}
            />
          </div>
        )}
        
        {state.selectedPlayer && state.showAssetManager && (
          <div className="modal-overlay" onClick={() => setState(prev => ({ ...prev, showAssetManager: false }))}>
            <PlayerAssetManager
              player={state.selectedPlayer}
              onClose={() => setState(prev => ({ ...prev, showAssetManager: false }))}
              onUpdate={(updatedPlayer) => {
                // Update the player in the list
                setState(prev => ({
                  ...prev,
                  players: prev.players.map(p => p.id === updatedPlayer.id ? updatedPlayer : p),
                  selectedPlayer: updatedPlayer
                }));
              }}
            />
          </div>
        )}
        
        {state.selectedPlayer && state.showEmergencyOps && (
          <div className="modal-overlay" onClick={() => setState(prev => ({ ...prev, showEmergencyOps: false }))}>
            <EmergencyOperationsPanel
              player={state.selectedPlayer}
              onClose={() => setState(prev => ({ ...prev, showEmergencyOps: false }))}
              onUpdate={(updatedPlayer) => {
                // Update the player in the list
                setState(prev => ({
                  ...prev,
                  players: prev.players.map(p => p.id === updatedPlayer.id ? updatedPlayer : p),
                  selectedPlayer: updatedPlayer
                }));
              }}
            />
          </div>
        )}
      </div>
    </div>
  );
};

export default PlayerAnalytics;