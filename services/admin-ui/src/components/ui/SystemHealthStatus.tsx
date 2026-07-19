import React, { useState, useEffect } from 'react';
import './system-health-status.css';

interface ServerStatus {
  status: 'online' | 'offline' | 'maintenance' | 'unknown';
  responseTime: number;
  activeConnections: number;
  adminConnections: number;
  lastChecked: string;
  connectionStats?: {
    total_connections: number;
    total_admin_connections: number;
    sectors_with_players: number;
    teams_with_players: number;
    connections_by_sector: Record<string, number>;
    connections_by_team: Record<string, number>;
  };
}

interface APIHealth {
  provider: string;
  status: 'healthy' | 'degraded' | 'unavailable';
  configured: boolean;
  reachable: boolean;
  response_time: number;
  last_check: string;
  error?: string;
}

interface AllProvidersHealth {
  provider: string;
  status: 'healthy' | 'degraded' | 'unavailable';
  providers: {
    openai: APIHealth;
    anthropic: APIHealth;
  };
  summary: {
    healthy: number;
    configured: number;
    total: number;
  };
  response_time: number;
  last_check: string;
}

interface DatabaseHealth {
  provider: string;
  status: 'healthy' | 'degraded' | 'unavailable';
  host?: string;
  database?: string;
  connected?: boolean;
  response_time: number;
  pool_status?: {
    size: number;
    checked_out: number;
    overflow: number;
    total_connections: number;
  };
  database_info?: {
    size_mb: number;
    size_pretty: string;
    table_count: number;
    active_connections: number;
  };
  last_check: string;
  error?: string;
}

// ContainerInfo/ContainerHealth types removed 2026-07-10: orphaned after the
// Docker-socket health check they described was removed for security -- see
// the "REMOVED: checkContainerHealth()" note below.

