import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import PageHeader from '../ui/PageHeader';
import { AuditLogViewer } from '../security/AuditLogViewer';
import { MFASetup } from '../auth/MFASetup';
import { useAuth } from '../../contexts/AuthContext';
import { api } from '../../utils/auth';
import { formatAdminApiError, formatSettledRejection } from '../../utils/adminApiError';
import { useToast, useConfirm } from '../../contexts/ToastContext';
import { useSystemAlerts } from '../../contexts/WebSocketContext';
import './security-dashboard.css';

type PlayerSecurityActionKind = 'block' | 'unblock' | 'reset_violations' | 'reset_trust';

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
  const toast = useToast();
  const confirm = useConfirm();
  const [report, setReport] = useState<SecurityReport | null>(null);
  const [alerts, setAlerts] = useState<SecurityAlertsResponse | null>(null);
  const [overviewError, setOverviewError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showMFASetup, setShowMFASetup] = useState(false);
  const [activeTab, setActiveTab] = useState<'overview' | 'audit' | 'threats' | 'settings'>('overview');
  const [daysToKeep, setDaysToKeep] = useState(7);
  const [playerId, setPlayerId] = useState('');
  const [playerAction, setPlayerAction] = useState<PlayerSecurityActionKind>('block');
  const [durationHours, setDurationHours] = useState('');
  const [actionReason, setActionReason] = useState('');

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
      failures.push(
        formatSettledRejection(
          reportResult.reason,
          'security report',
          'admin security report scope required'
        )
      );
    }

    if (alertsResult.status === 'fulfilled') {
      setAlerts(alertsResult.value.data as SecurityAlertsResponse);
    } else {
      failures.push(
        formatSettledRejection(
          alertsResult.reason,
          'security alerts',
          'admin security alerts scope required'
        )
      );
    }

    setOverviewError(failures.length > 0 ? failures.join(' | ') : null);
    setLoading(false);
  };

  const securityActError = (err: unknown, fallback: string) =>
    formatAdminApiError(err, {
      fallback,
      scopeHint: 'SECURITY_ACT scope required',
    });

  /** Tip GS: POST /api/v1/admin/security/cleanup — query days_to_keep (LEG-1713). */
  const handleSecurityCleanup = async () => {
    if (!(await confirm({
      title: 'Clean up old security data',
      message:
        `Remove security tracking older than ${daysToKeep} day(s)? This posts the tip admin security/cleanup route (SECURITY_ACT).`,
      confirmLabel: 'Clean up',
    }))) {
      return;
    }

    try {
      const response = await api.post('/api/v1/admin/security/cleanup', null, {
        params: { days_to_keep: daysToKeep },
      });
      const data = response.data as { message?: string; days_kept?: number } | undefined;
      toast.success(
        data?.message ?? `Cleaned up security data older than ${data?.days_kept ?? daysToKeep} days`
      );
      void fetchSecurityOverview();
    } catch (err: unknown) {
      toast.error(securityActError(err, 'Failed to clean up security data'));
    }
  };

  /** Tip GS: POST /api/v1/admin/security/player/{id}/action (LEG-1713). */
  const handlePlayerSecurityAction = async () => {
    const id = playerId.trim();
    if (!id) {
      toast.error('Player id is required');
      return;
    }
    if (playerAction === 'block' && durationHours.trim() === '') {
      toast.error('Block duration in hours is required');
      return;
    }

    const body: {
      action: PlayerSecurityActionKind;
      duration_hours?: number;
      reason?: string;
    } = { action: playerAction };
    if (playerAction === 'block') {
      body.duration_hours = Number(durationHours);
    }
    const reason = actionReason.trim();
    if (reason) {
      body.reason = reason;
    }

    try {
      const response = await api.post(
        `/api/v1/admin/security/player/${id}/action`,
        body
      );
      const data = response.data as { message?: string } | undefined;
      toast.success(data?.message ?? `Player security action ${playerAction} completed`);
      void fetchSecurityOverview();
    } catch (err: unknown) {
      toast.error(securityActError(err, 'Failed to take player security action'));
    }
  };

  // Live updates: complements the 30s poll above with an immediate refresh on
  // alert/performance/security events, so a new high-priority alert doesn't
  // sit for up to 30s. fetchSecurityOverview closes over no component state
  // (only calls setters), so passing it directly is safe here.
  useSystemAlerts(fetchSecurityOverview, fetchSecurityOverview, fetchSecurityOverview);

  const getSeverityClass = (severity: string) => {
    return `severity-${severity}`;
  };

  return (
    <div className="security-dashboard">
      <PageHeader
        title="Security Dashboard"
        subtitle="Monitor security alerts and the HTTP audit trail"
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
          HTTP Audit (legacy)
        </button>
        <button
          className={`tab ${activeTab === 'threats' ? 'active' : ''}`}
          onClick={() => setActiveTab('threats')}
        >
          <i className="fas fa-shield-alt"></i>
          Threats — unavailable
        </button>
        <button
          className={`tab ${activeTab === 'settings' ? 'active' : ''}`}
          onClick={() => setActiveTab('settings')}
        >
          <i className="fas fa-cog"></i>
          Settings — partial
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

              <div className="security-ops" role="region" aria-label="Security operations">
                <h3>Security operations</h3>
                <p className="security-ops-note">
                  Tip routes only: cleanup old security tracking, or take a player security action
                  (block, unblock, reset violations, reset trust). SECURITY_ACT required.
                </p>
                <div className="security-ops-row">
                  <label>
                    Days to keep
                    <input
                      type="number"
                      min={1}
                      max={30}
                      value={daysToKeep}
                      onChange={(e) => setDaysToKeep(Number(e.target.value) || 7)}
                      aria-label="Days of security data to keep"
                    />
                  </label>
                  <button
                    type="button"
                    className="btn btn-outline"
                    onClick={handleSecurityCleanup}
                    aria-label="Clean up old security data"
                  >
                    Clean up old data
                  </button>
                </div>
                <div className="security-ops-row">
                  <label>
                    Player id
                    <input
                      type="text"
                      value={playerId}
                      onChange={(e) => setPlayerId(e.target.value)}
                      aria-label="Player id for security action"
                      placeholder="player uuid"
                    />
                  </label>
                  <label>
                    Action
                    <select
                      value={playerAction}
                      onChange={(e) => setPlayerAction(e.target.value as PlayerSecurityActionKind)}
                      aria-label="Player security action type"
                    >
                      <option value="block">block</option>
                      <option value="unblock">unblock</option>
                      <option value="reset_violations">reset_violations</option>
                      <option value="reset_trust">reset_trust</option>
                    </select>
                  </label>
                  {playerAction === 'block' && (
                    <label>
                      Duration (hours)
                      <input
                        type="number"
                        min={1}
                        value={durationHours}
                        onChange={(e) => setDurationHours(e.target.value)}
                        aria-label="Block duration in hours"
                      />
                    </label>
                  )}
                  <label>
                    Reason (optional)
                    <input
                      type="text"
                      value={actionReason}
                      onChange={(e) => setActionReason(e.target.value)}
                      aria-label="Reason for player security action"
                    />
                  </label>
                  <button
                    type="button"
                    className="btn btn-primary"
                    onClick={handlePlayerSecurityAction}
                    aria-label="Take player security action"
                  >
                    Take action
                  </button>
                </div>
              </div>

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
          <div
            role="note"
            style={{
              margin: '0 0 16px 0',
              padding: '10px 12px',
              background: 'rgba(59, 130, 246, 0.12)',
              border: '1px solid rgba(59, 130, 246, 0.35)',
              borderRadius: '6px',
              color: '#93c5fd',
              fontSize: '0.85rem',
              lineHeight: 1.45,
            }}
          >
            This tab is the <strong>legacy HTTP request audit trail</strong>
            {' '}(<code>/admin/audit/logs</code>). For RBAC accountability
            (scoped admin mutations, review queue), use{' '}
            <Link to="/audit" style={{ color: '#bfdbfe', fontWeight: 600 }}>
              Action Log
            </Link>
            {' '}in the sidebar — that is the append-only AdminActionLog.
          </div>
          <AuditLogViewer />
        </div>
      )}

      {activeTab === 'threats' && (
        <div className="security-threats">
          <div
            role="note"
            style={{
              margin: '0 0 16px 0', padding: '10px 12px',
              background: 'rgba(234, 179, 8, 0.12)', border: '1px solid rgba(234, 179, 8, 0.35)',
              borderRadius: '6px', color: '#fbbf24', fontSize: '0.82rem', lineHeight: 1.4
            }}
          >
            Threat-detection rule configuration is unavailable: no admin API exists
            to load or persist detection rules. Live rate-limit / block metrics on
            Overview come from <code style={{ color: '#fde68a' }}>/admin/security/report</code>.
            This tab does not invent a Rules panel, thresholds, or toggle state.
          </div>
          <div
            role="note"
            style={{
              margin: '0 0 16px 0', padding: '10px 12px',
              background: 'rgba(234, 179, 8, 0.12)', border: '1px solid rgba(234, 179, 8, 0.35)',
              borderRadius: '6px', color: '#fbbf24', fontSize: '0.82rem', lineHeight: 1.4
            }}
          >
            IP blocklist management is unavailable: no backend endpoint exists to
            list, add, or remove blocked IPs. This tab does not invent a Blocklist
            Management panel or disabled Add controls.
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
            <div
              role="note"
              style={{
                margin: '0 0 16px 0', padding: '10px 12px',
                background: 'rgba(234, 179, 8, 0.12)', border: '1px solid rgba(234, 179, 8, 0.35)',
                borderRadius: '6px', color: '#fbbf24', fontSize: '0.82rem', lineHeight: 1.4
              }}
            >
              Security-policy configuration is unavailable: no admin API exists to
              load or edit password / session policy. This settings area does not invent
              a Policies panel, defaults, or disabled Edit controls.
            </div>
          </div>

          <div className="settings-section">
            <div
              role="note"
              style={{
                margin: '0 0 16px 0', padding: '10px 12px',
                background: 'rgba(234, 179, 8, 0.12)', border: '1px solid rgba(234, 179, 8, 0.35)',
                borderRadius: '6px', color: '#fbbf24', fontSize: '0.82rem', lineHeight: 1.4
              }}
            >
              Security-header status is unavailable: this UI does not probe response
              headers. Green “enabled” checks previously shown here were invented
              chrome, not a live probe — no Headers panel is shown.
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