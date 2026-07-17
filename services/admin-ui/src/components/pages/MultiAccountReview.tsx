import React, { useCallback, useEffect, useState } from 'react';
import PageHeader from '../ui/PageHeader';
import { api } from '../../utils/auth';
import './multi-account-review.css';

// Matches _serialize_cluster() in admin_multi_account.py
interface MultiAccountCluster {
  id: string;
  signal_summary: Record<string, unknown>;
  severity: 'hard' | 'soft';
  all_paid_subscribers: boolean;
  admin_decision: 'pending' | 'confirmed' | 'overridden' | 'escalated';
  admin_decision_reason: string | null;
  admin_decision_at: string | null;
  admin_decision_by: string | null;
  created_at: string;
  updated_at: string;
  member_count: number;
  // Only present on the detail endpoint
  flags?: MultiAccountFlag[];
}

// Matches _serialize_flag() in admin_multi_account.py
interface MultiAccountFlag {
  id: string;
  player_id: string;
  signal: string;
  severity: 'hard' | 'soft';
  created_at: string | null;
}

const DECISION_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'confirmed', label: 'Confirm (enforce limits)' },
  { value: 'overridden', label: 'Override (dismiss)' },
  { value: 'escalated', label: 'Escalate (flag for further review)' },
];

const FILTER_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'pending', label: 'Pending' },
  { value: 'confirmed', label: 'Confirmed' },
  { value: 'overridden', label: 'Overridden' },
  { value: 'escalated', label: 'Escalated' },
];

