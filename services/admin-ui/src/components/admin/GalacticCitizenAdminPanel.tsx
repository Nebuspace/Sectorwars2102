import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../../utils/auth';
import { formatAdminApiError } from '../../utils/adminApiError';
import { useToast, useConfirm } from '../../contexts/ToastContext';

const MODIFY_SCOPE_HINT =
  'manual Galactic Citizen grant/revoke requires admin.subscriptions.modify';
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

function gcPanelError(err: unknown, fallback: string, scopeHint: string): string {
  return formatAdminApiError(err, { fallback, scopeHint });
}

export interface GalacticCitizenAdminPanelProps {
  playerId: string;
  playerName?: string;
}

const GalacticCitizenAdminPanel: React.FC<GalacticCitizenAdminPanelProps> = ({
  playerId,
  playerName,
}) => {
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
      const variant = data.idempotent ? 'info' : 'success';
      if (variant === 'info') {
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

export default GalacticCitizenAdminPanel;
