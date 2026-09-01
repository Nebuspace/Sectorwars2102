import React, { useCallback, useState } from 'react';
import { api } from '../../utils/auth';
import { formatAdminApiError } from '../../utils/adminApiError';
import { useToast, useConfirm } from '../../contexts/ToastContext';

type PlayerSecurityActionKind = 'block' | 'unblock' | 'reset_violations' | 'reset_trust';

const ARIA_AUDIT_SCOPE_HINT = 'admin.aria.audit scope required';

interface PlayerRiskAssessment {
  player_id?: string;
  risk_level: string;
  risk_score?: number;
  risk_factors?: string[];
  trust_score?: number;
  violation_count?: number;
  is_blocked?: boolean;
  daily_cost_usd?: number;
  last_violation?: string | null;
  reason?: string;
}

interface PlayerSecurityStatus {
  is_blocked: boolean;
  trust_score: number;
  violation_count: number;
  last_violation?: string | null;
  request_count_1min: number;
  request_count_1day: number;
  block_expires?: string | null;
}

function ariaSecurityError(err: unknown, fallback: string): string {
  return formatAdminApiError(err, {
    fallback,
    scopeHint: ARIA_AUDIT_SCOPE_HINT,
  });
}

function riskLevelClass(level: string): string {
  switch (level) {
    case 'critical':
      return 'risk-critical';
    case 'high':
      return 'risk-high';
    case 'medium':
      return 'risk-medium';
    case 'low':
      return 'risk-low';
    default:
      return 'risk-unknown';
  }
}

function actionConfirmCopy(action: PlayerSecurityActionKind, playerId: string): {
  title: string;
  message: string;
  confirmLabel: string;
} {
  switch (action) {
    case 'block':
      return {
        title: 'Block player ARIA access',
        message: `Block player ${playerId} from ARIA requests? This posts to the admin security action route (${ARIA_AUDIT_SCOPE_HINT}).`,
        confirmLabel: 'Block player',
      };
    case 'unblock':
      return {
        title: 'Unblock player ARIA access',
        message: `Immediately unblock player ${playerId}? Outcome is logged for audit review.`,
        confirmLabel: 'Unblock player',
      };
    case 'reset_trust':
      return {
        title: 'Reset player trust score',
        message: `Reset trust score to 1.0 for player ${playerId}? Outcome is logged for audit review.`,
        confirmLabel: 'Reset trust',
      };
    case 'reset_violations':
      return {
        title: 'Reset violation count',
        message: `Reset violation count to 0 for player ${playerId}? Outcome is logged for audit review.`,
        confirmLabel: 'Reset violations',
      };
  }
}

