import React, { useCallback, useEffect, useState } from 'react';
import PageHeader from '../ui/PageHeader';
import { api } from '../../utils/auth';
import './admin-action-log.css';

interface AdminActionItem {
  id: string;
  admin_user_id?: string | null;
  scope_used?: string | null;
  action: string;
  target_type?: string | null;
  target_id?: string | null;
  payload_snapshot?: unknown;
  result?: string | null;
  failure_reason?: string | null;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
  at: string;
}

interface AdminActionPage {
  items: AdminActionItem[];
  total: number;
  page: number;
  limit: number;
  pages: number;
}

function scopeMissingMessage(err: any, fallback: string): string {
  const detail = err?.response?.data?.detail || err?.response?.data?.message;
  if (err?.response?.status === 403) {
    return typeof detail === 'string'
      ? detail
      : 'You lack admin.audit.view — cannot view the AdminActionLog.';
  }
  return typeof detail === 'string' ? detail : fallback;
}

export const AdminActionLogPage: React.FC = () => {
  const [data, setData] = useState<AdminActionPage | null>(null);
  const [page, setPage] = useState(1);
  const [actor, setActor] = useState('');
  const [action, setAction] = useState('');
  const [targetType, setTargetType] = useState('');
  const [targetId, setTargetId] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [applied, setApplied] = useState({
    actor: '',
    action: '',
    targetType: '',
    targetId: '',
    startDate: '',
    endDate: '',
  });
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    setForbidden(false);
    try {
      const params: Record<string, string | number> = {
        page,
        limit: 50,
      };
      if (applied.actor.trim()) params.admin_user_id = applied.actor.trim();
      if (applied.action.trim()) params.action = applied.action.trim();
      if (applied.targetType.trim()) params.target_type = applied.targetType.trim();
      if (applied.targetId.trim()) params.target_id = applied.targetId.trim();
      if (applied.startDate) params.start_date = new Date(applied.startDate).toISOString();
      if (applied.endDate) params.end_date = new Date(applied.endDate).toISOString();

      const res = await api.get('/api/v1/admin/audit/actions', { params });
      setData(res.data as AdminActionPage);
    } catch (err: any) {
      setData(null);
      if (err?.response?.status === 403) {
        setForbidden(true);
      }
      setError(scopeMissingMessage(err, 'Failed to load admin action log'));
    } finally {
      setIsLoading(false);
    }
  }, [page, applied]);

  useEffect(() => {
    load();
  }, [load]);

  const applyFilters = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    setApplied({ actor, action, targetType, targetId, startDate, endDate });
  };

  const clearFilters = () => {
    setActor('');
    setAction('');
    setTargetType('');
    setTargetId('');
    setStartDate('');
    setEndDate('');
    setPage(1);
    setApplied({
      actor: '',
      action: '',
      targetType: '',
      targetId: '',
      startDate: '',
      endDate: '',
    });
  };

  return (
    <div className="aal-page">
      <PageHeader
        title="Admin Action Log"
        subtitle="Append-only AdminActionLog — read-only accountability ledger (not the legacy HTTP audit trail)."
      />

      {forbidden && (
        <div className="aal-alert aal-alert-forbidden" role="alert">
          {error || 'You lack admin.audit.view — cannot view the AdminActionLog.'}
        </div>
      )}

      {!forbidden && error && (
        <div className="aal-alert aal-alert-error" role="alert">
          {error}
        </div>
      )}

      {!forbidden && (
        <form className="aal-filters" onSubmit={applyFilters} aria-label="Action log filters">
          <label>
            Actor (user id)
            <input
              value={actor}
              onChange={(e) => setActor(e.target.value)}
              placeholder="UUID"
              autoComplete="off"
            />
          </label>
          <label>
            Action
            <input
              value={action}
              onChange={(e) => setAction(e.target.value)}
              placeholder="e.g. scope_grant"
              autoComplete="off"
            />
          </label>
          <label>
            Target type
            <input
              value={targetType}
              onChange={(e) => setTargetType(e.target.value)}
              placeholder="user"
              autoComplete="off"
            />
          </label>
          <label>
            Target id
            <input
              value={targetId}
              onChange={(e) => setTargetId(e.target.value)}
              autoComplete="off"
            />
          </label>
          <label>
            From
            <input
              type="datetime-local"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
            />
          </label>
          <label>
            To
            <input
              type="datetime-local"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
            />
          </label>
          <div className="aal-filter-actions">
            <button type="submit" className="btn btn-primary">
              Apply
            </button>
            <button type="button" className="btn btn-secondary" onClick={clearFilters}>
              Clear
            </button>
          </div>
        </form>
      )}

      {isLoading && <p className="aal-muted">Loading actions…</p>}

      {!isLoading && !forbidden && data && (
        <>
          <p className="aal-muted" aria-live="polite">
            {data.total} row{data.total === 1 ? '' : 's'} · page {data.page} of{' '}
            {Math.max(data.pages, 1)}
          </p>

          <div className="aal-table-wrap">
            <table className="aal-table">
              <thead>
                <tr>
                  <th scope="col">When</th>
                  <th scope="col">Actor</th>
                  <th scope="col">Action</th>
                  <th scope="col">Target</th>
                  <th scope="col">Scope</th>
                  <th scope="col">Result</th>
                  <th scope="col">Payload</th>
                </tr>
              </thead>
              <tbody>
                {data.items.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="aal-muted">
                      No admin actions match these filters.
                    </td>
                  </tr>
                ) : (
                  data.items.map((row) => (
                    <React.Fragment key={row.id}>
                      <tr>
                        <td>{new Date(row.at).toLocaleString()}</td>
                        <td>
                          <code>{row.admin_user_id?.slice(0, 8) || '—'}</code>
                        </td>
                        <td>{row.action}</td>
                        <td>
                          {row.target_type || '—'}
                          {row.target_id ? (
                            <>
                              {' '}
                              <code>{row.target_id.slice(0, 12)}</code>
                            </>
                          ) : null}
                        </td>
                        <td>
                          <code>{row.scope_used || '—'}</code>
                        </td>
                        <td>{row.result || '—'}</td>
                        <td>
                          {row.payload_snapshot != null ? (
                            <button
                              type="button"
                              className="btn btn-secondary aal-payload-btn"
                              aria-expanded={expandedId === row.id}
                              onClick={() =>
                                setExpandedId(expandedId === row.id ? null : row.id)
                              }
                            >
                              {expandedId === row.id ? 'Hide' : 'View'}
                            </button>
                          ) : (
                            '—'
                          )}
                        </td>
                      </tr>
                      {expandedId === row.id && (
                        <tr className="aal-payload-row">
                          <td colSpan={7}>
                            <pre className="aal-payload-pre" tabIndex={0}>
                              {JSON.stringify(row.payload_snapshot, null, 2)}
                            </pre>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  ))
                )}
              </tbody>
            </table>
          </div>

          <div className="aal-pagination">
            <button
              type="button"
              className="btn btn-secondary"
              disabled={page <= 1 || isLoading}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              Previous
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              disabled={!data.pages || page >= data.pages || isLoading}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </button>
          </div>
        </>
      )}
    </div>
  );
};

export default AdminActionLogPage;
