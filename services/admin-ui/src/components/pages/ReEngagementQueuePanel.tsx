import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../../utils/auth';
import { useToast } from '../../contexts/ToastContext';
import { formatAdminApiError } from '../../utils/adminApiError';

interface ReEngagementRow {
  id: string;
  player_id: string;
  player_nickname: string | null;
  signals: string[];
  signal_detail: Record<string, unknown>;
  status: string;
  computed_at: string | null;
  computed_day: number | null;
  resolved_at: string | null;
}

interface ReEngagementSummary {
  open: number;
  contacted: number;
  resolved: number;
  total: number;
  open_share: number | null;
}

interface ReEngagementQueuePanelProps {
  /** When true, omit the outer section chrome (embedded under Player Analytics). */
  embedded?: boolean;
  onSummaryChange?: (summary: ReEngagementSummary | null) => void;
}

const STATUS_FILTERS = ['OPEN', 'CONTACTED', 'RESOLVED', 'ALL'] as const;

/**
 * LEG-28 — operator surface for player_re_engagement_queue.
 * Canon: OPERATIONS/retention.md § At-risk signals / Re-engagement campaigns.
 */
const ReEngagementQueuePanel: React.FC<ReEngagementQueuePanelProps> = ({
  embedded = false,
  onSummaryChange,
}) => {
  const toast = useToast();
  const [statusFilter, setStatusFilter] = useState<string>('OPEN');
  const [items, setItems] = useState<ReEngagementRow[]>([]);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<ReEngagementSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [mutating, setMutating] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [listRes, summaryRes] = await Promise.all([
        api.get<{ items: ReEngagementRow[]; total: number }>(
          `/api/v1/admin/re-engagement?status=${encodeURIComponent(statusFilter)}&limit=100`
        ),
        api.get<ReEngagementSummary>('/api/v1/admin/re-engagement/summary'),
      ]);
      setItems(listRes.data.items ?? []);
      setTotal(listRes.data.total ?? 0);
      setSummary(summaryRes.data);
      onSummaryChange?.(summaryRes.data);
    } catch (err: unknown) {
      setItems([]);
      setTotal(0);
      setSummary(null);
      onSummaryChange?.(null);
      setError(
        formatAdminApiError(err, {
          fallback: 'Failed to load re-engagement queue',
          scopeHint: 'PLAYERS_VIEW required to list re-engagement queue',
        })
      );
    } finally {
      setLoading(false);
    }
  }, [statusFilter, onSummaryChange]);

  useEffect(() => {
    void load();
  }, [load]);

  const updateStatus = async (id: string, next: 'CONTACTED' | 'RESOLVED') => {
    setMutating(id);
    try {
      await api.patch(`/api/v1/admin/re-engagement/${id}`, { status: next });
      toast.success(`Marked ${next}`);
      await load();
    } catch (err: unknown) {
      toast.error(
        formatAdminApiError(err, {
          fallback: 'Failed to update status',
          scopeHint: 'PLAYERS_ADJUST_REP required to update re-engagement status',
        })
      );
    } finally {
      setMutating(null);
    }
  };

  const body = (
    <>
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <label className="text-sm">
          Status{' '}
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            aria-label="Re-engagement status filter"
          >
            {STATUS_FILTERS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        {summary && (
          <span className="text-sm text-muted">
            Open {summary.open} · Contacted {summary.contacted} · Resolved {summary.resolved}
          </span>
        )}
        <button type="button" className="btn btn-sm btn-ghost" onClick={() => void load()}>
          Refresh
        </button>
      </div>

      {error && (
        <div className="alert alert-error mb-3" role="alert">
          {error}
        </div>
      )}
      {loading ? (
        <p className="text-muted">Loading queue…</p>
      ) : items.length === 0 ? (
        <p className="text-muted">No rows for this filter ({total} total matching).</p>
      ) : (
        <div className="levers-table-wrap">
          <table className="levers-table">
            <thead>
              <tr>
                <th>Player</th>
                <th>Signals</th>
                <th>Status</th>
                <th>Computed</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((row) => (
                <tr key={row.id}>
                  <td>
                    <div className="font-medium">{row.player_nickname || '—'}</div>
                    <div className="font-mono text-xs text-muted">{row.player_id}</div>
                  </td>
                  <td>
                    <code className="text-xs">{(row.signals || []).join(', ') || '—'}</code>
                  </td>
                  <td>{row.status}</td>
                  <td className="text-sm">
                    {row.computed_at ? new Date(row.computed_at).toLocaleString() : '—'}
                  </td>
                  <td>
                    <div className="flex gap-2 flex-wrap">
                      {row.status === 'OPEN' && (
                        <button
                          type="button"
                          className="btn btn-sm btn-primary"
                          disabled={mutating === row.id}
                          onClick={() => void updateStatus(row.id, 'CONTACTED')}
                        >
                          Mark contacted
                        </button>
                      )}
                      {row.status !== 'RESOLVED' && (
                        <button
                          type="button"
                          className="btn btn-sm"
                          disabled={mutating === row.id}
                          onClick={() => void updateStatus(row.id, 'RESOLVED')}
                        >
                          Resolve
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );

  if (embedded) {
    return <div className="re-engagement-queue-panel">{body}</div>;
  }

  return (
    <section className="section re-engagement-queue-panel">
      <div className="section-header">
        <div>
          <h3 className="section-title">Re-engagement queue</h3>
          <p className="section-subtitle">
            At-risk players from the nightly retention sweep (OPERATIONS/retention.md)
          </p>
        </div>
      </div>
      {body}
    </section>
  );
};

export type { ReEngagementSummary };
export default ReEngagementQueuePanel;