const SystemHealthStatus: React.FC = () => {
  const [serverStatus, setServerStatus] = useState<ServerStatus>({
    status: 'unknown',
    responseTime: 0,
    activeConnections: 0,
    adminConnections: 0,
    lastChecked: ''
  });
  const [aiHealth, setAiHealth] = useState<AllProvidersHealth | null>(null);
  const [dbHealth, setDbHealth] = useState<DatabaseHealth | null>(null);
  // REMOVED: Container health monitoring no longer available (Docker socket removed for security)
  const [isLoading, setIsLoading] = useState(true);
  const [isExpanded, setIsExpanded] = useState(false);

  const checkServerStatus = async () => {
    try {
      const startTime = Date.now();
      const response = await fetch('/api/v1/status/', {
        method: 'GET',
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('accessToken')}`,
        },
      });
      
      const endTime = Date.now();
      const responseTime = endTime - startTime;

      if (response.ok) {
        const data = await response.json();
        setServerStatus({
          status: 'online',
          responseTime,
          activeConnections: data.active_connections || 0,
          adminConnections: data.admin_connections || 0,
          connectionStats: data.connection_stats,
          lastChecked: new Date().toLocaleTimeString()
        });
      } else {
        setServerStatus(prev => ({
          ...prev,
          status: 'offline',
          lastChecked: new Date().toLocaleTimeString()
        }));
      }
    } catch (error) {
      console.error('Failed to check server status:', error);
      setServerStatus(prev => ({
        ...prev,
        status: 'offline',
        lastChecked: new Date().toLocaleTimeString()
      }));
    }
  };

  const checkAIHealth = async () => {
    try {
      const response = await fetch('/api/v1/status/ai/providers', {
        method: 'GET',
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('accessToken')}`,
        },
      });
      
      if (response.ok) {
        const data = await response.json();
        setAiHealth(data);
      } else {
        console.error('Failed to fetch AI health status');
        setAiHealth(null);
      }
    } catch (error) {
      console.error('Failed to check AI health:', error);
      setAiHealth(null);
    }
  };

  const checkDatabaseHealth = async () => {
    try {
      const response = await fetch('/api/v1/status/database/detailed', {
        method: 'GET',
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('accessToken')}`,
        },
      });
      
      if (response.ok) {
        const data = await response.json();
        setDbHealth(data);
      } else {
        console.error('Failed to fetch database health status');
        setDbHealth(null);
      }
    } catch (error) {
      console.error('Failed to check database health:', error);
      setDbHealth(null);
    }
  };

  // REMOVED: Container health checking no longer available (Docker socket removed for security)

  const checkAllStatus = async () => {
    setIsLoading(true);
    await Promise.all([
      checkServerStatus(),
      checkAIHealth(),
      checkDatabaseHealth()
      // REMOVED: checkContainerHealth() - Docker socket removed for security
    ]);
    setIsLoading(false);
  };

  useEffect(() => {
    checkAllStatus();
    
    // Check status every 30 seconds
    const interval = setInterval(checkAllStatus, 30000);
    
    return () => clearInterval(interval);
  }, []);

  const getOverallStatus = () => {
    if (serverStatus.status !== 'online') return 'offline';
    if (!dbHealth?.connected) return 'degraded';
    // REMOVED: Container health checks (Docker socket removed for security)
    if (aiHealth?.status === 'unavailable') return 'degraded';
    if (aiHealth?.status === 'degraded' || dbHealth?.status === 'degraded') return 'degraded';
    return 'healthy';
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'healthy':
      case 'online': return '#2ecc71';
      case 'degraded': return '#f39c12';
      case 'offline':
      case 'unavailable': return '#e74c3c';
      default: return '#7f8c8d';
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'healthy':
      case 'online': return '✓';
      case 'degraded': return '⚠';
      case 'offline':
      case 'unavailable': return '✗';
      default: return '?';
    }
  };

  const formatLastCheck = (timestamp: string) => {
    try {
      return new Date(timestamp).toLocaleTimeString();
    } catch {
      return timestamp;
    }
  };

  const getPoolUtilization = () => {
    if (!dbHealth?.pool_status) return 0;
    const { size, checked_out } = dbHealth.pool_status;
    return size > 0 ? Math.round((checked_out / size) * 100) : 0;
  };

  const overallStatus = getOverallStatus();

  if (isLoading && !serverStatus.lastChecked) {
    return (
      <div className="system-health-status loading">
        <div className="system-health-header">
          <div className="header-left">
            <span className="system-icon">🛡️</span>
            <h4>System Status</h4>
          </div>
          <span className="loading-spinner">⟳</span>
        </div>
      </div>
    );
  }

  return (
    <div className="system-health-status">
      <div 
        className="system-health-header clickable"
        onClick={() => setIsExpanded(!isExpanded)}
        title="Click to expand/collapse system details"
      >
        <div className="header-left">
          <span className="system-icon">🛡️</span>
          <h4>System Status</h4>
        </div>
        <div className="header-right">
          <span 
            className="status-icon"
            style={{ color: getStatusColor(overallStatus) }}
          >
            {getStatusIcon(overallStatus)}
          </span>
          <button 
            className="refresh-status-btn"
            onClick={(e) => {
              e.stopPropagation();
              checkAllStatus();
            }}
            disabled={isLoading}
            title="Refresh all status"
          >
            {isLoading ? '⟳' : '↻'}
          </button>
          <a 
            href={window.location.origin.replace('-3000.app.github.dev', '-3001.app.github.dev')}
            target="_blank"
            rel="noopener noreferrer"
            className="play-button"
            title="Open Player UI"
            onClick={(e) => e.stopPropagation()}
          >
            ▶️
          </a>
          <span className="expand-icon">
            {isExpanded ? '▼' : '▶'}
          </span>
        </div>
      </div>
      
      <div className="system-health-content">
        <div className="status-summary">
          <div className="service-status">
            <span 
              className="service-indicator"
              style={{ color: getStatusColor(serverStatus.status) }}
              title={`Game Server: ${serverStatus.status} - ${serverStatus.activeConnections} players, ${serverStatus.adminConnections} admins`}
            >
              🎮 {serverStatus.status === 'online' ? 'Online' : 'Offline'} ({serverStatus.activeConnections + serverStatus.adminConnections})
            </span>
            <span className="service-metric">{serverStatus.responseTime}ms</span>
          </div>
          
          <div className="service-status">
            <span 
              className="service-indicator"
              style={{ color: getStatusColor(dbHealth?.status || 'unavailable') }}
              title={`Database: ${dbHealth?.connected ? 'Connected' : 'Disconnected'}`}
            >
              🗄️ {dbHealth?.connected ? 'Connected' : 'Disconnected'}
            </span>
            <span className="service-metric">{dbHealth?.response_time?.toFixed(0) || 0}ms</span>
          </div>
          
          {/* REMOVED: Container health status (Docker socket removed for security) */}
          
          <div className="service-status">
            <span 
              className="service-indicator"
              style={{ color: getStatusColor(aiHealth?.status || 'unavailable') }}
              title={`AI Services: ${aiHealth?.summary.healthy || 0}/${aiHealth?.summary.total || 0} healthy`}
            >
              🤖 {aiHealth?.summary.healthy || 0}/{aiHealth?.summary.total || 0}
            </span>
            <span className="service-metric">{aiHealth?.response_time?.toFixed(0) || 0}ms</span>
          </div>
        </div>
        
        {isExpanded && (
          <div className="system-detail">
            {/* Game Server Details */}
            <div className="service-section">
              <h5 className="service-title">🎮 Game Server</h5>
              <div className="service-metrics">
                <div className="metric-row">
                  <span className="metric-label">Status:</span>
                  <span 
                    className="metric-value"
                    style={{ color: getStatusColor(serverStatus.status) }}
                  >
                    {serverStatus.status}
                  </span>
                </div>
                <div className="metric-row">
                  <span className="metric-label">Response:</span>
                  <span className="metric-value">{serverStatus.responseTime}ms</span>
                </div>
                <div className="metric-row">
                  <span className="metric-label">Player Connections:</span>
                  <span className="metric-value">{serverStatus.activeConnections}</span>
                </div>
                <div className="metric-row">
                  <span className="metric-label">Admin Connections:</span>
                  <span className="metric-value">{serverStatus.adminConnections}</span>
                </div>
                {serverStatus.connectionStats && (
                  <>
                    <div className="metric-row">
                      <span className="metric-label">Active Sectors:</span>
                      <span className="metric-value">{serverStatus.connectionStats.sectors_with_players}</span>
                    </div>
                    <div className="metric-row">
                      <span className="metric-label">Active Teams:</span>
                      <span className="metric-value">{serverStatus.connectionStats.teams_with_players}</span>
                    </div>
                  </>
                )}
                <div className="metric-row">
                  <span className="metric-label">Last Check:</span>
                  <span className="metric-value">{serverStatus.lastChecked}</span>
                </div>
              </div>
            </div>

            {/* Database Details */}
            {dbHealth && (
              <div className="service-section">
                <h5 className="service-title">🗄️ Database</h5>
                <div className="service-metrics">
                  <div className="metric-row">
                    <span className="metric-label">Connection:</span>
                    <span 
                      className="metric-value"
                      style={{ color: dbHealth.connected ? '#2ecc71' : '#e74c3c' }}
                    >
                      {dbHealth.connected ? 'Connected' : 'Disconnected'}
                    </span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">Response:</span>
                    <span className="metric-value">{dbHealth.response_time.toFixed(0)}ms</span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">Pool Usage:</span>
                    <span className="metric-value">
                      {dbHealth.pool_status?.checked_out ?? '—'}/{dbHealth.pool_status?.size ?? '—'} ({getPoolUtilization()}%)
                    </span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">Database:</span>
                    <span className="metric-value">{dbHealth.database_info?.size_pretty ?? '—'}</span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">Last Check:</span>
                    <span className="metric-value">{formatLastCheck(dbHealth.last_check)}</span>
                  </div>
                </div>
              </div>
            )}

            {/* REMOVED: Container health details (Docker socket removed for security) */}

            {/* AI Services Details */}
            {aiHealth && (
              <div className="service-section">
                <h5 className="service-title">🤖 AI Services</h5>
                <div className="service-metrics">
                  <div className="metric-row">
                    <span className="metric-label">Overall:</span>
                    <span 
                      className="metric-value"
                      style={{ color: getStatusColor(aiHealth.status) }}
                    >
                      {aiHealth.summary.healthy}/{aiHealth.summary.total} healthy
                    </span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">Response:</span>
                    <span className="metric-value">{aiHealth.response_time.toFixed(0)}ms</span>
                  </div>
                  {Object.entries(aiHealth.providers).map(([key, provider]) => (
                    <div key={key} className="metric-row">
                      <span className="metric-label">{key}:</span>
                      <span 
                        className="metric-value provider-status-inline"
                        style={{ color: getStatusColor(provider.status) }}
                      >
                        {getStatusIcon(provider.status)}
                        <span className="provider-indicators">
                          <span 
                            className="indicator"
                            style={{ color: provider.configured ? '#2ecc71' : '#7f8c8d' }}
                            title={provider.configured ? 'API key configured' : 'No API key'}
                          >
                            🔑
                          </span>
                          <span 
                            className="indicator"
                            style={{ color: provider.reachable ? '#2ecc71' : '#e74c3c' }}
                            title={provider.reachable ? 'API reachable' : 'API unreachable'}
                          >
                            🌐
                          </span>
                        </span>
                      </span>
                    </div>
                  ))}
                  <div className="metric-row">
                    <span className="metric-label">Last Check:</span>
                    <span className="metric-value">{formatLastCheck(aiHealth.last_check)}</span>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default SystemHealthStatus;