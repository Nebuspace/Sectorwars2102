import React, { useCallback, useEffect, useState } from 'react';
import PageHeader from '../ui/PageHeader';
import { api } from '../../utils/auth';
import './contract-dispute-arbitration.css';

// Matches the backend _serialize_dispute() shape in admin_contract_disputes.py
interface DisputedContract {
  id: string;
  payment: number | null;
  penalty: number | null;
  dispute_notes: string | null;
  dispute_filed_at: string | null;
  deadline: string | null;
  commodity_type: string | null;
  quantity: number | null;
  acceptor_player_id: string | null;
  issuer_type: string | null;
  issuer_id: string | null;
  escalated_to_admin: boolean;
  contract_type: string | null;
  status: string | null;
}

// Matches ContractDisputeResolution (src/models/contract.py)
const OUTCOMES: Array<{ value: string; label: string }> = [
  { value: 'full_payout', label: 'Full Payout' },
  { value: 'partial_payout', label: 'Partial Payout' },
  { value: 'refund', label: 'Refund' },
  { value: 'split', label: 'Split' },
  { value: 'penalty', label: 'Penalty' },
];

export const ContractDisputeArbitration: React.FC = () => {
  const [disputes, setDisputes] = useState<DisputedContract[]>([]);
  const [selected, setSelected] = useState<DisputedContract | null>(null);
  const [outcome, setOutcome] = useState('');
  const [notes, setNotes] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [isResolving, setIsResolving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resolveError, setResolveError] = useState<string | null>(null);

  const loadDisputes = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await api.get('/api/v1/admin/contracts/disputes');
      setDisputes(res.data as DisputedContract[]);
    } catch (err: any) {
      setDisputes([]);
      setError(err?.response?.data?.detail || err?.response?.data?.message || 'Failed to load disputed contracts');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadDisputes();
    const interval = setInterval(loadDisputes, 30000);
    return () => clearInterval(interval);
  }, [loadDisputes]);

  const handleResolve = async () => {
    if (!selected || !outcome) return;
    setIsResolving(true);
    setResolveError(null);
    try {
      await api.post(`/api/v1/admin/contracts/${selected.id}/resolve-dispute`, {
        outcome,
        notes: notes || undefined,
      });
      setSelected(null);
      setOutcome('');
      setNotes('');
      await loadDisputes();
    } catch (err: any) {
      setResolveError(err?.response?.data?.detail || err?.response?.data?.message || 'Failed to resolve dispute');
    } finally {
      setIsResolving(false);
    }
  };

  if (isLoading) {
    return (
      <div className="contract-dispute-arbitration loading">
        <PageHeader title="Contract Dispute Arbitration" />
        <div className="loading-spinner">Loading disputed contracts...</div>
      </div>
    );
  }

  return (
    <div className="contract-dispute-arbitration">
      <PageHeader title="Contract Dispute Arbitration" subtitle="Tier-2 admin ruling on escalated trade-contract disputes" />

      {error && (
        <div className="alert error" style={{ marginBottom: '20px' }}>
          <span className="alert-icon">❌</span>
          <span className="alert-message">{error}</span>
        </div>
      )}

      <div className="cda-content">
        <div className="cda-queue">
          <h4>Escalated Queue ({disputes.length})</h4>
          {disputes.length === 0 ? (
            <p className="cda-empty">No contracts currently escalated to Tier-2.</p>
          ) : (
            disputes.map((c) => (
              <div
                key={c.id}
                className={`cda-item ${selected?.id === c.id ? 'selected' : ''}`}
                onClick={() => {
                  setSelected(c);
                  setOutcome('');
                  setNotes('');
                  setResolveError(null);
                }}
              >
                <div className="cda-item-header">
                  <span className="cda-item-type">{c.contract_type || 'unknown'}</span>
                  <span className="cda-item-filed">
                    {c.dispute_filed_at ? new Date(c.dispute_filed_at).toLocaleString() : '—'}
                  </span>
                </div>
                <p className="cda-item-summary">
                  {c.commodity_type || 'escort'} {c.quantity ? `x${c.quantity}` : ''} — payment {c.payment ?? '—'}
                </p>
              </div>
            ))
          )}
        </div>

        {selected && (
          <div className="cda-evidence">
            <h4>Evidence Panel</h4>

            <div className="cda-detail-section">
              <label>Contract ID:</label>
              <span>{selected.id}</span>
            </div>
            <div className="cda-detail-section">
              <label>Contract / Issuer Type:</label>
              <span>{selected.contract_type || '—'} / {selected.issuer_type || '—'}</span>
            </div>
            <div className="cda-detail-section">
              <label>Issuer ID:</label>
              <span>{selected.issuer_id || '—'}</span>
            </div>
            <div className="cda-detail-section">
              <label>Acceptor Player ID:</label>
              <span>{selected.acceptor_player_id || '—'}</span>
            </div>
            <div className="cda-detail-section">
              <label>Commodity / Quantity:</label>
              <span>{selected.commodity_type || '—'} {selected.quantity ? `x${selected.quantity}` : ''}</span>
            </div>
            <div className="cda-detail-section">
              <label>Payment / Penalty:</label>
              <span>{selected.payment ?? '—'} / {selected.penalty ?? '—'}</span>
            </div>
            <div className="cda-detail-section">
              <label>Deadline:</label>
              <span>{selected.deadline ? new Date(selected.deadline).toLocaleString() : '—'}</span>
            </div>
            <div className="cda-detail-section">
              <label>Dispute Filed At:</label>
              <span>{selected.dispute_filed_at ? new Date(selected.dispute_filed_at).toLocaleString() : '—'}</span>
            </div>
            <div className="cda-detail-section">
              <label>Dispute Notes:</label>
              <p>{selected.dispute_notes || '—'}</p>
            </div>

            {/* Honest gap: canon's own Tier-2 section also names reputation/
                cooldowns and a "2 false disputes in 30d -> manual-review flag"
                auto-escalation. Neither is built yet (contract_service.py's
                dispute-section header comment: Settlement column only) --
                this panel does not invent one. */}
            <p className="cda-honest-gap">
              Reputation/cooldown history is not yet built for either party — this ruling is based on the evidence above only.
            </p>

            <div className="cda-ruling-form">
              <h5>Ruling</h5>

              {resolveError && (
                <div className="resolve-error" role="alert">
                  {resolveError}
                </div>
              )}

              <div className="form-group">
                <label>Outcome:</label>
                <div className="cda-outcome-buttons">
                  {OUTCOMES.map((o) => (
                    <button
                      key={o.value}
                      type="button"
                      className={`cda-outcome-btn ${outcome === o.value ? 'active' : ''}`}
                      onClick={() => setOutcome(o.value)}
                      disabled={isResolving}
                    >
                      {o.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="form-group">
                <label>Admin Notes:</label>
                <textarea
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="Reasoning for this ruling..."
                  rows={4}
                  disabled={isResolving}
                />
              </div>

              <div className="resolution-actions">
                <button
                  className="btn btn-success"
                  onClick={handleResolve}
                  disabled={!outcome || isResolving}
                >
                  {isResolving ? 'Submitting...' : 'Submit Ruling'}
                </button>
                <button
                  className="btn btn-secondary"
                  onClick={() => {
                    setSelected(null);
                    setOutcome('');
                    setNotes('');
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

export default ContractDisputeArbitration;
