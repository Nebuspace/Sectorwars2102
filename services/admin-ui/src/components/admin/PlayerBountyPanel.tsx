import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../../utils/auth';
import {
  axiosResponseStatus,
  detailFromResponse,
  formatAdminApiError,
} from '../../utils/adminApiError';
import { useToast, useConfirm } from '../../contexts/ToastContext';
import {
  creditConfirmLabel,
  useCreditInlineConfirm,
} from '../../hooks/useCreditInlineConfirm';

interface PlayerBountyEntry {
  id: string;
  placed_by?: string;
  placed_by_name?: string;
  amount?: number;
  type?: string;
  reason?: string;
  placed_at?: string;
}

interface BountyListResponse {
  success: boolean;
  target_id: string;
  target_name?: string;
  player_bounties: PlayerBountyEntry[];
  system_bounties: Array<{ amount?: number; reason?: string; type?: string }>;
  total_value: number;
  message?: string;
}

function bountyPanelError(
  err: unknown,
  fallback: string,
  scopeHint: 'PLAYERS_VIEW' | 'ECONOMY_INTERVENE'
): string {
  const scope =
    scopeHint === 'PLAYERS_VIEW'
      ? 'listing bounties requires the admin players view scope (PLAYERS_VIEW).'
      : 'bounty force-cancel / collapse requires ECONOMY_INTERVENE.';
  const status = axiosResponseStatus(err);
  if (status === 401 || status === 403 || status === 429) {
    return formatAdminApiError(err, { fallback, scopeHint: scope });
  }
  // Preserve GS string detail for 404 / status-less errors (helper 404 path ignores detail).
  return detailFromResponse(err) ?? formatAdminApiError(err, { fallback, scopeHint: scope });
}

export interface PlayerBountyPanelProps {
  targetId: string;
  targetName?: string;
}

const PlayerBountyPanel: React.FC<PlayerBountyPanelProps> = ({ targetId, targetName }) => {
  const toast = useToast();
  const confirm = useConfirm();
  const { isArmed, gateCreditAction } = useCreditInlineConfirm();

  const [list, setList] = useState<BountyListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [mutating, setMutating] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);

  const loadBounties = useCallback(async () => {
    const id = targetId.trim();
    if (!id) return;
    setLoading(true);
    setListError(null);
    try {
      const { data } = await api.get<BountyListResponse>(
        `/api/v1/admin/players/${encodeURIComponent(id)}/bounties`
      );
      setList(data);
    } catch (err: unknown) {
      setList(null);
      setListError(bountyPanelError(err, 'Failed to load bounties', 'PLAYERS_VIEW'));
    } finally {
      setLoading(false);
    }
  }, [targetId]);

  useEffect(() => {
    void loadBounties();
  }, [loadBounties]);

  const runForceCancel = async (bountyId: string) => {
    const id = targetId.trim();
    if (!id) return;
    setMutating(bountyId);
    try {
      await api.post(
        `/api/v1/admin/players/${encodeURIComponent(id)}/bounties/${encodeURIComponent(bountyId)}/force-cancel`
      );
      toast.success('Bounty force-cancelled');
      await loadBounties();
    } catch (err: unknown) {
      toast.error(bountyPanelError(err, 'Force-cancel failed', 'ECONOMY_INTERVENE'));
    } finally {
      setMutating(null);
    }
  };

  const onForceCancelClick = (bountyId: string, amount: number) => {
    gateCreditAction(`force-cancel-${bountyId}`, amount, () => {
      void runForceCancel(bountyId);
    });
  };

  const collapse = async () => {
    const id = targetId.trim();
    if (!id) return;
    const ok = await confirm({
      title: 'Collapse excess bounties',
      message:
        'Merge older entries over the soft cap (50) per placer. No credits move. Continue?',
      confirmLabel: 'Collapse',
    });
    if (!ok) return;
    setMutating('collapse');
    try {
      const { data } = await api.post<{
        collapsed?: number;
        entry_count?: number;
        message?: string;
      }>(`/api/v1/admin/players/${encodeURIComponent(id)}/bounties/collapse`);
      toast.success(
        `Collapsed ${data?.collapsed ?? 0} · ${data?.entry_count ?? '—'} entries remain`
      );
      await loadBounties();
    } catch (err: unknown) {
      toast.error(bountyPanelError(err, 'Collapse failed', 'ECONOMY_INTERVENE'));
    } finally {
      setMutating(null);
    }
  };

  const playerBounties = list?.player_bounties ?? [];
  const displayName = list?.target_name || targetName || '—';

  return (
    <section className="space-y-3 mt-6 pt-6 border-t border-base-300" data-testid="player-bounty-panel">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h4 className="text-lg font-semibold">Bounties</h4>
        <div className="flex gap-2">
          <button
            type="button"
            className="btn btn-sm btn-outline"
            disabled={loading}
            onClick={() => void loadBounties()}
          >
            {loading ? 'Loading…' : 'Refresh'}
          </button>
          <button
            type="button"
            className="btn btn-sm btn-outline"
            disabled={!targetId.trim() || mutating === 'collapse'}
            onClick={() => void collapse()}
          >
            Collapse excess
          </button>
        </div>
      </div>

      {listError && (
        <div className="alert alert-error" role="alert">
          {listError}
        </div>
      )}

      {list && (
        <p className="text-sm text-muted">
          {displayName} · total value{' '}
          {list.total_value?.toLocaleString?.() ?? list.total_value} · {playerBounties.length}{' '}
          player-placed · {list.system_bounties?.length ?? 0} system
        </p>
      )}

      {!loading && list && playerBounties.length === 0 && (
        <p className="text-sm text-muted">No player-placed bounties on this target.</p>
      )}

      {playerBounties.length > 0 && (
        <div className="levers-table-wrap overflow-x-auto">
          <table className="levers-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Placer</th>
                <th>Amount</th>
                <th>Type</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {playerBounties.map((b) => {
                const amount = b.amount ?? 0;
                const forceCancelKey = `force-cancel-${b.id}`;
                const forceCancelArmed = isArmed(forceCancelKey);
                return (
                  <tr key={b.id}>
                    <td className="font-mono text-xs">{b.id}</td>
                    <td>
                      {b.placed_by_name || '—'}
                      <div className="font-mono text-xs text-muted">{b.placed_by}</div>
                    </td>
                    <td>{b.amount?.toLocaleString?.() ?? b.amount ?? '—'}</td>
                    <td>{b.type || 'player'}</td>
                    <td>
                      <button
                        type="button"
                        className="btn btn-sm btn-danger"
                        disabled={mutating === b.id || b.type === 'system'}
                        onClick={() => onForceCancelClick(b.id, amount)}
                      >
                        {forceCancelArmed
                          ? creditConfirmLabel(amount, 'refund')
                          : 'Force-cancel'}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
};

export default PlayerBountyPanel;
