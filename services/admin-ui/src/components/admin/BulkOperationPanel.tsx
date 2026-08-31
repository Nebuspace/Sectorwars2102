import React, { useCallback, useMemo, useState } from 'react';
import { api } from '../../utils/auth';
import { formatAdminApiError } from '../../utils/adminApiError';
import { PlayerModel } from '../../types/playerManagement';
import './bulk-operation-panel.css';

interface BulkOperationPanelProps {
  selectedPlayers: PlayerModel[];
  onClose: () => void;
  onComplete: (operation: string, results: BulkOperationResponse) => void;
}

type BulkOpType = 'CREDIT_ADJUST' | 'TURN_GRANT' | 'STATUS_CHANGE' | 'REPUTATION_ADJUST';

interface BulkOperationItemResult {
  player_id: string;
  success: boolean;
  detail?: string | null;
}

export interface BulkOperationResponse {
  operation: string;
  applied: number;
  rejected: number;
  results: BulkOperationItemResult[];
}

const OPERATIONS: Array<{
  id: BulkOpType;
  title: string;
  description: string;
}> = [
  {
    id: 'CREDIT_ADJUST',
    title: 'Adjust Credits',
    description: 'Add or subtract credits from each selected player.',
  },
  {
    id: 'TURN_GRANT',
    title: 'Grant Turns',
    description: 'Add or subtract turns from each selected player.',
  },
  {
    id: 'STATUS_CHANGE',
    title: 'Change Status',
    description: 'Set account status (active, inactive, banned, suspended).',
  },
  {
    id: 'REPUTATION_ADJUST',
    title: 'Adjust Reputation',
    description: 'Set absolute reputation with a faction for each player.',
  },
];

const STATUS_OPTIONS = ['active', 'inactive', 'banned', 'suspended'] as const;

const bulkOpApiError = (err: unknown, fallback: string): string =>
  formatAdminApiError(err, {
    fallback,
    scopeHint:
      'bulk player operations require the matching admin players scope (VIEW plus ADJUST_CREDITS, SUSPEND, or ADJUST_REP as appropriate)',
  });

