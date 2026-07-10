import React, { useState } from 'react';
import { PlayerModel } from '../../types/playerManagement';
import './bulk-operation-panel.css';

interface BulkOperationPanelProps {
  selectedPlayers: PlayerModel[];
  onClose: () => void;
  onComplete: (operation: string, results: any) => void;
}

interface BulkOperationResult {
  playerId: string;
  playerName: string;
  success: boolean;
  error?: string;
}

interface OperationField {
  name: string;
  label: string;
  type: 'text' | 'number' | 'select' | 'textarea';
  placeholder?: string;
  options?: string[];
}

const BulkOperationPanel: React.FC<BulkOperationPanelProps> = ({
  selectedPlayers,
  onClose,
  onComplete: _onComplete
}) => {
  const [selectedOperation, setSelectedOperation] = useState<string>('');
  const [operationData, setOperationData] = useState<any>({});
  const [isExecuting] = useState(false);
  const [results] = useState<BulkOperationResult[]>([]);
  const [showResults] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  // Bulk operations are disabled: the backend route
  // POST /api/v1/admin/players/bulk-operation does not exist. The form
  // stays visible to document intent but cannot execute.
  const BULK_OP_ENDPOINT = 'POST /api/v1/admin/players/bulk-operation';

  const operations = [
    {
      id: 'adjust_credits',
      name: 'Adjust Credits',
      description: 'Add or remove credits from selected players',
      fields: [
        { name: 'amount', label: 'Credit Amount', type: 'number' as const, placeholder: '1000 (negative to remove)' }
      ] as OperationField[]
    },
    {
      id: 'adjust_turns',
      name: 'Adjust Turns',
      description: 'Add or remove turns from selected players',
      fields: [
        { name: 'amount', label: 'Turn Amount', type: 'number' as const, placeholder: '100 (negative to remove)' }
      ] as OperationField[]
    },
    {
      id: 'change_status',
      name: 'Change Status',
      description: 'Change account status for selected players',
      fields: [
        { 
          name: 'status', 
          label: 'New Status', 
          type: 'select' as const, 
          options: ['active', 'inactive', 'suspended', 'banned'] 
        }
      ] as OperationField[]
    },
    {
      id: 'teleport',
      name: 'Teleport Players',
      description: 'Move selected players to a specific sector',
      fields: [
        { name: 'sector_id', label: 'Target Sector ID', type: 'number' as const, placeholder: '1' }
      ] as OperationField[]
    },
    {
      id: 'send_message',
      name: 'Send Message',
      description: 'Send a system message to selected players',
      fields: [
        { name: 'subject', label: 'Subject', type: 'text' as const, placeholder: 'Administrative Notice' },
        { name: 'message', label: 'Message', type: 'textarea' as const, placeholder: 'Enter your message...' }
      ] as OperationField[]
    },
    {
      id: 'reset_password',
      name: 'Reset Passwords',
      description: 'Force password reset for selected players',
      fields: [] as OperationField[]
    },
    {
      id: 'clear_violations',
      name: 'Clear Violations',
      description: 'Clear all violation records for selected players',
      fields: [] as OperationField[]
    }
  ];

  const handleFieldChange = (fieldName: string, value: any) => {
    setOperationData((prev: any) => ({
      ...prev,
      [fieldName]: value
    }));
  };

  // Disabled: the bulk-operation backend endpoint does not exist. We keep
  // this handler as an inline-notice guard rather than wiring a dead write.
  const executeOperation = () => {
    setNotice(`Bulk operations are unavailable: the backend endpoint ${BULK_OP_ENDPOINT} is not implemented.`);
  };

  const selectedOp = operations.find(op => op.id === selectedOperation);

  if (showResults) {
    const successCount = results.filter(r => r.success).length;
    const failureCount = results.filter(r => !r.success).length;

    return (
      <div className="bulk-operation-panel results">
        <div className="panel-header">
          <h3>Bulk Operation Results</h3>
          <button onClick={onClose} className="close-btn">×</button>
        </div>

        <div className="results-summary">
          <div className="summary-stats">
            <div className="stat success">
              <span className="count">{successCount}</span>
              <span className="label">Successful</span>
            </div>
            <div className="stat failure">
              <span className="count">{failureCount}</span>
              <span className="label">Failed</span>
            </div>
          </div>
        </div>

        <div className="results-list">
          {results.map((result) => (
            <div key={result.playerId} className={`result-item ${result.success ? 'success' : 'failure'}`}>
              <div className="player-info">
                <span className="player-name">{result.playerName}</span>
                <span className="player-id">{result.playerId.slice(0, 8)}</span>
              </div>
              <div className="result-status">
                {result.success ? (
                  <span className="success-icon">✓ Success</span>
                ) : (
                  <span className="error-message">✗ {result.error}</span>
                )}
              </div>
            </div>
          ))}
        </div>

        <div className="panel-actions">
          <button onClick={onClose} className="btn btn-primary">
            Close
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
        <button onClick={onClose} className="close-btn">×</button>
      </div>

      <div className="panel-content">
        <div
          role="note"
          style={{
            margin: '0 0 16px 0', padding: '10px 12px',
            background: 'rgba(234, 179, 8, 0.12)', border: '1px solid rgba(234, 179, 8, 0.35)',
            borderRadius: '6px', color: '#fbbf24', fontSize: '0.82rem', lineHeight: 1.4
          }}
        >
          Bulk operations are unavailable: the backend endpoint{' '}
          <code style={{ color: '#fde68a' }}>{BULK_OP_ENDPOINT}</code> is not implemented.
          The form below is shown to document intended capability.
        </div>
        {notice && (
          <div
            role="alert"
            style={{
              margin: '0 0 16px 0', padding: '10px 12px',
              background: 'rgba(239, 68, 68, 0.12)', border: '1px solid rgba(239, 68, 68, 0.35)',
              borderRadius: '6px', color: '#fca5a5', fontSize: '0.82rem', lineHeight: 1.4
            }}
          >
            {notice}
          </div>
        )}
        <div className="operation-selection">
          <h4>Select Operation</h4>
          <div className="operation-grid">
            {operations.map((operation) => (
              <div
                key={operation.id}
                className={`operation-card ${selectedOperation === operation.id ? 'selected' : ''}`}
                onClick={() => setSelectedOperation(operation.id)}
              >
                <h5>{operation.name}</h5>
                <p>{operation.description}</p>
              </div>
            ))}
          </div>
        </div>

        {selectedOp && (
          <div className="operation-config">
            <h4>Configure {selectedOp.name}</h4>
            <div className="config-form">
              {selectedOp.fields.map((field) => (
                <div key={field.name} className="form-group">
                  <label>{field.label}:</label>
                  {field.type === 'select' ? (
                    <select
                      value={operationData[field.name] || ''}
                      onChange={(e) => handleFieldChange(field.name, e.target.value)}
                      disabled={isExecuting}
                    >
                      <option value="">Select {field.label}</option>
                      {field.options?.map(option => (
                        <option key={option} value={option}>{option}</option>
                      ))}
                    </select>
                  ) : field.type === 'textarea' ? (
                    <textarea
                      value={operationData[field.name] || ''}
                      onChange={(e) => handleFieldChange(field.name, e.target.value)}
                      placeholder={field.placeholder}
                      disabled={isExecuting}
                      rows={4}
                    />
                  ) : (
                    <input
                      type={field.type}
                      value={operationData[field.name] || ''}
                      onChange={(e) => handleFieldChange(field.name, 
                        field.type === 'number' ? parseInt(e.target.value) || 0 : e.target.value
                      )}
                      placeholder={field.placeholder}
                      disabled={isExecuting}
                    />
                  )}
                </div>
              ))}
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
          </div>
        </div>
      </div>

      <div className="panel-actions">
        <button onClick={onClose} className="btn btn-secondary" disabled={isExecuting}>
          Cancel
        </button>
        <button
          onClick={executeOperation}
          className="btn btn-danger"
          disabled
          title={`Disabled — missing backend endpoint ${BULK_OP_ENDPOINT}`}
        >
          {`Execute on ${selectedPlayers.length} Players`}
        </button>
      </div>
    </div>
  );
};

export default BulkOperationPanel;