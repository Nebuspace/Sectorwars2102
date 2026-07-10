import React, { useState, useEffect } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import './central-nexus-manager.css';

interface NexusStatus {
  exists: boolean;
  status: string;
  nexus_id?: string;
  created_at?: string;
  total_sectors: number;
  total_ports: number;
  total_planets: number;
}

interface Cluster {
  cluster_id: string;
  name: string;
  cluster_type: string;
  sector_count: number;
  ports_count: number;
  planets_count: number;
  avg_security_level: number;
  avg_development_level: number;
  is_discovered: boolean;
  economic_value: number;
}

interface NexusStats {
  total_sectors: number;
  total_ports: number;
  total_planets: number;
  total_warp_gates: number;
  // null = no telemetry exists for this metric (gameserver does not track it)
  active_players: number | null;
  daily_traffic: number | null;
  clusters: Array<Record<string, unknown>>;
}

const CentralNexusManager: React.FC = () => {
  const { token } = useAuth();
  const [nexusStatus, setNexusStatus] = useState<NexusStatus | null>(null);
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [stats, setStats] = useState<NexusStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'overview' | 'clusters'>('overview');

  useEffect(() => {
    loadNexusStatus();
    loadClusters();
    loadStats();
  }, []);

  const loadNexusStatus = async () => {
    try {
      const response = await fetch('/api/v1/nexus/status', {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (response.ok) {
        const data = await response.json();
        setNexusStatus(data);
      }
    } catch (err) {
      console.error('Failed to load nexus status:', err);
    }
  };

  const loadClusters = async () => {
    setLoading(true);
    try {
      const response = await fetch('/api/v1/nexus/clusters', {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (response.ok) {
        const data = await response.json();
        setClusters(data);
      } else if (response.status !== 404) {
        const errData = await response.json().catch(() => ({ detail: 'Failed to load clusters' }));
        setError(errData.detail || 'Failed to load clusters');
      }
    } catch (err) {
      console.error('Failed to load clusters:', err);
      setError('Network error while loading clusters');
    } finally {
      setLoading(false);
    }
  };

  const loadStats = async () => {
    try {
      const response = await fetch('/api/v1/nexus/stats', {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (response.ok) {
        const data = await response.json();
        setStats(data);
      }
    } catch (err) {
      console.error('Failed to load stats:', err);
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'active': return 'status-active';
      case 'generating': return 'status-generating';
      case 'not_generated': return 'status-not-generated';
      default: return 'status-unknown';
    }
  };

  // Renders an em-dash for null/undefined (no telemetry) — never a fake '0'
  const formatNumber = (num: number | null | undefined) => {
    return num != null ? num.toLocaleString() : '—';
  };

  const formatClusterType = (clusterType: string) => {
    return clusterType.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
  };

  return (
    <div className="central-nexus-manager">
      <div className="nexus-header">
        <h1>Central Nexus Management</h1>
        <p>Manage the galactic hub connecting all regional territories</p>
      </div>

      {error && (
        <div className="error-message">
          {error}
        </div>
      )}

      <div className="nexus-tabs">
        <button
          className={`tab-button ${activeTab === 'overview' ? 'active' : ''}`}
          onClick={() => setActiveTab('overview')}
        >
          Overview
        </button>
        <button
          className={`tab-button ${activeTab === 'clusters' ? 'active' : ''}`}
          onClick={() => setActiveTab('clusters')}
        >
          Clusters
        </button>
      </div>

      <div className="nexus-content">
        {activeTab === 'overview' && (
          <div className="overview-tab">
            {/* Status Card */}
            <div className="status-card">
              <h3>Central Nexus Status</h3>
              {nexusStatus ? (
                <div className="status-info">
                  <div className="status-item">
                    <label>Status:</label>
                    <span className={`status-badge ${getStatusColor(nexusStatus.status)}`}>
                      {nexusStatus.exists ? nexusStatus.status : 'Not Generated'}
                    </span>
                  </div>
                  {nexusStatus.exists && (
                    <>
                      <div className="status-item">
                        <label>Created:</label>
                        <span>{nexusStatus.created_at ? new Date(nexusStatus.created_at).toLocaleDateString() : 'Unknown'}</span>
                      </div>
                      <div className="status-item">
                        <label>Nexus ID:</label>
                        <span className="nexus-id">{nexusStatus.nexus_id}</span>
                      </div>
                    </>
                  )}
                </div>
              ) : (
                <div className="loading">Loading status...</div>
              )}
            </div>

            {/* Statistics Cards */}
            {stats && nexusStatus?.exists && (
              <div className="stats-grid">
                <div className="stat-card">
                  <h4>Sectors</h4>
                  <div className="stat-value">{formatNumber(stats.total_sectors)}</div>
                </div>
                <div className="stat-card">
                  <h4>Ports</h4>
                  <div className="stat-value">{formatNumber(stats.total_ports)}</div>
                </div>
                <div className="stat-card">
                  <h4>Planets</h4>
                  <div className="stat-value">{formatNumber(stats.total_planets)}</div>
                </div>
                <div className="stat-card">
                  <h4>Warp Gates</h4>
                  <div className="stat-value">{formatNumber(stats.total_warp_gates)}</div>
                </div>
                <div className="stat-card">
                  <h4>Active Players</h4>
                  <div
                    className="stat-value"
                    title={stats.active_players == null ? 'no telemetry exists' : undefined}
                  >
                    {formatNumber(stats.active_players)}
                  </div>
                </div>
                <div className="stat-card">
                  <h4>Daily Traffic</h4>
                  <div
                    className="stat-value"
                    title={stats.daily_traffic == null ? 'no telemetry exists' : undefined}
                  >
                    {formatNumber(stats.daily_traffic)}
                  </div>
                </div>
              </div>
            )}

            {/* Quick Actions */}
            <div className="quick-actions">
              <h3>Quick Actions</h3>
              <div className="action-buttons">
                <button
                  onClick={() => loadNexusStatus()}
                  className="action-button refresh"
                  disabled={loading}
                >
                  Refresh Status
                </button>
                <button
                  onClick={() => loadStats()}
                  className="action-button refresh"
                  disabled={loading}
                >
                  Refresh Stats
                </button>
              </div>
              {!nexusStatus?.exists && (
                <div className="info-card" style={{ marginTop: '16px' }}>
                  <p>
                    Central Nexus auto-generates when you create a new galaxy.
                    Use the &quot;Bang a New Galaxy!&quot; button on the Universe Management page.
                  </p>
                </div>
              )}
            </div>
          </div>
        )}

        {activeTab === 'clusters' && (
          <div className="districts-tab">
            <h3>Cluster Overview</h3>
            <div className="info-card" style={{ marginBottom: '16px' }}>
              <p>Cluster regeneration is offline — no regeneration endpoint exists.</p>
            </div>
            {clusters.length > 0 ? (
              <div className="districts-table">
                <table>
                  <thead>
                    <tr>
                      <th>Cluster Name</th>
                      <th>Type</th>
                      <th>Sectors</th>
                      <th>Ports</th>
                      <th>Planets</th>
                      <th>Security</th>
                      <th>Development</th>
                      <th>Economic Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    {clusters.map((cluster) => (
                      <tr key={cluster.cluster_id}>
                        <td className="district-name">{cluster.name}</td>
                        <td>{formatClusterType(cluster.cluster_type)}</td>
                        <td>{formatNumber(cluster.sector_count)}</td>
                        <td>{formatNumber(cluster.ports_count)}</td>
                        <td>{formatNumber(cluster.planets_count)}</td>
                        <td>
                          <div className="security-level">
                            <div className="security-bar">
                              <div
                                className="security-fill"
                                style={{ width: `${(cluster.avg_security_level / 10) * 100}%` }}
                              ></div>
                            </div>
                            <span>{cluster.avg_security_level.toFixed(1)}/10</span>
                          </div>
                        </td>
                        <td>
                          <div className="development-level">
                            <div className="development-bar">
                              <div
                                className="development-fill"
                                style={{ width: `${(cluster.avg_development_level / 10) * 100}%` }}
                              ></div>
                            </div>
                            <span>{cluster.avg_development_level.toFixed(1)}/10</span>
                          </div>
                        </td>
                        <td>{formatNumber(cluster.economic_value)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : loading ? (
              <div className="no-districts">Loading clusters...</div>
            ) : (
              <div className="no-districts">
                {nexusStatus?.exists
                  ? 'No clusters reported for the Central Nexus'
                  : 'Central Nexus not generated yet'}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default CentralNexusManager;