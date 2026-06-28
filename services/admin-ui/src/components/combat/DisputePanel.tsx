import React, { useState } from 'react';
import { api } from '../../utils/auth';

// Matches the backend CombatDisputeResponse schema in admin_combat.py
interface CombatDispute {
  id: string;
  combat_id: string | null;
  type: string;
  severity: string;
  timestamp: string;
  description: string;
  participants: Record<string, unknown>;
  status: string;
  recommended_action: string;
}

interface DisputePanelProps {
  disputes: CombatDispute[];
  onResolve?: () => void;
}

export const DisputePanel: React.FC<DisputePanelProps> = ({
  disputes,
  onResolve
}) => {
  const [selectedDispute, setSelectedDispute] = useState<CombatDispute | null>(null);
  const [resolution, setResolution] = useState('');
  const [adminNotes, setAdminNotes] = useState('');
  const [isResolving, setIsResolving] = useState(false);
  const [resolveError, setResolveError] = useState<string | null>(null);

  const handleResolve = async (action: 'resolve' | 'reject') => {
    if (!selectedDispute?.combat_id) {
      setResolveError('Cannot resolve: no combat_id associated with this dispute.');
      return;
    }
    setIsResolving(true);
    setResolveError(null);
    try {
      const notes = action === 'reject'
        ? `Rejected: ${adminNotes}`
        : `Resolved: ${resolution}. Admin notes: ${adminNotes}`;

      // Route: POST /admin/combat/{combat_id}/resolve
      // Body: CombatResolutionRequest — outcome, notes, credits_adjustment
      await api.post(`/api/v1/admin/combat/${selectedDispute.combat_id}/resolve`, {
        notes
      });

      // Clear form and refresh
      setSelectedDispute(null);
      setResolution('');
      setAdminNotes('');
      onResolve?.();
    } catch (error: any) {
      const detail = error?.response?.data?.detail || error?.message || 'Unknown error';
      setResolveError(`Failed to resolve dispute: ${detail}`);
    } finally {
      setIsResolving(false);
    }
  };

  const getStatusColor = (status: string): string => {
    switch (status.toLowerCase()) {
      case 'pending': return 'status-pending';
      case 'investigating': return 'status-investigating';
      case 'resolved': return 'status-resolved';
      case 'rejected': return 'status-rejected';
      default: return '';
    }
  };

  const pendingDisputes = disputes.filter(d => {
    const s = d.status.toLowerCase();
    return s === 'pending' || s === 'investigating' || s === 'open';
  });
  const resolvedDisputes = disputes.filter(d => {
    const s = d.status.toLowerCase();
    return s === 'resolved' || s === 'rejected' || s === 'closed';
  });

  return (
    <div className="dispute-panel">
      <h3>Combat Disputes</h3>
      
      <div className="dispute-stats">
        <div className="stat-card">
          <span className="stat-value">{pendingDisputes.length}</span>
          <span className="stat-label">Pending</span>
        </div>
        <div className="stat-card">
          <span className="stat-value">{resolvedDisputes.length}</span>
          <span className="stat-label">Resolved</span>
        </div>
      </div>

      <div className="dispute-content">
        <div className="dispute-list">
          <h4>Active Disputes</h4>
          {pendingDisputes.length === 0 ? (
            <p className="no-disputes">No pending disputes</p>
          ) : (
            pendingDisputes.map(dispute => (
              <div
                key={dispute.id}
                className={`dispute-item ${selectedDispute?.id === dispute.id ? 'selected' : ''}`}
                onClick={() => setSelectedDispute(dispute)}
              >
                <div className="dispute-header">
                  <span className={`dispute-status ${getStatusColor(dispute.status)}`}>
                    {dispute.status.toUpperCase()}
                  </span>
                  <span className="dispute-severity">
                    [{dispute.severity.toUpperCase()}]
                  </span>
                  <span className="dispute-time">
                    {new Date(dispute.timestamp).toLocaleString()}
                  </span>
                </div>

                <div className="dispute-info">
                  <p className="dispute-reason">{dispute.description}</p>
                  <p className="dispute-type">
                    Type: <strong>{dispute.type}</strong>
                  </p>
                  <p className="dispute-combat">
                    Combat ID: {dispute.combat_id || '—'}
                  </p>
                </div>
              </div>
            ))
          )}
        </div>

        {selectedDispute && (
          <div className="dispute-details">
            <h4>Dispute Details</h4>

            <div className="detail-section">
              <label>Dispute ID:</label>
              <span>{selectedDispute.id}</span>
            </div>

            <div className="detail-section">
              <label>Combat ID:</label>
              <span>{selectedDispute.combat_id || '—'}</span>
            </div>

            <div className="detail-section">
              <label>Type:</label>
              <span>{selectedDispute.type}</span>
            </div>

            <div className="detail-section">
              <label>Severity:</label>
              <span>{selectedDispute.severity}</span>
            </div>

            <div className="detail-section">
              <label>Timestamp:</label>
              <span>{new Date(selectedDispute.timestamp).toLocaleString()}</span>
            </div>

            <div className="detail-section">
              <label>Description:</label>
              <p>{selectedDispute.description}</p>
            </div>

            <div className="detail-section">
              <label>Recommended Action:</label>
              <p>{selectedDispute.recommended_action}</p>
            </div>

            {!selectedDispute.combat_id && (
              <div className="detail-section error-note">
                <span>⚠ No combat_id — this dispute cannot be resolved via the backend endpoint.</span>
              </div>
            )}

            <div className="resolution-form">
              <h5>Resolution</h5>

              {resolveError && (
                <div className="resolve-error" role="alert">
                  {resolveError}
                </div>
              )}

              <div className="form-group">
                <label>Resolution Action:</label>
                <select
                  value={resolution}
                  onChange={(e) => setResolution(e.target.value)}
                  disabled={isResolving}
                >
                  <option value="">Select action...</option>
                  <option value="No violation found">No violation found</option>
                  <option value="Warning issued">Warning issued</option>
                  <option value="Combat results adjusted">Combat results adjusted</option>
                  <option value="Player penalized">Player penalized</option>
                  <option value="Exploit fixed">Exploit fixed</option>
                </select>
              </div>

              <div className="form-group">
                <label>Admin Notes:</label>
                <textarea
                  value={adminNotes}
                  onChange={(e) => setAdminNotes(e.target.value)}
                  placeholder="Add detailed notes about the investigation and resolution..."
                  rows={4}
                  disabled={isResolving}
                />
              </div>

              <div className="resolution-actions">
                <button
                  className="btn btn-success"
                  onClick={() => handleResolve('resolve')}
                  disabled={!resolution || !adminNotes || isResolving || !selectedDispute.combat_id}
                >
                  {isResolving ? 'Resolving...' : 'Resolve Dispute'}
                </button>

                <button
                  className="btn btn-danger"
                  onClick={() => handleResolve('reject')}
                  disabled={!adminNotes || isResolving || !selectedDispute.combat_id}
                >
                  {isResolving ? 'Rejecting...' : 'Reject Dispute'}
                </button>

                <button
                  className="btn btn-secondary"
                  onClick={() => {
                    setSelectedDispute(null);
                    setResolution('');
                    setAdminNotes('');
                    setResolveError(null);
                  }}
                  disabled={isResolving}
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};