const BulkOperationPanel: React.FC<BulkOperationPanelProps> = ({
  selectedPlayers,
  onClose,
  onComplete,
}) => {
  const [selectedOperation, setSelectedOperation] = useState<BulkOpType | null>(null);
  const [amount, setAmount] = useState('');
  const [newStatus, setNewStatus] = useState<string>('active');
  const [faction, setFaction] = useState('');
  const [reputationValue, setReputationValue] = useState('');
  const [reason, setReason] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [response, setResponse] = useState<BulkOperationResponse | null>(null);

  const playerNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const player of selectedPlayers) {
      map.set(player.id, player.username);
    }
    return map;
  }, [selectedPlayers]);

  const canConfigure = selectedPlayers.length > 0 && selectedOperation !== null;

  const formValid = useMemo(() => {
    if (!canConfigure || !reason.trim()) {
      return false;
    }
    if (selectedOperation === 'CREDIT_ADJUST' || selectedOperation === 'TURN_GRANT') {
      return amount.trim() !== '' && !Number.isNaN(Number(amount));
    }
    if (selectedOperation === 'STATUS_CHANGE') {
      return STATUS_OPTIONS.includes(newStatus as (typeof STATUS_OPTIONS)[number]);
    }
    if (selectedOperation === 'REPUTATION_ADJUST') {
      return faction.trim() !== '' && reputationValue.trim() !== '' && !Number.isNaN(Number(reputationValue));
    }
    return false;
  }, [amount, canConfigure, faction, newStatus, reason, reputationValue, selectedOperation]);

  const buildPayload = useCallback(() => {
    if (!selectedOperation) {
      throw new Error('No operation selected');
    }
    const parameters: Record<string, unknown> = { reason: reason.trim() };
    if (selectedOperation === 'CREDIT_ADJUST' || selectedOperation === 'TURN_GRANT') {
      parameters.amount = Number(amount);
    } else if (selectedOperation === 'STATUS_CHANGE') {
      parameters.new_status = newStatus;
    } else if (selectedOperation === 'REPUTATION_ADJUST') {
      parameters.reputation_changes = [
        { faction: faction.trim(), new_value: Number(reputationValue) },
      ];
    }
    return {
      player_ids: selectedPlayers.map((p) => p.id),
      operation: selectedOperation,
      parameters,
    };
  }, [amount, faction, newStatus, reason, reputationValue, selectedOperation, selectedPlayers]);

  const executeOperation = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const payload = buildPayload();
      const { data } = await api.post<BulkOperationResponse>(
        '/api/v1/admin/players/bulk-operation',
        payload,
      );
      setResponse(data);
      setConfirming(false);
      onComplete(data.operation, data);
    } catch (err) {
      setError(bulkOpApiError(err, 'Bulk operation failed'));
      setConfirming(false);
    } finally {
      setSubmitting(false);
    }
  };

  if (response) {
    return (
      <div className="bulk-operation-panel results" onClick={(e) => e.stopPropagation()}>
        <div className="panel-header">
          <h3>Bulk Operation Results</h3>
          <span className="player-count">{response.operation}</span>
          <button onClick={onClose} className="close-btn" type="button">
            ×
          </button>
        </div>

        <div className="panel-content">
          <div className="results-summary">
            <div className="summary-stats">
              <div className="stat success">
                <span className="count">{response.applied}</span>
                <span className="label">Applied</span>
              </div>
              <div className="stat failure">
                <span className="count">{response.rejected}</span>
                <span className="label">Rejected</span>
              </div>
            </div>
          </div>

          <div className="results-list">
            {response.results.map((result) => (
              <div
                key={result.player_id}
                className={`result-item ${result.success ? 'success' : 'failure'}`}
              >
                <div className="player-info">
                  <span className="player-name">
                    {playerNameById.get(result.player_id) ?? result.player_id}
                  </span>
                  <span className="player-id">{result.player_id}</span>
                </div>
                {result.success ? (
                  <span className="success-icon">Success</span>
                ) : (
                  <span className="error-message">{result.detail ?? 'Failed'}</span>
                )}
              </div>
            ))}
          </div>
        </div>

        <div className="panel-actions">
          <button onClick={onClose} className="btn btn-primary" type="button">
            Done
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="bulk-operation-panel" onClick={(e) => e.stopPropagation()}>
      <div className="panel-header">
        <h3>Bulk Operations</h3>
        <span className="player-count">{selectedPlayers.length} players selected</span>
        <button onClick={onClose} className="close-btn" type="button">
          ×
        </button>
      </div>

      <div className="panel-content">
        {error && (
          <div
            role="alert"
            style={{
              margin: '0 0 16px 0',
              padding: '10px 12px',
              background: 'rgba(231, 76, 60, 0.12)',
              border: '1px solid rgba(231, 76, 60, 0.35)',
              borderRadius: '6px',
              color: '#e74c3c',
              fontSize: '0.82rem',
              lineHeight: 1.4,
            }}
          >
            {error}
          </div>
        )}

        <div className="operation-selection">
          <h4>Select Operation</h4>
          <div className="operation-grid">
            {OPERATIONS.map((op) => (
              <div
                key={op.id}
                className={`operation-card ${selectedOperation === op.id ? 'selected' : ''}`}
                onClick={() => {
                  setSelectedOperation(op.id);
                  setError(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    setSelectedOperation(op.id);
                    setError(null);
                  }
                }}
                role="button"
                tabIndex={0}
              >
                <h5>{op.title}</h5>
                <p>{op.description}</p>
              </div>
            ))}
          </div>
        </div>

        {canConfigure && (
          <div className="operation-config">
            <h4>Configuration</h4>
            <div className="config-form">
              {(selectedOperation === 'CREDIT_ADJUST' || selectedOperation === 'TURN_GRANT') && (
                <div className="form-group">
                  <label htmlFor="bulk-amount">
                    {selectedOperation === 'CREDIT_ADJUST' ? 'Credit delta' : 'Turn delta'}
                  </label>
                  <input
                    id="bulk-amount"
                    type="number"
                    value={amount}
                    onChange={(e) => setAmount(e.target.value)}
                    placeholder="e.g. 100 or -50"
                  />
                </div>
              )}

              {selectedOperation === 'STATUS_CHANGE' && (
                <div className="form-group">
                  <label htmlFor="bulk-status">New status</label>
                  <select
                    id="bulk-status"
                    value={newStatus}
                    onChange={(e) => setNewStatus(e.target.value)}
                  >
                    {STATUS_OPTIONS.map((status) => (
                      <option key={status} value={status}>
                        {status}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              {selectedOperation === 'REPUTATION_ADJUST' && (
                <>
                  <div className="form-group">
                    <label htmlFor="bulk-faction">Faction</label>
                    <input
                      id="bulk-faction"
                      type="text"
                      value={faction}
                      onChange={(e) => setFaction(e.target.value)}
                      placeholder="Faction name"
                    />
                  </div>
                  <div className="form-group">
                    <label htmlFor="bulk-reputation">New reputation value</label>
                    <input
                      id="bulk-reputation"
                      type="number"
                      value={reputationValue}
                      onChange={(e) => setReputationValue(e.target.value)}
                    />
                  </div>
                </>
              )}

              <div className="form-group">
                <label htmlFor="bulk-reason">Reason (required, audit-visible)</label>
                <textarea
                  id="bulk-reason"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  rows={3}
                  placeholder="Why is this bulk change being applied?"
                />
              </div>
            </div>
          </div>
        )}

        <div className="selected-players">
          <h4>Selected Players</h4>
          <div className="player-list">
            {selectedPlayers.slice(0, 10).map((player) => (
              <div key={player.id} className="player-item">
                <span className="player-name">{player.username}</span>
                <span className="player-credits">{player.credits.toLocaleString()} credits</span>
                <span className="player-status">{player.status}</span>
              </div>
            ))}
            {selectedPlayers.length > 10 && (
              <div className="more-players">
                ...and {selectedPlayers.length - 10} more players
              </div>
            )}
            {selectedPlayers.length === 0 && (
              <div className="more-players">No players selected.</div>
            )}
          </div>
        </div>

        {confirming && (
          <div
            role="alertdialog"
            aria-labelledby="bulk-confirm-title"
            style={{
              marginTop: '16px',
              padding: '16px',
              background: 'rgba(231, 76, 60, 0.08)',
              border: '1px solid rgba(231, 76, 60, 0.3)',
              borderRadius: '8px',
            }}
          >
            <h4 id="bulk-confirm-title" style={{ margin: '0 0 8px 0' }}>
              Confirm bulk operation
            </h4>
            <p style={{ margin: '0 0 12px 0', fontSize: '0.9rem' }}>
              Apply <strong>{selectedOperation}</strong> to{' '}
              <strong>{selectedPlayers.length}</strong> player(s)? This is audited and cannot be
              undone from this panel.
            </p>
            <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setConfirming(false)}
                disabled={submitting}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={() => void executeOperation()}
                disabled={submitting}
              >
                {submitting ? 'Executing…' : 'Confirm Execute'}
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="panel-actions">
        <button onClick={onClose} className="btn btn-secondary" type="button">
          Close
        </button>
        <button
          className="btn btn-primary"
          type="button"
          disabled={!formValid || submitting || confirming}
          onClick={() => setConfirming(true)}
        >
          Execute
        </button>
      </div>
    </div>
  );
};

export default BulkOperationPanel;
