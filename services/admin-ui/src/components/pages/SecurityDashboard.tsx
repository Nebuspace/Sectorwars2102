import React, { useState, useEffect } from 'react';
import PageHeader from '../ui/PageHeader';
import { AuditLogViewer } from '../security/AuditLogViewer';
import { MFASetup } from '../auth/MFASetup';
import { useAuth } from '../../contexts/AuthContext';
import { api } from '../../utils/auth';
import './security-dashboard.css';

// Shape of GET /api/v1/admin/security/report
// (admin_comprehensive.py -> AISecurityService.generate_security_report)
interface SecurityReport {
  timestamp: string;
  players: {
    total: number;
    blocked: number;
    high_risk: number;
    blocked_percentage: number;
  };
  violations: {
    total: number;
    by_type: Record<string, number>;
    average_per_player: number;
  };
  costs: {
    total_today_usd: number;
    average_per_player_usd: number;
    highest_spender: [string, number] | null;
    players_over_limit: number;
  };
  rate_limits: {
    requests_per_minute: number;
    requests_per_hour: number;
    requests_per_day: number;
    max_cost_per_day_usd: number;
  };
}

// Shape of GET /api/v1/admin/security/alerts
interface SecurityAlert {
  type: string;
  severity: string;
  message: string;
  details: unknown;
  timestamp: string;
}

interface SecurityAlertsResponse {
  alerts: SecurityAlert[];
  alert_count: number;
  high_priority_count: number;
}