export const MultiAccountReview: React.FC = () => {
  const [clusters, setClusters] = useState<MultiAccountCluster[]>([]);
  const [selected, setSelected] = useState<MultiAccountCluster | null>(null);
  const [filterDecision, setFilterDecision] = useState('pending');
  const [decision, setDecision] = useState('');
  const [reason, setReason] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [isDeciding, setIsDeciding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [decideError, setDecideError] = useState<string | null>(null);

  const loadClusters = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await api.get(
        `/api/v1/admin/multi-account/clusters?decision=${filterDecision}`
      );
      setClusters(res.data as MultiAccountCluster[]);
    } catch (err: any) {
      setClusters([]);
      setError(
        err?.response?.data?.detail ||
          err?.response?.data?.message ||
          'Failed to load clusters'
      );
    } finally {
      setIsLoading(false);
    }
  }, [filterDecision]);

  const loadClusterDetail = useCallback(async (id: string) => {
    try {
      const res = await api.get(`/api/v1/admin/multi-account/clusters/${id}`);
      setSelected(res.data as MultiAccountCluster);
    } catch (err: any) {
      setDecideError(
        err?.response?.data?.detail ||
          err?.response?.data?.message ||
          'Failed to load cluster detail'
      );
    }
  }, []);

  useEffect(() => {
    loadClusters();
    const interval = setInterval(loadClusters, 30000);
    return () => clearInterval(interval);
  }, [loadClusters]);

  const handleSelectCluster = (c: MultiAccountCluster) => {
    setDecision('');
    setReason('');
    setDecideError(null);
    loadClusterDetail(c.id);
  };

  const handleDecide = async () => {
    if (!selected || !decision) return;
    setIsDeciding(true);
    setDecideError(null);
    try {
      await api.post(
        `/api/v1/admin/multi-account/clusters/${selected.id}/decide`,
        { decision, reason: reason || undefined }
      );
      setSelected(null);
      setDecision('');
      setReason('');
      await loadClusters();
    } catch (err: any) {
      setDecideError(
        err?.response?.data?.detail ||
          err?.response?.data?.message ||
          'Failed to record decision'
      );
    } finally {
      setIsDeciding(false);
    }
  };

  if (isLoading) {
    return (
      <div className="mar-page loading">
        <PageHeader title="Multi-Account Review" />
        <div className="loading-spinner">Loading clusters...</div>
      </div>
    );
  }

  return (
    <div className="mar-page">
      <PageHeader
        title="Multi-Account Review"
        subtitle="Admin review queue for detected multi-account clusters (ADR-0056)"
      />

      {error && (
        <div className="alert error" style={{ marginBottom: '20px' }}>
          <span className="alert-icon">❌</span>
          <span className="alert-message">{error}</span>
        </div>
      )}

      {/* Filter bar */}
      <div className="mar-filter-bar">
        <span className="mar-filter-label">Show:</span>
        {FILTER_OPTIONS.map((f) => (
          <button
            key={f.value}
            type="button"
            className={`mar-filter-btn ${filterDecision === f.value ? 'active' : ''}`}
            onClick={() => {
              setFilterDecision(f.value);
              setSelected(null);
            }}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Honest-gap notice — detection service not yet shipped */}
      <p className="mar-honest-gap">
        The detection sweep (P7-admin-multiacct-service-sweep) has not shipped yet. This queue
        will be empty until the hourly sweep runs against live accounts. The review UI and
        decision-recording REST layer are fully wired and ready.
      </p>

      <div className="mar-content">
        {/* Queue panel */}
        <div className="mar-queue">
          <h4>
            {FILTER_OPTIONS.find((f) => f.value === filterDecision)?.label} Clusters (
            {clusters.length})
          </h4>
          {clusters.length === 0 ? (
            <p className="mar-empty">No clusters in this state.</p>
          ) : (
            clusters.map((c) => (
              <div
                key={c.id}
                className={`mar-item ${selected?.id === c.id ? 'selected' : ''}`}
                onClick={() => handleSelectCluster(c)}
              >
                <div className="mar-item-header">
                  <span className={`mar-severity mar-severity-${c.severity}`}>
                    {c.severity.toUpperCase()}
                  </span>
                  <span className="mar-item-date">
                    {new Date(c.created_at).toLocaleDateString()}
                  </span>
                </div>
                <p className="mar-item-summary">
                  {c.member_count} member{c.member_count !== 1 ? 's' : ''}
                  {c.all_paid_subscribers && (
                    <span className="mar-paid-badge"> · All Paid</span>
                  )}
                </p>
              </div>
            ))
          )}
        </div>

        {/* Evidence + ruling panel */}
        {selected && (
          <div className="mar-evidence">
            <h4>Evidence Panel</h4>

            <div className="mar-detail-section">
              <label>Cluster ID</label>
              <span className="mar-mono">{selected.id}</span>
            </div>
            <div className="mar-detail-section">
              <label>Severity</label>
              <span className={`mar-severity mar-severity-${selected.severity}`}>
                {selected.severity.toUpperCase()}
              </span>
            </div>
            <div className="mar-detail-section">
              <label>All Paid Subscribers</label>
              <span>{selected.all_paid_subscribers ? 'Yes (discount bypass)' : 'No'}</span>
            </div>
            <div className="mar-detail-section">
              <label>Current Decision</label>
              <span>{selected.admin_decision}</span>
            </div>
            {selected.admin_decision_reason && (
              <div className="mar-detail-section">
                <label>Decision Reason</label>
                <p>{selected.admin_decision_reason}</p>
              </div>
            )}
            <div className="mar-detail-section">
              <label>Detected</label>
              <span>{new Date(selected.created_at).toLocaleString()}</span>
            </div>

            {/* Signal summary */}
            <div className="mar-detail-section">
              <label>Signal Summary</label>
              <pre className="mar-signal-pre">
                {JSON.stringify(selected.signal_summary, null, 2)}
              </pre>
            </div>

            {/* Member flags */}
            {selected.flags && selected.flags.length > 0 && (
              <div className="mar-flags">
                <label>Member Flags ({selected.flags.length})</label>
                <table className="mar-flags-table">
                  <thead>
                    <tr>
                      <th>Player ID</th>
                      <th>Signal</th>
                      <th>Severity</th>
                      <th>Detected</th>
                    </tr>
                  </thead>
                  <tbody>
                    {selected.flags.map((f) => (
                      <tr key={f.id}>
                        <td className="mar-mono">{f.player_id}</td>
                        <td>{f.signal}</td>
                        <td>
                          <span className={`mar-severity mar-severity-${f.severity}`}>
                            {f.severity}
                          </span>
                        </td>
                        <td>
                          {f.created_at
                            ? new Date(f.created_at).toLocaleDateString()
                            : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* Ruling form — only show for pending clusters */}
            {selected.admin_decision === 'pending' && (
              <div className="mar-ruling-form">
                <h5>Record Ruling</h5>

                {decideError && (
                  <div className="mar-decide-error" role="alert">
                    {decideError}
                  </div>
                )}

                <div className="form-group">
                  <label>Decision</label>
                  <div className="mar-decision-buttons">
                    {DECISION_OPTIONS.map((o) => (
                      <button
                        key={o.value}
                        type="button"
                        className={`mar-decision-btn mar-decision-btn-${o.value} ${decision === o.value ? 'active' : ''}`}
                        onClick={() => setDecision(o.value)}
                        disabled={isDeciding}
                      >
                        {o.label}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="form-group">
                  <label>Reason (optional)</label>
                  <textarea
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    placeholder="Reasoning for this ruling..."
                    rows={3}
                    disabled={isDeciding}
                  />
                </div>

                <div className="mar-ruling-actions">
                  <button
                    className="btn btn-success"
                    onClick={handleDecide}
                    disabled={!decision || isDeciding}
                  >
                    {isDeciding ? 'Submitting...' : 'Submit Ruling'}
                  </button>
                  <button
                    className="btn btn-secondary"
                    onClick={() => {
                      setSelected(null);
                      setDecision('');
                      setReason('');
                      setDecideError(null);
                    }}
                    disabled={isDeciding}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default MultiAccountReview;
