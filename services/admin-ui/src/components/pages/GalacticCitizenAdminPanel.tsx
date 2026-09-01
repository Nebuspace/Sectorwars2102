import React, { useCallback, useState } from 'react';
import { api } from '../../utils/auth';
import { formatAdminApiError } from '../../utils/adminApiError';
import { useToast } from '../../contexts/ToastContext';

const SUBSCRIPTIONS_MODIFY_SCOPE_HINT =
  'admin.subscriptions.modify (SUBSCRIPTIONS_MODIFY) scope required for manual GC grant/revoke';

interface GcMutationResponse {
  player_id: string;
  is_galactic_citizen: boolean;
  subscription_tier: string | null;
  changed: boolean;
  idempotent: boolean;
  message: string;
}

function gcPanelError(err: unknown, fallback: string): string {
  return formatAdminApiError(err, {
    fallback,
    scopeHint: SUBSCRIPTIONS_MODIFY_SCOPE_HINT,
    notFoundMessage: 'Player not found (404). Confirm the player UUID exists.',
  });
}

/**
 * Manual Galactic Citizen grant/revoke (LEG-3617 / ADR-0115).
 * POST /api/v1/admin/players/{player_id}/galactic-citizen/grant|revoke
 */
const GalacticCitizenAdminPanel: React.FC = () => {
  const toast = useToast();
  const [playerId, setPlayerId] = useState('');
  const [reason, setReason] = useState('');
  const [submitting, setSubmitting] = useState<'grant' | 'revoke' | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<GcMutationResponse | null>(null);

  const runMutation = useCallback(
    async (action: 'grant' | 'revoke') => {
      const id = playerId.trim();
      const reasonText = reason.trim();
      setFormError(null);
      setLastResult(null);

      if (!id) {
        setFormError('Player UUID is required.');
        return;
      }
      if (!reasonText) {
        setFormError('Reason is required (non-empty).');
        return;
      }

      setSubmitting(action);
      try {
        const { data } = await api.post<GcMutationResponse>(
          `/api/v1/admin/players/${encodeURIComponent(id)}/galactic-citizen/${action}`,
          { reason: reasonText },
        );
        setLastResult(data);
        toast.success(data.message);
      } catch (err) {
        const message = gcPanelError(
          err,
          action === 'grant'
            ? 'Failed to grant Galactic Citizenship'
            : 'Failed to revoke Galactic Citizenship',
        );
        setFormError(message);
        toast.error(message);
      } finally {
        setSubmitting(null);
      }
    },
    [playerId, reason, toast],
  );

  return (
    <section
      className="galactic-citizen-admin-panel"
      aria-labelledby="gc-admin-heading"
      style={{
        marginTop: '1.5rem',
        padding: '1rem 1.25rem',
        border: '1px solid #374151',
        borderRadius: '8px',
        background: 'rgba(17, 24, 39, 0.65)',
      }}
    >
      <h3 id="gc-admin-heading" style={{ marginTop: 0 }}>
        Galactic Citizen — manual grant / revoke
      </h3>
      <p style={{ fontSize: '0.9rem', color: '#9ca3af', marginTop: 0 }}>
        Operator comp/clawback for paid-tier GC status. Requires{' '}
        <code>admin.subscriptions.modify</code>. Idempotent responses surface when the
        player is already in the target state.
      </p>

      <div style={{ marginBottom: '0.75rem' }}>
        <label htmlFor="gc-player-id">Player UUID</label>
        <input
          id="gc-player-id"
          type="text"
          value={playerId}
          onChange={(e) => setPlayerId(e.target.value)}
          placeholder="player UUID"
          autoComplete="off"
          disabled={submitting !== null}
          style={{ display: 'block', width: '100%', marginTop: '0.25rem' }}
        />
      </div>

      <div style={{ marginBottom: '0.75rem' }}>
        <label htmlFor="gc-reason">Reason (required)</label>
        <textarea
          id="gc-reason"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={2}
          maxLength={500}
          disabled={submitting !== null}
          style={{ display: 'block', width: '100%', marginTop: '0.25rem' }}
        />
      </div>

      {formError && (
        <div role="alert" style={{ color: '#f87171', marginBottom: '0.75rem' }}>
          {formError}
        </div>
      )}

      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        <button
          type="button"
          disabled={submitting !== null}
          onClick={() => runMutation('grant')}
        >
          {submitting === 'grant' ? 'Granting…' : 'Grant GC'}
        </button>
        <button
          type="button"
          disabled={submitting !== null}
          onClick={() => runMutation('revoke')}
        >
          {submitting === 'revoke' ? 'Revoking…' : 'Revoke GC'}
        </button>
      </div>

      {lastResult && (
        <div role="status" style={{ marginTop: '1rem', fontSize: '0.9rem' }}>
          <strong>Result:</strong> {lastResult.message}
          <br />
          <code>is_galactic_citizen={String(lastResult.is_galactic_citizen)}</code>
          {lastResult.subscription_tier != null && (
            <>
              {' '}
              · tier <code>{lastResult.subscription_tier}</code>
            </>
          )}
          {lastResult.idempotent && (
            <span style={{ color: '#fbbf24' }}> (idempotent — no state change)</span>
          )}
        </div>
      )}
    </section>
  );
};

export default GalacticCitizenAdminPanel;