export const SecurityDashboard: React.FC = () => {
  const { user } = useAuth();
  const [report, setReport] = useState<SecurityReport | null>(null);
  const [alerts, setAlerts] = useState<SecurityAlertsResponse | null>(null);
  const [overviewError, setOverviewError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showMFASetup, setShowMFASetup] = useState(false);
  const [activeTab, setActiveTab] = useState<'overview' | 'audit' | 'threats' | 'settings'>('overview');

  useEffect(() => {
    fetchSecurityOverview();
    const interval = setInterval(fetchSecurityOverview, 30000); // Refresh every 30 seconds
    return () => clearInterval(interval);
  }, []);

  const fetchSecurityOverview = async () => {
    const [reportResult, alertsResult] = await Promise.allSettled([
      api.get('/api/v1/admin/security/report'),
      api.get('/api/v1/admin/security/alerts')
    ]);

    const failures: string[] = [];

    if (reportResult.status === 'fulfilled') {
      setReport(reportResult.value.data as SecurityReport);
    } else {
      const reason: any = reportResult.reason;
      failures.push(`security report: ${reason?.response?.data?.detail || reason?.message || 'request failed'}`);
    }

    if (alertsResult.status === 'fulfilled') {
      setAlerts(alertsResult.value.data as SecurityAlertsResponse);
    } else {
      const reason: any = alertsResult.reason;
      failures.push(`security alerts: ${reason?.response?.data?.detail || reason?.message || 'request failed'}`);
    }

    setOverviewError(failures.length > 0 ? `Failed to load ${failures.join('; ')}` : null);
    setLoading(false);
  };

  const getSeverityClass = (severity: string) => {
    return `severity-${severity}`;
  };

  return (
    <div className="security-dashboard">
      <PageHeader
        title="Security Dashboard"
        subtitle="Monitor and manage system security"
      />

      <div className="security-tabs">
        <button
          className={`tab ${activeTab === 'overview' ? 'active' : ''}`}
          onClick={() => setActiveTab('overview')}
        >
          <i className="fas fa-chart-line"></i>
          Overview
        </button>
        <button
          className={`tab ${activeTab === 'audit' ? 'active' : ''}`}
          onClick={() => setActiveTab('audit')}
        >
          <i className="fas fa-history"></i>
          Audit Logs
        </button>
        <button
          className={`tab ${activeTab === 'threats' ? 'active' : ''}`}
          onClick={() => setActiveTab('threats')}
        >
          <i className="fas fa-shield-alt"></i>
          Threat Detection
        </button>
        <button
          className={`tab ${activeTab === 'settings' ? 'active' : ''}`}
          onClick={() => setActiveTab('settings')}
        >
          <i className="fas fa-cog"></i>
          Settings
        </button>
      </div>

      {activeTab === 'overview' && (
        <div className="security-overview">
          {loading ? (
            <div className="loading-state">
              <i className="fas fa-spinner fa-spin"></i>
              <span>Loading security overview...</span>
            </div>
          ) : (
            <>
              {overviewError && (
                <div
                  role="alert"
                  style={{
                    margin: '0 0 16px 0',
                    padding: '16px',
                    background: 'rgba(239, 68, 68, 0.1)',
                    border: '1px solid rgba(239, 68, 68, 0.4)',
                    borderRadius: '8px',
                    color: '#fca5a5',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '12px'
                  }}
                >
                  <i className="fas fa-exclamation-circle"></i>
                  <span>{overviewError}</span>
                  <button
                    onClick={() => { setLoading(true); fetchSecurityOverview(); }}
                    style={{
                      marginLeft: 'auto',
                      padding: '6px 12px',
                      background: '#374151',
                      color: '#e5e7eb',
                      border: '1px solid #4b5563',
                      borderRadius: '6px',
                      cursor: 'pointer'
                    }}
                  >
                    Retry
                  </button>
                </div>
              )}

              {report && (
                <div className="security-metrics">
                  <div className="metric-card">
                    <div className="metric-icon">
                      <i className="fas fa-users"></i>
                    </div>
                    <div className="metric-content">
                      <h3>Tracked Players</h3>
                      <div className="metric-value">{report.players.total.toLocaleString()}</div>
                      <div className="metric-label">AI security profiles</div>
                    </div>
                  </div>

                  <div className="metric-card alert">
                    <div className="metric-icon">
                      <i className="fas fa-ban"></i>
                    </div>
                    <div className="metric-content">
                      <h3>Blocked Players</h3>
                      <div className="metric-value">{report.players.blocked}</div>
                      <div className="metric-label">
                        {report.players.blocked_percentage.toFixed(1)}% of tracked players
                      </div>
                    </div>
                  </div>

                  <div className="metric-card warning">
                    <div className="metric-icon">
                      <i className="fas fa-exclamation-triangle"></i>
                    </div>
                    <div className="metric-content">
                      <h3>High-Risk Players</h3>
                      <div className="metric-value">{report.players.high_risk}</div>
                      <div className="metric-label">Trust score below 0.3</div>
                    </div>
                  </div>

                  <div className="metric-card warning">
                    <div className="metric-icon">
                      <i className="fas fa-flag"></i>
                    </div>
                    <div className="metric-content">
                      <h3>Violations</h3>
                      <div className="metric-value">{report.violations.total}</div>
                      <div className="metric-label">
                        {report.violations.average_per_player.toFixed(2)} avg per player
                      </div>
                    </div>
                  </div>

                  <div className="metric-card">
                    <div className="metric-icon">
                      <i className="fas fa-dollar-sign"></i>
                    </div>
                    <div className="metric-content">
                      <h3>AI Cost Today</h3>
                      <div className="metric-value">${report.costs.total_today_usd.toFixed(4)}</div>
                      <div className="metric-label">
                        {report.costs.players_over_limit} player(s) near daily limit
                      </div>
                    </div>
                  </div>

                  <div className="metric-card">
                    <div className="metric-icon">
                      <i className="fas fa-tachometer-alt"></i>
                    </div>
                    <div className="metric-content">
                      <h3>Rate Limits</h3>
                      <div className="metric-value">{report.rate_limits.requests_per_minute}/min</div>
                      <div className="metric-label">
                        {report.rate_limits.requests_per_day}/day, ${report.rate_limits.max_cost_per_day_usd}/day cap
                      </div>
                    </div>
                  </div>
                </div>
              )}

              <div className="recent-threats">
                <h3>
                  Security Alerts
                  {alerts ? ` (${alerts.alert_count}${alerts.high_priority_count > 0 ? `, ${alerts.high_priority_count} high priority` : ''})` : ''}
                </h3>
                <div className="threats-list">
                  {alerts && alerts.alerts.length > 0 ? (
                    alerts.alerts.map((alert, index) => (
                      <div key={`${alert.type}-${index}`} className={`threat-item ${getSeverityClass(alert.severity)}`}>
                        <div className="threat-header">
                          <div className="threat-type">
                            <i className="fas fa-exclamation-circle"></i>
                            {alert.type.replace(/_/g, ' ')}
                          </div>
                          <div className={`threat-status severity-${alert.severity}`}>
                            {alert.severity}
                          </div>
                        </div>
                        <div className="threat-description">{alert.message}</div>
                        <div className="threat-timestamp">
                          {new Date(alert.timestamp).toLocaleString()}
                        </div>
                      </div>
                    ))
                  ) : alerts ? (
                    <div className="no-threats" style={{ padding: '20px', textAlign: 'center', color: '#9ca3af' }}>
                      No active security alerts.
                    </div>
                  ) : (
                    <div className="no-threats" style={{ padding: '20px', textAlign: 'center', color: '#9ca3af' }}>
                      Alerts unavailable — see error above.
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {activeTab === 'audit' && (
        <div className="security-audit">
          <AuditLogViewer />
        </div>
      )}

      {activeTab === 'threats' && (
        <div className="security-threats">
          <div className="threat-detection-panel">
            <h3>Threat Detection Rules</h3>
            <div
              role="note"
              style={{
                margin: '0 0 16px 0', padding: '10px 12px',
                background: 'rgba(234, 179, 8, 0.12)', border: '1px solid rgba(234, 179, 8, 0.35)',
                borderRadius: '6px', color: '#fbbf24', fontSize: '0.82rem', lineHeight: 1.4
              }}
            >
              Threat-detection rule configuration is unavailable: no backend endpoint
              exists to persist rule changes. The rules below reflect server-side
              defaults and are read-only.
            </div>
            <div className="detection-rules">
              <div className="rule-item active">
                <div className="rule-header">
                  <span className="rule-name">Brute Force Detection</span>
                  <label className="toggle">
                    <input type="checkbox" defaultChecked disabled />
                    <span className="toggle-slider"></span>
                  </label>
                </div>
                <div className="rule-config">
                  Threshold: 5 failed attempts in 5 minutes
                </div>
              </div>
              <div className="rule-item active">
                <div className="rule-header">
                  <span className="rule-name">API Rate Limiting</span>
                  <label className="toggle">
                    <input type="checkbox" defaultChecked disabled />
                    <span className="toggle-slider"></span>
                  </label>
                </div>
                <div className="rule-config">
                  Limit: 100 requests per minute per user
                </div>
              </div>
              <div className="rule-item">
                <div className="rule-header">
                  <span className="rule-name">Suspicious Pattern Detection</span>
                  <label className="toggle">
                    <input type="checkbox" disabled />
                    <span className="toggle-slider"></span>
                  </label>
                </div>
                <div className="rule-config">
                  AI-powered anomaly detection (disabled)
                </div>
              </div>
            </div>
          </div>

          <div className="blocked-ips-panel">
            <h3>IP Blocklist Management</h3>
            <div
              role="note"
              style={{
                margin: '0 0 16px 0', padding: '10px 12px',
                background: 'rgba(234, 179, 8, 0.12)', border: '1px solid rgba(234, 179, 8, 0.35)',
                borderRadius: '6px', color: '#fbbf24', fontSize: '0.82rem', lineHeight: 1.4
              }}
            >
              IP blocklist management is unavailable: no backend endpoint exists to
              add or remove blocked IPs. This list is read-only.
            </div>
            <div className="ip-blocklist">
              <div className="add-ip-form">
                <input type="text" placeholder="Enter IP address to block" disabled />
                <button className="btn btn-primary" disabled title="Disabled — no IP blocklist backend endpoint">
                  <i className="fas fa-plus"></i>
                  Add to Blocklist
                </button>
              </div>
              <div className="blocked-ips-list">
                <div style={{ padding: '20px', textAlign: 'center', color: '#9ca3af' }}>
                  <i className="fas fa-shield-alt" style={{ fontSize: '1.5rem', marginBottom: '8px', display: 'block' }}></i>
                  No blocked IPs. The blocklist is empty.
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {activeTab === 'settings' && (
        <div className="security-settings">
          <div className="settings-section">
            <h3>Multi-Factor Authentication</h3>
            <div className="mfa-status">
              <div className="status-info">
                <i className="fas fa-shield-alt"></i>
                <div>
                  <h4>Your MFA Status</h4>
                  <p>{user?.mfaEnabled ? 'MFA is enabled for your account' : 'MFA is not enabled for your account'}</p>
                </div>
              </div>
              {!user?.mfaEnabled && (
                <button 
                  className="btn btn-primary"
                  onClick={() => setShowMFASetup(true)}
                >
                  Enable MFA
                </button>
              )}
            </div>
          </div>

          <div className="settings-section">
            <h3>Security Policies</h3>
            <div
              role="note"
              style={{
                margin: '0 0 16px 0', padding: '10px 12px',
                background: 'rgba(234, 179, 8, 0.12)', border: '1px solid rgba(234, 179, 8, 0.35)',
                borderRadius: '6px', color: '#fbbf24', fontSize: '0.82rem', lineHeight: 1.4
              }}
            >
              Security-policy editing is unavailable: no backend endpoint exists to
              persist policy changes. The values below reflect server-side defaults
              and are read-only.
            </div>
            <div className="policy-list">
              <div className="policy-item">
                <div className="policy-header">
                  <h4>Password Requirements</h4>
                  <button className="btn btn-secondary" disabled title="Disabled — no policy-config backend endpoint">
                    <i className="fas fa-edit"></i>
                    Edit
                  </button>
                </div>
                <ul className="policy-rules">
                  <li>Minimum 12 characters</li>
                  <li>At least one uppercase letter</li>
                  <li>At least one number</li>
                  <li>At least one special character</li>
                </ul>
              </div>
              <div className="policy-item">
                <div className="policy-header">
                  <h4>Session Management</h4>
                  <button className="btn btn-secondary" disabled title="Disabled — no policy-config backend endpoint">
                    <i className="fas fa-edit"></i>
                    Edit
                  </button>
                </div>
                <ul className="policy-rules">
                  <li>Session timeout: 30 minutes</li>
                  <li>Maximum concurrent sessions: 3</li>
                  <li>Remember me duration: 7 days</li>
                </ul>
              </div>
            </div>
          </div>

          <div className="settings-section">
            <h3>Security Headers</h3>
            <div className="headers-status">
              <div className="header-item enabled">
                <i className="fas fa-check-circle"></i>
                <span>X-Frame-Options: DENY</span>
              </div>
              <div className="header-item enabled">
                <i className="fas fa-check-circle"></i>
                <span>X-Content-Type-Options: nosniff</span>
              </div>
              <div className="header-item enabled">
                <i className="fas fa-check-circle"></i>
                <span>Strict-Transport-Security: max-age=31536000</span>
              </div>
              <div className="header-item enabled">
                <i className="fas fa-check-circle"></i>
                <span>Content-Security-Policy: default-src 'self'</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {showMFASetup && (
        <div className="mfa-modal">
          <div className="mfa-modal-content">
            <MFASetup
              onSetupComplete={() => {
                setShowMFASetup(false);
                // Refresh user data to update MFA status
                window.location.reload();
              }}
              onCancel={() => setShowMFASetup(false)}
            />
          </div>
        </div>
      )}
    </div>
  );
};