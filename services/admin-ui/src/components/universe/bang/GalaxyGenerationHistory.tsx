import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useAdmin } from '../../../contexts/AdminContext';
import type {
  BangConfig,
  BangJobResponse,
  BangJobWarning,
} from './types';
import './galaxy-generation-history.css';

interface GalaxyGenerationHistoryProps {
  /**
   * Called with a previous job's config when the operator clicks
   * "Regenerate". Parent prefills the form with this config.
   */
  onRegenerate?: (config: BangConfig) => void;
  /** Called when the operator clicks "View log" — parent shows the log panel. */
  onSelectJob?: (jobId: string) => void;
}

const DEFAULT_PAGE_SIZE = 20;

/** Group warnings by category for the count badge cluster. */
function warningCounts(
  warnings: BangJobWarning[],
): Array<{ category: string; count: number }> {
  const counts = new Map<string, number>();
  for (const w of warnings) {
    counts.set(w.category, (counts.get(w.category) ?? 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([category, count]) => ({ category, count }))
    .sort((a, b) => b.count - a.count);
}

function formatDuration(ms: number | null | undefined): string {
  if (!ms || ms <= 0) return '—';
  if (ms < 1000) return `${ms}ms`;
  const seconds = Math.round(ms / 100) / 10;
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remSeconds = Math.round(seconds - minutes * 60);
  return `${minutes}m ${remSeconds}s`;
}

const GalaxyGenerationHistory: React.FC<GalaxyGenerationHistoryProps> = ({
  onRegenerate,
  onSelectJob,
}) => {
  const { t } = useTranslation('admin');
  const { bangHistory, bangHistoryTotal, loadBangHistory, isLoading } = useAdmin();
  const [page, setPage] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setError(null);
        await loadBangHistory(page, DEFAULT_PAGE_SIZE);
      } catch (err) {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : String(err);
          setError(message);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [page, loadBangHistory]);

  const totalPages = bangHistoryTotal
    ? Math.max(1, Math.ceil(bangHistoryTotal / DEFAULT_PAGE_SIZE))
    : 1;

  return (
    <div className="galaxy-generation-history">
      <div className="history-header">
        <h3>{t('bang.history.title')}</h3>
      </div>

      {error && (
        <p className="history-error">
          {t('bang.history.loadFailed', { error })}
        </p>
      )}

      {bangHistory.length === 0 && !isLoading ? (
        <p className="history-empty">{t('bang.history.empty')}</p>
      ) : (
        <div className="history-table-wrap">
          <table className="history-table">
            <thead>
              <tr>
                <th>{t('bang.history.columns.date')}</th>
                <th>{t('bang.history.columns.admin')}</th>
                <th>{t('bang.history.columns.seed')}</th>
                <th>{t('bang.history.columns.bangVersion')}</th>
                <th>{t('bang.history.columns.regions')}</th>
                <th>{t('bang.history.columns.warnings')}</th>
                <th>{t('bang.history.columns.duration')}</th>
                <th>{t('bang.history.columns.status')}</th>
                <th>{t('bang.history.columns.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {bangHistory.map((job: BangJobResponse) => {
                const params = job.params_json as Partial<BangConfig> & Record<string, unknown>;
                const seed = params.seed ?? '—';
                const regionType = params.region_type ?? '—';
                const bangVersion = (params as { bang_version?: string }).bang_version ?? '—';
                const date = job.started_at ? new Date(job.started_at).toLocaleString() : '—';
                const counts = warningCounts(job.warnings_json ?? []);
                return (
                  <tr key={job.id}>
                    <td>{date}</td>
                    <td className="history-admin-cell">{job.admin_user_id.slice(0, 8)}</td>
                    <td className="history-seed-cell">{String(seed)}</td>
                    <td>{bangVersion}</td>
                    <td>{regionType}</td>
                    <td className="history-warnings-cell">
                      {counts.length === 0 ? (
                        <span className="history-warning-zero">0</span>
                      ) : (
                        counts.map((c) => (
                          <span
                            key={c.category}
                            className="history-warning-badge"
                            title={c.category}
                          >
                            {c.category.slice(0, 4)}:{c.count}
                          </span>
                        ))
                      )}
                    </td>
                    <td>{formatDuration(job.duration_ms)}</td>
                    <td>
                      <span
                        className={`history-status history-status-${job.status.toLowerCase()}`}
                      >
                        {t(`bang.history.status.${job.status}`)}
                      </span>
                    </td>
                    <td className="history-actions-cell">
                      {onRegenerate && (
                        <button
                          type="button"
                          className="history-action-btn"
                          onClick={() =>
                            onRegenerate(job.params_json as BangConfig)
                          }
                        >
                          {t('bang.history.actions.regenerate')}
                        </button>
                      )}
                      {onSelectJob && (
                        <button
                          type="button"
                          className="history-action-btn"
                          onClick={() => onSelectJob(job.id)}
                        >
                          {t('bang.history.actions.viewLog')}
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="history-pagination">
        <button
          type="button"
          className="history-page-btn"
          onClick={() => setPage((p) => Math.max(0, p - 1))}
          disabled={page === 0 || isLoading}
        >
          {t('bang.history.pagination.prev')}
        </button>
        <span className="history-page-label">
          {isLoading
            ? t('bang.history.pagination.loading')
            : t('bang.history.pagination.page', { page: page + 1 })}
        </span>
        <button
          type="button"
          className="history-page-btn"
          onClick={() => setPage((p) => p + 1)}
          disabled={page + 1 >= totalPages || isLoading}
        >
          {t('bang.history.pagination.next')}
        </button>
      </div>
    </div>
  );
};

export default GalaxyGenerationHistory;
