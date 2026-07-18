import React, { useState, useEffect } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import './genesis-device-tracking.css';

interface GenesisDevice {
  id: string;
  name: string;
  status: 'active' | 'dormant' | 'deployed' | 'destroyed';
  ownerId: string;
  ownerName: string;
  teamId?: string;
  teamName?: string;
  location: {
    type: 'ship' | 'planet' | 'space';
    id: string;
    name: string;
    sectorId: string;
    sectorName: string;
  };
  powerLevel: number;
  integrity: number;
  chargeTime: number; // seconds until ready
  deploymentHistory: Array<{
    timestamp: string;
    targetPlanetId: string;
    targetPlanetName: string;
    result: 'success' | 'failure' | 'partial';
    transformationType: string;
  }>;
  createdAt: string;
  lastActivity: string;
}

interface GenesisStats {
  totalDevices: number;
  activeDevices: number;
  deployedThisWeek: number;
  successRate: number;
  averagePowerLevel: number;
  topUsers: Array<{
    playerId: string;
    playerName: string;
    deviceCount: number;
    successfulDeployments: number;
  }>;
}

interface GenesisAlert {
  id: string;
  deviceId: string;
  deviceName: string;
  type: 'security' | 'malfunction' | 'unauthorized' | 'critical';
  message: string;
  timestamp: string;
  severity: 'low' | 'medium' | 'high' | 'critical';
}

