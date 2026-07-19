import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { Link } from 'react-router-dom';

// Components
import PageHeader from '../ui/PageHeader';
import { useAuth } from '../../contexts/AuthContext';

// Define types for our dashboard data
interface SystemHealth {
  database: {
    status: string;
    connected: boolean;
    response_time: number;
  };
  ai: {
    status: string;
    healthy: number;
    total: number;
  };
  gameserver: {
    status: string;
    response_time: number;
  };
}

interface PlayerStats {
  total_players: number | null;
  active_sessions: number | null;
  new_today: number | null;
  new_this_week: number | null;
}

interface UniverseStats {
  total_sectors: number | null;
  total_planets: number | null;
  total_ports: number | null;
  total_ships: number | null;
  total_warp_tunnels: number | null;
}

const fmtStat = (n: number | null | undefined): string =>
  n != null && !Number.isNaN(n) ? n.toLocaleString() : '—';

const asStat = (v: unknown): number | null =>
  typeof v === 'number' && !Number.isNaN(v) ? v : null;

interface DashboardData {
  system_health: SystemHealth;
  player_stats: PlayerStats;
  universe_stats: UniverseStats;
  last_updated: string;
}

interface AuditLogEntry {
  id: string;
  timestamp: string | null;
  method: string;
  path: string;
  status_code: number | null;
  user_id: string | null;
  user_type: string | null;
  client_ip: string;
  action: string | null;
  resource_type: string | null;
  resource_id: string | null;
}

type AuditFeedState =
  | { status: 'loading' }
  | { status: 'ok'; entries: AuditLogEntry[] }
  | { status: 'error'; message: string };

