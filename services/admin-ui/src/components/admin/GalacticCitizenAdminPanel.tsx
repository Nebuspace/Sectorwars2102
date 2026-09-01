import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../../utils/auth';
import { formatAdminApiError } from '../../utils/adminApiError';
import { useToast, useConfirm } from '../../contexts/ToastContext';

const MODIFY_SCOPE_HINT =
  'admin.subscriptions.modify (SUBSCRIPTIONS_MODIFY) scope required for manual GC grant/revoke';
const VIEW_SCOPE_HINT =
  'loading GC membership status requires admin.subscriptions.view';

interface SubscriptionsOverview {
  galactic_citizens?: Array<{ player_id: string; username?: string }>;
}

export interface GcMutationResponse {
  player_id: string;
  is_galactic_citizen: boolean;
  subscription_tier: string | null;
  changed: boolean;
  idempotent: boolean;
  message: string;
}

function gcPanelError(
  err: unknown,
  fallback: string,
  scopeHint: string,
  notFoundMessage?: string,
): string {
  return formatAdminApiError(err, {
    fallback,
    scopeHint,
    notFoundMessage,
  });
}

export interface GalacticCitizenAdminPanelProps {
  playerId?: string;
  playerName?: string;
}

/**
 * Manual Galactic Citizen grant/revoke (LEG-273 / LEG-3617 / LEG-3632).
 * Embedded mode: pass playerId from Player Analytics detail.
 * Standalone mode: operator enters player UUID (Economy Dashboard).
 */
const GalacticCitizenAdminPanel: React.FC<GalacticCitizenAdminPanelProps> = ({
  playerId: playerIdProp,
  playerName,
}) => {
  if (playerIdProp) {
    return (
      <EmbeddedGalacticCitizenAdminPanel playerId={playerIdProp} playerName={playerName} />
    );
  }
  return <StandaloneGalacticCitizenAdminPanel />;
};

const EmbeddedGalacticCitizenAdminPanel: React.FC<{
  playerId: string;
  playerName?: string;
}> = ({ playerId, playerName }) => {
  const toast = useToast();
  const confirm = useConfirm();

  const [isGc, setIsGc] = useState<boolean | null>(null);
  const [subscriptionTier, setSubscriptionTier] = useState<string | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [mutating, setMutating] = useState<'grant' | 'revoke' | null>(null);

  const loadStatus = useCallback(async () => {
    const id = playerId.trim();
    if (!id) return;
    setStatusLoading(true);
    setStatusError(null);
    try {
      const { data } = await api.get<SubscriptionsOverview>('/api/v1/admin/subscriptions');
      const match = (data.galactic_citizens ?? []).some((row) => row.player_id === id);
      setIsGc(match);
    } catch (err: unknown) {
      setIsGc(null);
      setStatusError(gcPanelError(err, 'Failed to load Galactic Citizen status', VIEW_SCOPE_HINT));
    } finally {
      setStatusLoading(false);
    }
  }, [playerId]);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  const runMutation = async (action: 'grant' | 'revoke') => {
    const id = playerId.trim();
    const trimmedReason = reason.trim();
    if (!id || !trimmedReason) return;

    const isGrant = action === 'grant';
    const ok = await confirm({
      title: isGrant ? 'Grant Galactic Citizenship' : 'Revoke Galactic Citizenship',
      message: isGrant
        ? `Grant Galactic Citizen status to ${playerName ?? id}? Reason will be recorded in AdminActionLog.`
        : `Revoke Galactic Citizen status from ${playerName ?? id}? Reason will be recorded in AdminActionLog.`,
      confirmLabel: isGrant ? 'Grant GC' : 'Revoke GC',
      danger: !isGrant,
    });
    if (!ok) return;

    setMutating(action);
    try {
      const { data } = await api.post<GcMutationResponse>(
        `/api/v1/admin/players/${encodeURIComponent(id)}/galactic-citizen/${action}`,
        { reason: trimmedReason },
      );
      setIsGc(data.is_galactic_citizen);
      setSubscriptionTier(data.subscription_tier);
      if (data.idempotent) {
        toast.info(data.message);
      } else {
        toast.success(data.message);
      }
      setReason('');
    } catch (err: unknown) {
      toast.error(
        gcPanelError(
          err,
          isGrant ? 'Galactic Citizen grant failed' : 'Galactic Citizen revoke failed',
          MODIFY_SCOPE_HINT,
        ),
      );
    } finally {
      setMutating(null);
    }
  };

  const reasonValid = reason.trim().length > 0;
  const displayName = playerName || playerId || '—';
  const statusLabel =
    isGc === null ? 'Unknown' : isGc ? 'Active Galactic Citizen' : 'Not a Galactic Citizen';

  return (
    <section
      className="space-y-3 mt-6 pt-6 border-t border-base-300"
      data-testid="galactic-citizen-admin-panel"
    >
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h4 className="text-lg font-semibold">Galactic Citizen (manual)</h4>
        <button
          type="button"
          className="btn btn-sm btn-outline"
          disabled={statusLoading}
          onClick={() => void loadStatus()}
        >
          {statusLoading ? 'Loading…' : 'Refresh status'}
        </button>
      </div>

      <p className="text-sm text-muted">
        {displayName} · {statusLabel}
        {subscriptionTier ? ` · tier ${subscriptionTier}` : ''}
      </p>
      <p className="text-xs text-muted">
        Comp or claw back GC outside PayPal — gated by <code>admin.subscriptions.modify</code>.
        Every mutation requires a reason and writes to AdminActionLog.
      </p>

      {statusError && (
        <div className="alert alert-warning" role="alert">
          {statusError}
        </div>
      )}

      <label className="form-control w-full max-w-xl">
        <span className="label-text font-medium">Reason (required)</span>
        <textarea
          className="textarea textarea-bordered w-full"
          rows={2}
          maxLength={500}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Support ticket ref, comp authorization, clawback justification…"
          data-testid="gc-mutation-reason"
        />
      </label>

      <div className="flex gap-2 flex-wrap">
        <button
          type="button"
          className="btn btn-sm btn-primary"
          disabled={!reasonValid || mutating !== null}
          onClick={() => void runMutation('grant')}
        >
          {mutating === 'grant' ? 'Granting…' : 'Grant GC'}
        </button>
        <button
          type="button"
          className="btn btn-sm btn-danger"
          disabled={!reasonValid || mutating !== null}
          onClick={() => void runMutation('revoke')}
        >
          {mutating === 'revoke' ? 'Revoking…' : 'Revoke GC'}
        </button>
      </div>
    </section>
  );
};

const StandaloneGalacticCitizenAdminPanel: React.FC = () => {
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
          MODIFY_SCOPE_HINT,
          'Player not found (404). Confirm the player UUID exists.',
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