export const GenesisDeviceTracking: React.FC = () => {
  useAuth();
  const [devices, setDevices] = useState<GenesisDevice[]>([]);
  const [stats, setStats] = useState<GenesisStats | null>(null);
  const [alerts, setAlerts] = useState<GenesisAlert[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [filterStatus, setFilterStatus] = useState<string>('all');
  const [selectedDevice, setSelectedDevice] = useState<GenesisDevice | null>(null);
  const [showAlerts, setShowAlerts] = useState(true);

  useEffect(() => {
    loadGenesisData();
    const interval = setInterval(loadGenesisData, 30000); // Refresh every 30 seconds
    return () => clearInterval(interval);
  }, []);

  const loadGenesisData = async () => {
    try {
      const token = localStorage.getItem('accessToken');
      const response = await fetch('/api/v1/admin/colonization/genesis-devices', {
        headers: {
          'Authorization': `Bearer ${token}`,
        },
      });

      if (!response.ok) {
        throw new Error('Failed to load Genesis device data');
      }

      const data = await response.json();
      setDevices(data.devices);
      setStats(data.stats);
      setAlerts(data.alerts);
    } catch (err) {
      console.error('Error loading Genesis data:', err);
      // Don't use mock data - show real error state
      setDevices([]);
      setStats(null);
      setAlerts([]);
    } finally {
      setLoading(false);
    }
  };

  const filteredDevices = devices.filter(device => {
    const matchesSearch = device.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      device.ownerName.toLowerCase().includes(searchTerm.toLowerCase()) ||
      device.location.name.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesStatus = filterStatus === 'all' || device.status === filterStatus;
    return matchesSearch && matchesStatus;
  });

  const getStatusColor = (status: GenesisDevice['status']) => {
    switch (status) {
      case 'active': return 'var(--status-success)';
      case 'dormant': return 'var(--status-warning)';
      case 'deployed': return 'var(--status-info)';
      case 'destroyed': return 'var(--status-error)';
      default: return 'var(--text-primary)';
    }
  };

  const getAlertColor = (severity: GenesisAlert['severity']) => {
    switch (severity) {
      case 'critical': return 'var(--status-error)';
      case 'high': return 'var(--status-warning)';
      case 'medium': return 'var(--status-info)';
      case 'low': return 'var(--text-secondary)';
      default: return 'var(--text-primary)';
    }
  };

  const formatTime = (seconds: number) => {
    if (seconds === 0) return 'Ready';
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (hours > 0) return `${hours}h ${minutes}m`;
    return `${minutes}m`;
  };

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleString();
  };

  if (loading) {
    return <div className="genesis-tracking loading">Loading Genesis device data...</div>;
  }

  return (
    <div className="genesis-tracking">
      <div className="tracking-header">
        <h2>Genesis Device Tracking</h2>
      </div>

      <div className="tracking-controls">
        <input
          type="text"
          placeholder="Search devices..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          className="search-input"
        />
        <select
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
          className="filter-select"
        >
          <option value="all">All Status</option>
          <option value="active">Active</option>
          <option value="dormant">Dormant</option>
          <option value="deployed">Deployed</option>
          <option value="destroyed">Destroyed</option>
        </select>
        <button
          className={`alerts-toggle ${showAlerts ? 'active' : ''}`}
          onClick={() => setShowAlerts(!showAlerts)}
        >
          🚨 Alerts ({alerts.length})
        </button>
      </div>

      {showAlerts && alerts.length > 0 && (
        <div className="alerts-section">
          <h3>Active Alerts</h3>
          <div className="alerts-grid">
            {alerts.map(alert => (
              <div
                key={alert.id}
                className={`alert-card ${alert.severity}`}
                style={{ borderColor: getAlertColor(alert.severity) }}
              >
                <div className="alert-header">
                  <span className="alert-type">{alert.type.toUpperCase()}</span>
                  <span className="alert-time">{formatDate(alert.timestamp)}</span>
                </div>
                <div className="alert-device">{alert.deviceName}</div>
                <div className="alert-message">{alert.message}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="devices-grid">
        {filteredDevices.map(device => (
          <div
            key={device.id}
            className="device-card"
            onClick={() => setSelectedDevice(device)}
          >
            <div className="device-header">
              <h3>{device.name}</h3>
              <span
                className="device-status"
                style={{ color: getStatusColor(device.status) }}
              >
                {device.status}
              </span>
            </div>
            
            <div className="device-info">
              <div className="info-row">
                <span className="info-label">Owner:</span>
                <span className="info-value">{device.ownerName}</span>
              </div>
              {device.teamName && (
                <div className="info-row">
                  <span className="info-label">Team:</span>
                  <span className="info-value">{device.teamName}</span>
                </div>
              )}
              <div className="info-row">
                <span className="info-label">Location:</span>
                <span className="info-value">{device.location.name}</span>
              </div>
              <div className="info-row">
                <span className="info-label">Sector:</span>
                <span className="info-value">{device.location.sectorName}</span>
              </div>
            </div>

            <div className="device-metrics">
              <div className="metric">
                <span className="metric-label">Power Level</span>
                <div className="metric-bar">
                  <div
                    className="metric-fill power"
                    style={{
                      width: `${device.powerLevel}%`,
                      backgroundColor: device.powerLevel > 70 ? 'var(--status-success)' :
                        device.powerLevel > 30 ? 'var(--status-warning)' : 'var(--status-error)'
                    }}
                  />
                </div>
              </div>
              <div className="metric">
                <span className="metric-label">Integrity</span>
                <div className="metric-bar">
                  <div
                    className="metric-fill integrity"
                    style={{
                      width: `${device.integrity}%`,
                      backgroundColor: device.integrity > 70 ? 'var(--status-success)' :
                        device.integrity > 30 ? 'var(--status-warning)' : 'var(--status-error)'
                    }}
                  />
                </div>
              </div>
            </div>

            <div className="device-footer">
              <span className="charge-time">
                {device.status === 'active' ? '⚡ Ready' : `⏱️ ${formatTime(device.chargeTime)}`}
              </span>
              <span className="deployment-count">
                📊 {device.deploymentHistory.length} deployments
              </span>
            </div>
          </div>
        ))}
      </div>

      <div className="header-stats">
          <div className="stat-card">
            <span className="stat-label">Total Devices</span>
            <span className="stat-value">{stats?.totalDevices || 0}</span>
          </div>
          <div className="stat-card">
            <span className="stat-label">Active</span>
            <span className="stat-value success">{stats?.activeDevices || 0}</span>
          </div>
          <div className="stat-card">
            <span className="stat-label">Deployed This Week</span>
            <span className="stat-value">{stats?.deployedThisWeek || 0}</span>
          </div>
          <div className="stat-card">
            <span className="stat-label">Success Rate</span>
            <span className="stat-value">{Math.round(stats?.successRate || 0)}%</span>
          </div>
          <div className="stat-card">
            <span className="stat-label">Avg Power</span>
            <span className="stat-value">{Math.round(stats?.averagePowerLevel || 0)}%</span>
          </div>
        </div>

      {selectedDevice && (
        <div className="device-detail-modal" onClick={() => setSelectedDevice(null)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <h2>{selectedDevice.name} Details</h2>
            <button className="close-button" onClick={() => setSelectedDevice(null)}>×</button>
            
            <div className="detail-sections">
              <div className="detail-section">
                <h3>Device Information</h3>
                <div className="detail-row">
                  <span>Status:</span>
                  <span style={{ color: getStatusColor(selectedDevice.status) }}>
                    {selectedDevice.status}
                  </span>
                </div>
                <div className="detail-row">
                  <span>Created:</span>
                  <span>{formatDate(selectedDevice.createdAt)}</span>
                </div>
                <div className="detail-row">
                  <span>Last Activity:</span>
                  <span>{formatDate(selectedDevice.lastActivity)}</span>
                </div>
                <div className="detail-row">
                  <span>Location Type:</span>
                  <span>{selectedDevice.location.type}</span>
                </div>
              </div>

              <div className="detail-section">
                <h3>Deployment History</h3>
                {selectedDevice.deploymentHistory.length === 0 ? (
                  <p className="no-deployments">No deployments recorded</p>
                ) : (
                  <div className="deployment-list">
                    {selectedDevice.deploymentHistory.map((deployment, index) => (
                      <div key={index} className="deployment-item">
                        <div className="deployment-header">
                          <span className="deployment-planet">{deployment.targetPlanetName}</span>
                          <span className={`deployment-result ${deployment.result}`}>
                            {deployment.result}
                          </span>
                        </div>
                        <div className="deployment-details">
                          <span>{deployment.transformationType}</span>
                          <span>{formatDate(deployment.timestamp)}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div
                role="note"
                style={{
                  margin: '12px 0 0 0',
                  padding: '10px 12px',
                  background: 'rgba(234, 179, 8, 0.12)',
                  border: '1px solid rgba(234, 179, 8, 0.35)',
                  borderRadius: '6px',
                  color: '#fbbf24',
                  fontSize: '0.82rem',
                  lineHeight: 1.4,
                }}
              >
                Device detail actions (track location, view owner, monitor activity, disable
                device, investigate destruction) are unavailable — no admin backend exists for
                them. This drawer does not invent an Actions button bar.
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};