const Dashboard: React.FC = () => {
  const { token } = useAuth();
  const [dashboardData, setDashboardData] = useState<DashboardData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [lastRefresh, setLastRefresh] = useState<Date>(new Date());
  const [auditFeed, setAuditFeed] = useState<AuditFeedState>({ status: 'loading' });

  const fetchDashboardData = async () => {
    try {
      // Prepare headers with authentication
      const headers = token ? { Authorization: `Bearer ${token}` } : {};

      // Fetch all dashboard data concurrently - use allSettled so partial failures don't blank everything
      const [dbHealthRes, aiHealthRes, gameServerRes, adminStatsRes, auditRes] = await Promise.allSettled([
        axios.get('/api/v1/status/database/detailed', { headers, timeout: 10000 }),
        axios.get('/api/v1/status/ai/providers', { headers, timeout: 15000 }),
        axios.get('/api/v1/status/', { headers, timeout: 10000 }),
        axios.get('/api/v1/admin/stats', { headers, timeout: 10000 }),
        axios.get('/api/v1/admin/audit/logs', { headers, timeout: 10000, params: { limit: 8 } })
      ]);

      // Process recent audit events with honest empty/error state (no mock data)
      if (auditRes.status === 'fulfilled') {
        const logs = auditRes.value.data?.logs;
        setAuditFeed({ status: 'ok', entries: Array.isArray(logs) ? (logs as AuditLogEntry[]) : [] });
      } else {
        const reason = auditRes.reason;
        let message = 'Unable to load recent audit events.';
        if (axios.isAxiosError(reason)) {
          if (reason.response) {
            message = `Audit log request failed (${reason.response.status}).`;
          } else if (reason.code === 'ECONNABORTED') {
            message = 'Audit log request timed out.';
          } else {
            message = 'Audit log request failed: network error.';
          }
        }
        setAuditFeed({ status: 'error', message });
      }

      // Process system health data with graceful degradation
      const systemHealth: SystemHealth = {
        database: dbHealthRes.status === 'fulfilled' ? {
          status: dbHealthRes.value.data.status,
          connected: dbHealthRes.value.data.connected,
          response_time: dbHealthRes.value.data.response_time
        } : { status: 'unavailable', connected: false, response_time: 0 },
        ai: aiHealthRes.status === 'fulfilled' ? {
          status: aiHealthRes.value.data.status,
          healthy: aiHealthRes.value.data.summary.healthy,
          total: aiHealthRes.value.data.summary.total
        } : { status: 'unavailable', healthy: 0, total: 0 },
        gameserver: gameServerRes.status === 'fulfilled' ? {
          status: gameServerRes.value.data.status === 'healthy' ? 'healthy' : 'degraded',
          response_time: 0
        } : { status: 'unavailable', response_time: 0 }
      };

      // Process admin stats data
      const stats = adminStatsRes.status === 'fulfilled' ? adminStatsRes.value.data as any : {};
      
      const dashboardData: DashboardData = {
        system_health: systemHealth,
        player_stats: {
          total_players: asStat(stats.total_players),
          active_sessions: asStat(stats.active_sessions),
          new_today: asStat(stats.new_players_today),
          new_this_week: asStat(stats.new_players_week)
        },
        universe_stats: {
          total_sectors: asStat(stats.total_sectors),
          total_planets: asStat(stats.total_planets),
          total_ports: asStat(stats.total_ports),
          total_ships: asStat(stats.total_ships),
          total_warp_tunnels: asStat(stats.total_warp_tunnels)
        },
        last_updated: new Date().toISOString()
      };

      setDashboardData(dashboardData);
      setLastRefresh(new Date());
    } catch (error) {
      console.error('Error fetching dashboard data:', error);
      // Set fallback data on error
      setDashboardData({
        system_health: {
          database: { status: 'unavailable', connected: false, response_time: 0 },
          ai: { status: 'unavailable', healthy: 0, total: 2 },
          gameserver: { status: 'unavailable', response_time: 0 }
        },
        player_stats: { total_players: null, active_sessions: null, new_today: null, new_this_week: null },
        universe_stats: { total_sectors: null, total_planets: null, total_ports: null, total_ships: null, total_warp_tunnels: null },
        last_updated: new Date().toISOString()
      });
      setAuditFeed({ status: 'error', message: 'Unable to load recent audit events.' });
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchDashboardData();
    
    // Refresh dashboard data every 30 seconds
    const interval = setInterval(fetchDashboardData, 30000);
    
    return () => clearInterval(interval);
  }, []);

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'healthy': return '#2ecc71';
      case 'degraded': return '#f39c12';
      case 'unavailable': return '#e74c3c';
      default: return '#7f8c8d';
    }
  };

  const formatRelativeTime = (timestamp: string | null): string => {
    if (!timestamp) return 'unknown time';
    const then = new Date(timestamp);
    if (Number.isNaN(then.getTime())) return 'unknown time';
    const diffMs = Date.now() - then.getTime();
    const diffSec = Math.round(diffMs / 1000);
    if (diffSec < 0) return then.toLocaleString();
    if (diffSec < 60) return `${diffSec}s ago`;
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    const diffDay = Math.floor(diffHr / 24);
    if (diffDay < 7) return `${diffDay}d ago`;
    return then.toLocaleDateString();
  };

  const formatAuditAction = (entry: AuditLogEntry): string => {
    if (entry.action) {
      return entry.action.replace(/_/g, ' ');
    }
    return `${entry.method} ${entry.path}`;
  };

  const formatAuditActor = (entry: AuditLogEntry): string => {
    const type = entry.user_type || 'anonymous';
    if (entry.user_id) {
      return `${type} (${entry.user_id.slice(0, 8)})`;
    }
    return type;
  };

  const formatAuditTarget = (entry: AuditLogEntry): string | null => {
    if (!entry.resource_type && !entry.resource_id) return null;
    if (entry.resource_type && entry.resource_id) {
      return `${entry.resource_type}:${entry.resource_id.slice(0, 8)}`;
    }
    return entry.resource_type || (entry.resource_id ? entry.resource_id.slice(0, 8) : null);
  };

  if (isLoading) {
    return (
      <div className="page-container">
        <PageHeader title="Dashboard" subtitle="Game Galaxy Overview" />
        <div className="dashboard-loading">
          <div className="loading-spinner">⟳</div>
          <p>Loading dashboard data...</p>
        </div>
      </div>
    );
  }

  if (!dashboardData) {
    return (
      <div className="page-container">
        <PageHeader title="Dashboard" subtitle="Game Galaxy Overview" />
        <div className="dashboard-error">
          <p>Unable to load dashboard data. Please check your connection.</p>
          <button onClick={fetchDashboardData} className="retry-button">
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="page-container">
      <PageHeader title="Dashboard" subtitle="Game Galaxy Overview" />

      <div className="page-content">
        {/* Quick Access Section */}
        <section className="section">
          <div className="section-header">
            <div>
              <h3 className="section-title">Quick Access</h3>
              <p className="section-subtitle">Commonly used administrative functions</p>
            </div>
          </div>
          <div className="grid grid-auto-fit gap-6">
            <Link to="/users" className="card card-interactive">
              <div className="card-body">
                <div className="flex items-center gap-4">
                  <div className="text-3xl">👥</div>
                  <div className="flex-1">
                    <h4 className="font-semibold text-primary mb-1">Users</h4>
                    <p className="text-sm text-tertiary">Manage player accounts and permissions</p>
                  </div>
                </div>
              </div>
            </Link>
            
            <Link to="/universe" className="card card-interactive">
              <div className="card-body">
                <div className="flex items-center gap-4">
                  <div className="text-3xl">🌌</div>
                  <div className="flex-1">
                    <h4 className="font-semibold text-primary mb-1">Universe</h4>
                    <p className="text-sm text-tertiary">Generate and manage the game universe</p>
                  </div>
                </div>
              </div>
            </Link>
            
            <Link to="/sectors" className="card card-interactive">
              <div className="card-body">
                <div className="flex items-center gap-4">
                  <div className="text-3xl">🔳</div>
                  <div className="flex-1">
                    <h4 className="font-semibold text-primary mb-1">Sectors</h4>
                    <p className="text-sm text-tertiary">Configure sectors, planets and stations</p>
                  </div>
                </div>
              </div>
            </Link>
            
            <Link to="/analytics" className="card card-interactive">
              <div className="card-body">
                <div className="flex items-center gap-4">
                  <div className="text-3xl">📊</div>
                  <div className="flex-1">
                    <h4 className="font-semibold text-primary mb-1">Analytics</h4>
                    <p className="text-sm text-tertiary">View detailed reports and metrics</p>
                  </div>
                </div>
              </div>
            </Link>
          </div>
        </section>

        {/* System Health Overview */}
        <section className="section">
          <div className="section-header">
            <div>
              <h3 className="section-title">System Health</h3>
              <p className="section-subtitle">Real-time status of all system components</p>
            </div>
            <div className="section-actions">
              <span className="text-sm text-tertiary">
                Last updated: {lastRefresh.toLocaleTimeString()}
              </span>
              <button 
                onClick={fetchDashboardData} 
                className="btn btn-secondary btn-sm"
                disabled={isLoading}
                title="Refresh dashboard data"
              >
                {isLoading ? '⟳' : '↻'}
              </button>
            </div>
          </div>
          <div className="grid grid-auto-fit gap-6">
            <div className="card">
              <div className="card-body">
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <span className="text-2xl">🗄️</span>
                    <h4 className="font-semibold text-primary">Database</h4>
                  </div>
                  <span 
                    className="status-dot"
                    style={{ backgroundColor: getStatusColor(dashboardData.system_health.database.status) }}
                  >
                  </span>
                </div>
                <div className="flex flex-col gap-3">
                  <div className="flex justify-between items-center">
                    <span className="text-sm text-tertiary">Status:</span>
                    <span className="text-sm font-medium text-secondary">{dashboardData.system_health.database.status}</span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-sm text-tertiary">Response:</span>
                    <span className="text-sm font-medium text-secondary">{dashboardData.system_health.database.response_time.toFixed(0)}ms</span>
                  </div>
                </div>
              </div>
            </div>

            <div className="card">
              <div className="card-body">
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <span className="text-2xl">🤖</span>
                    <h4 className="font-semibold text-primary">AI Services</h4>
                  </div>
                  <span 
                    className="status-dot"
                    style={{ backgroundColor: getStatusColor(dashboardData.system_health.ai.status) }}
                  >
                  </span>
                </div>
                <div className="flex flex-col gap-3">
                  <div className="flex justify-between items-center">
                    <span className="text-sm text-tertiary">Healthy:</span>
                    <span className="text-sm font-medium text-secondary">
                      {dashboardData.system_health.ai.healthy}/{dashboardData.system_health.ai.total}
                    </span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-sm text-tertiary">Status:</span>
                    <span className="text-sm font-medium text-secondary">{dashboardData.system_health.ai.status}</span>
                  </div>
                </div>
              </div>
            </div>

            <div className="card">
              <div className="card-body">
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <span className="text-2xl">🖥️</span>
                    <h4 className="font-semibold text-primary">Game Server</h4>
                  </div>
                  <span 
                    className="status-dot"
                    style={{ backgroundColor: getStatusColor(dashboardData.system_health.gameserver.status) }}
                  >
                  </span>
                </div>
                <div className="flex flex-col gap-3">
                  <div className="flex justify-between items-center">
                    <span className="text-sm text-tertiary">Status:</span>
                    <span className="text-sm font-medium text-secondary">{dashboardData.system_health.gameserver.status}</span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-sm text-tertiary">API:</span>
                    <span className="text-sm font-medium text-secondary">
                      {dashboardData.system_health.gameserver.status === 'healthy' ? 'Operational' :
                       dashboardData.system_health.gameserver.status === 'degraded' ? 'Degraded' : 'Unavailable'}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Statistics Overview */}
        <section className="section">
          <div className="section-header">
            <div>
              <h3 className="section-title">Galaxy Statistics</h3>
              <p className="section-subtitle">Real-time metrics from across the game universe</p>
            </div>
          </div>
          <div className="grid grid-auto-fit gap-6">
            <div className="dashboard-stat-card">
              <div className="dashboard-stat-header">
                <span className="dashboard-stat-icon">👥</span>
                <h4 className="dashboard-stat-title">Players</h4>
              </div>
              <div className="dashboard-stat-value">
                {fmtStat(dashboardData.player_stats.total_players)}
              </div>
              <div className="flex justify-between">
                <div className="text-center">
                  <div className="text-xl font-semibold text-secondary">{fmtStat(dashboardData.player_stats.active_sessions)}</div>
                  <div className="text-xs text-tertiary">Online</div>
                </div>
                <div className="text-center">
                  <div className="text-xl font-semibold text-secondary">{fmtStat(dashboardData.player_stats.new_today)}</div>
                  <div className="text-xs text-tertiary">New Today</div>
                </div>
              </div>
            </div>

            <div className="dashboard-stat-card">
              <div className="dashboard-stat-header">
                <span className="dashboard-stat-icon">🌌</span>
                <h4 className="dashboard-stat-title">Universe</h4>
              </div>
              <Link to="/universe/sectors" className="block text-center mb-4 hover:opacity-80 transition-opacity">
                <div className="dashboard-stat-value">
                  {fmtStat(dashboardData.universe_stats.total_sectors)}
                </div>
                <div className="text-xs text-tertiary">Sectors →</div>
              </Link>
              <div className="grid grid-cols-3 gap-2">
                <Link to="/universe/planets" className="text-center hover:opacity-80 transition-opacity">
                  <div className="text-lg font-semibold text-secondary">{fmtStat(dashboardData.universe_stats.total_planets)}</div>
                  <div className="text-xs text-tertiary">Planets →</div>
                </Link>
                <Link to="/universe/stations" className="text-center hover:opacity-80 transition-opacity">
                  <div className="text-lg font-semibold text-secondary">{fmtStat(dashboardData.universe_stats.total_ports)}</div>
                  <div className="text-xs text-tertiary">Ports →</div>
                </Link>
                <Link to="/universe/warptunnels" className="text-center hover:opacity-80 transition-opacity">
                  <div className="text-lg font-semibold text-secondary">{fmtStat(dashboardData.universe_stats.total_warp_tunnels)}</div>
                  <div className="text-xs text-tertiary">Warp Tunnels →</div>
                </Link>
              </div>
            </div>

            <div className="dashboard-stat-card">
              <div className="dashboard-stat-header">
                <span className="dashboard-stat-icon">🚀</span>
                <h4 className="dashboard-stat-title">Fleet</h4>
              </div>
              <div className="dashboard-stat-value">
                {fmtStat(dashboardData.universe_stats.total_ships)}
              </div>
              <div className="text-xs text-tertiary" style={{ textAlign: 'center' }}>Total Ships</div>
            </div>

            <div className="dashboard-stat-card">
              <div className="dashboard-stat-header">
                <span className="dashboard-stat-icon">📈</span>
                <h4 className="dashboard-stat-title">Growth</h4>
              </div>
              <div className="dashboard-stat-value">
                {fmtStat(dashboardData.player_stats.new_this_week)}
              </div>
              <div className="text-xs text-tertiary mb-4">New This Week</div>
              <div className="flex justify-between">
                <div className="text-center">
                  <div className="text-xl font-semibold text-secondary">
                    {dashboardData.player_stats.total_players != null
                      && dashboardData.player_stats.total_players > 0
                      && dashboardData.player_stats.active_sessions != null
                      ? `${Math.round((dashboardData.player_stats.active_sessions / dashboardData.player_stats.total_players) * 100)}%`
                      : '—'}
                  </div>
                  <div className="text-xs text-tertiary">Active Rate</div>
                </div>
                <div className="text-center">
                  <div className="text-xl font-semibold text-secondary">
                    {dashboardData.player_stats.total_players != null
                      && dashboardData.player_stats.total_players > 0
                      && dashboardData.player_stats.new_this_week != null
                      ? `+${Math.round((dashboardData.player_stats.new_this_week / dashboardData.player_stats.total_players) * 100)}%`
                      : '—'}
                  </div>
                  <div className="text-xs text-tertiary">Weekly Growth</div>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Recent Audit Events */}
        <section className="section">
          <div className="section-header">
            <div>
              <h3 className="section-title">Recent Audit Events</h3>
              <p className="section-subtitle">Latest administrative and security activity</p>
            </div>
            <div className="section-actions">
              <Link to="/security" className="text-sm text-tertiary hover:opacity-80 transition-opacity">
                View all →
              </Link>
            </div>
          </div>
          <div className="card">
            <div className="card-body">
              {auditFeed.status === 'loading' && (
                <p className="text-sm text-tertiary" style={{ margin: 0 }}>Loading recent audit events…</p>
              )}
              {auditFeed.status === 'error' && (
                <p className="text-sm" style={{ margin: 0, color: 'var(--status-error, var(--color-red-500, #e74c3c))' }}>
                  {auditFeed.message}
                </p>
              )}
              {auditFeed.status === 'ok' && auditFeed.entries.length === 0 && (
                <p className="text-sm text-tertiary" style={{ margin: 0 }}>No recent audit events.</p>
              )}
              {auditFeed.status === 'ok' && auditFeed.entries.length > 0 && (
                <div className="flex flex-col">
                  {auditFeed.entries.map((entry, index) => {
                    const target = formatAuditTarget(entry);
                    return (
                      <div
                        key={entry.id}
                        className="flex items-center justify-between gap-4"
                        style={{
                          padding: '0.75rem 0',
                          borderTop: index === 0 ? 'none' : '1px solid var(--border-light, var(--color-gray-700, #374151))'
                        }}
                      >
                        <div className="flex flex-col gap-1" style={{ minWidth: 0 }}>
                          <span className="text-sm font-medium text-primary" style={{ textTransform: 'capitalize' }}>
                            {formatAuditAction(entry)}
                          </span>
                          <span className="text-xs text-tertiary" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {formatAuditActor(entry)}
                            {target ? ` → ${target}` : ''}
                          </span>
                        </div>
                        <span className="text-xs text-tertiary" style={{ whiteSpace: 'nowrap', flexShrink: 0 }}>
                          {formatRelativeTime(entry.timestamp)}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </section>

      </div>
    </div>
  );
};

export default Dashboard;