export const AriaPlayerSecurityOpsPanel: React.FC = () => {
  const toast = useToast();
  const confirm = useConfirm();

  const [playerId, setPlayerId] = useState('');
  const [risk, setRisk] = useState<PlayerRiskAssessment | null>(null);
  const [status, setStatus] = useState<PlayerSecurityStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionKind, setActionKind] = useState<PlayerSecurityActionKind>('block');
  const [durationHours, setDurationHours] = useState('');
  const [actionReason, setActionReason] = useState('');
  const [acting, setActing] = useState(false);

  const loadAssessment = useCallback(async (idOverride?: string) => {
    const id = (idOverride ?? playerId).trim();
    if (!id) {
      toast.error('Player id is required');
      return;
    }

    setLoading(true);
    setLoadError(null);

    const [riskResult, statusResult] = await Promise.allSettled([
      api.get<PlayerRiskAssessment>(`/api/v1/admin/security/player/${encodeURIComponent(id)}/risk`),
      api.get<PlayerSecurityStatus>(`/api/v1/admin/security/player/${encodeURIComponent(id)}/status`),
    ]);

    const failures: string[] = [];

    if (riskResult.status === 'fulfilled') {
      setRisk(riskResult.value.data);
    } else {
      setRisk(null);
      failures.push(ariaSecurityError(riskResult.reason, 'Failed to load player risk assessment'));
    }

    if (statusResult.status === 'fulfilled') {
      setStatus(statusResult.value.data);
    } else {
      setStatus(null);
      failures.push(ariaSecurityError(statusResult.reason, 'Failed to load player security status'));
    }

    setLoadError(failures.length > 0 ? failures.join(' | ') : null);
    setLoading(false);
  }, [playerId, toast]);

  const handleAction = async () => {
    const id = playerId.trim();
    if (!id) {
      toast.error('Player id is required');
      return;
    }
    if (actionKind === 'block' && durationHours.trim() === '') {
      toast.error('Block duration in hours is required');
      return;
    }

    const copy = actionConfirmCopy(actionKind, id);
    if (!(await confirm(copy))) {
      return;
    }

    const body: {
      action: PlayerSecurityActionKind;
      duration_hours?: number;
      reason?: string;
    } = { action: actionKind };
    if (actionKind === 'block') {
      body.duration_hours = Number(durationHours);
    }
    const reason = actionReason.trim();
    if (reason) {
      body.reason = reason;
    }

    setActing(true);
    try {
      const response = await api.post(
        `/api/v1/admin/security/player/${encodeURIComponent(id)}/action`,
        body,
      );
      const data = response.data as { message?: string; new_status?: PlayerSecurityStatus } | undefined;
      const outcome =
        data?.message ??
        `Player security action ${actionKind} completed — audit log updated for ${id}`;
      toast.success(outcome);
      await loadAssessment(id);
    } catch (err: unknown) {
      toast.error(ariaSecurityError(err, 'Failed to take player security action'));
    } finally {
      setActing(false);
    }
  };

  return (
    <section
      className="aria-player-security-ops"
      role="region"
      aria-label="ARIA per-player security operations"
    >
      <h4>ARIA per-player security ops</h4>
      <p className="security-ops-note">
        Review one player&apos;s ARIA risk and status, then block, unblock, or reset trust.
        Requires <code>admin.aria.audit</code> — tip routes only.
      </p>

      <div className="security-ops-row">
        <label>
          Player id
          <input
            type="text"
            value={playerId}
            onChange={(e) => setPlayerId(e.target.value)}
            aria-label="Player id for ARIA security assessment"
            placeholder="player uuid"
          />
        </label>
        <button
          type="button"
          className="btn btn-outline"
          onClick={() => void loadAssessment()}
          disabled={loading}
          aria-label="Load ARIA security assessment"
        >
          {loading ? 'Loading…' : 'Load assessment'}
        </button>
      </div>

      {loadError && (
        <div role="alert" className="aria-security-load-error">
          {loadError}
        </div>
      )}

      {(risk || status) && (
        <div className="aria-security-assessment-grid">
          {risk && (
            <div className="aria-security-card" aria-label="Player risk assessment">
              <h5>Risk assessment</h5>
              <div className={`aria-risk-badge ${riskLevelClass(risk.risk_level)}`}>
                {risk.risk_level}
                {typeof risk.risk_score === 'number' ? ` (${risk.risk_score})` : ''}
              </div>
              {risk.reason && <p>{risk.reason}</p>}
              <dl className="aria-security-metrics">
                <div>
                  <dt>Trust score</dt>
                  <dd>{risk.trust_score ?? '—'}</dd>
                </div>
                <div>
                  <dt>Violations</dt>
                  <dd>{risk.violation_count ?? '—'}</dd>
                </div>
                <div>
                  <dt>Blocked</dt>
                  <dd>{risk.is_blocked ? 'Yes' : 'No'}</dd>
                </div>
                <div>
                  <dt>Daily cost (USD)</dt>
                  <dd>
                    {typeof risk.daily_cost_usd === 'number'
                      ? risk.daily_cost_usd.toFixed(4)
                      : '—'}
                  </dd>
                </div>
              </dl>
              {risk.risk_factors && risk.risk_factors.length > 0 && (
                <ul className="aria-risk-factors">
                  {risk.risk_factors.map((factor) => (
                    <li key={factor}>{factor}</li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {status && (
            <div className="aria-security-card" aria-label="Player security status">
              <h5>Security status</h5>
              <dl className="aria-security-metrics">
                <div>
                  <dt>Blocked</dt>
                  <dd>{status.is_blocked ? 'Yes' : 'No'}</dd>
                </div>
                <div>
                  <dt>Trust score</dt>
                  <dd>{status.trust_score}</dd>
                </div>
                <div>
                  <dt>Violations</dt>
                  <dd>{status.violation_count}</dd>
                </div>
                <div>
                  <dt>Requests (1 min)</dt>
                  <dd>{status.request_count_1min}</dd>
                </div>
                <div>
                  <dt>Requests (1 day)</dt>
                  <dd>{status.request_count_1day}</dd>
                </div>
                <div>
                  <dt>Block expires</dt>
                  <dd>
                    {status.block_expires
                      ? new Date(status.block_expires).toLocaleString()
                      : '—'}
                  </dd>
                </div>
              </dl>
            </div>
          )}
        </div>
      )}

      <div className="security-ops-row" aria-label="ARIA player security actions">
        <label>
          Action
          <select
            value={actionKind}
            onChange={(e) => setActionKind(e.target.value as PlayerSecurityActionKind)}
            aria-label="ARIA player security action type"
          >
            <option value="block">block</option>
            <option value="unblock">unblock</option>
            <option value="reset_violations">reset_violations</option>
            <option value="reset_trust">reset_trust</option>
          </select>
        </label>
        {actionKind === 'block' && (
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
            aria-label="Reason for ARIA player security action"
          />
        </label>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => void handleAction()}
          disabled={acting}
          aria-label="Take ARIA player security action"
        >
          {acting ? 'Working…' : 'Take action'}
        </button>
      </div>
    </section>
  );
};
