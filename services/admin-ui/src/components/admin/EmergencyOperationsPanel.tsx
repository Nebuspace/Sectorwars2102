import React, { useState, useEffect } from 'react';
import { PlayerModel } from '../../types/playerManagement';
import './emergency-operations-panel.css';

interface EmergencyOperationsPanelProps {
  player: PlayerModel;
  onClose: () => void;
  onUpdate: (updatedPlayer: PlayerModel) => void;
}

interface EmergencyOperation {
  id: string;
  name: string;
  description: string;
  icon: string;
  category: 'location' | 'resources' | 'assets' | 'account';
  severity: 'low' | 'medium' | 'high' | 'critical';
  fields?: Array<{
    name: string;
    label: string;
    type: 'text' | 'number' | 'select' | 'textarea';
    placeholder?: string;
    options?: string[];
    required?: boolean;
  }>;
}

const EmergencyOperationsPanel: React.FC<EmergencyOperationsPanelProps> = ({
  player,
  onClose,
  onUpdate: _onUpdate
}) => {
  const [selectedOperation, setSelectedOperation] = useState<string>('');
  const [operationData, setOperationData] = useState<any>({});
  const [isExecuting] = useState(false);
  const [executionResult, setExecutionResult] = useState<string>('');
  const [, setPlayerInfo] = useState<any>(null);

  // Emergency operations are disabled: the backend routes
  // POST /api/v1/admin/players/emergency-operation and
  // GET /api/v1/admin/players/{id}/extended do not exist. The form stays
  // visible to document intent but cannot execute.
  const EMERGENCY_OP_ENDPOINT = 'POST /api/v1/admin/players/emergency-operation';
  const EXTENDED_ENDPOINT = 'GET /api/v1/admin/players/{id}/extended';

  const operations: EmergencyOperation[] = [
    {
      id: 'teleport_home',
      name: 'Teleport to Home',
      description: 'Instantly move player to Sol system (Sector 1)',
      icon: '🏠',
      category: 'location',
      severity: 'low'
    },
    {
      id: 'teleport_sector',
      name: 'Teleport to Sector',
      description: 'Move player to a specific sector',
      icon: '🌀',
      category: 'location',
      severity: 'medium',
      fields: [
        { name: 'sector_id', label: 'Target Sector ID', type: 'number', required: true, placeholder: 'Enter sector number' }
      ]
    },
    {
      id: 'rescue_ship',
      name: 'Rescue Ship',
      description: 'Teleport player\'s current ship to nearest port',
      icon: '🚁',
      category: 'location',
      severity: 'medium'
    },
    {
      id: 'reset_turns',
      name: 'Reset Turns',
      description: 'Grant player a full set of turns',
      icon: '🔄',
      category: 'resources',
      severity: 'low',
      fields: [
        { name: 'turn_amount', label: 'Number of Turns', type: 'number', required: true, placeholder: '1000' }
      ]
    },
    {
      id: 'emergency_credits',
      name: 'Emergency Credits',
      description: 'Grant emergency credits to stranded player',
      icon: '💰',
      category: 'resources',
      severity: 'medium',
      fields: [
        { name: 'credit_amount', label: 'Credit Amount', type: 'number', required: true, placeholder: '50000' },
        { name: 'reason', label: 'Reason', type: 'textarea', required: true, placeholder: 'Emergency assistance reason...' }
      ]
    },
    {
      id: 'clear_debt',
      name: 'Clear All Debt',
      description: 'Remove all negative credits from player account',
      icon: '💳',
      category: 'resources',
      severity: 'high'
    },
    {
      id: 'repair_ship',
      name: 'Repair Ship',
      description: 'Instantly repair player\'s current ship to 100%',
      icon: '🔧',
      category: 'assets',
      severity: 'low'
    },
    {
      id: 'refuel_ship',
      name: 'Refuel Ship',
      description: 'Fill player\'s ship with fuel ore and organics',
      icon: '⛽',
      category: 'assets',
      severity: 'low'
    },
    {
      id: 'clear_holds',
      name: 'Clear Cargo Holds',
      description: 'Empty all cargo from player\'s ship',
      icon: '📦',
      category: 'assets',
      severity: 'medium',
      fields: [
        { name: 'confirm_clear', label: 'Confirmation', type: 'select', required: true, options: ['', 'YES_CLEAR_CARGO'] }
      ]
    },
    {
      id: 'unlock_account',
      name: 'Unlock Account',
      description: 'Remove temporary locks or restrictions',
      icon: '🔓',
      category: 'account',
      severity: 'medium'
    },
    {
      id: 'reset_password',
      name: 'Force Password Reset',
      description: 'Invalidate current password and force reset',
      icon: '🔑',
      category: 'account',
      severity: 'high'
    },
    {
      id: 'ban_account',
      name: 'Emergency Ban',
      description: 'Immediately ban player account',
      icon: '🚫',
      category: 'account',
      severity: 'critical',
      fields: [
        { name: 'ban_reason', label: 'Ban Reason', type: 'textarea', required: true, placeholder: 'Reason for emergency ban...' },
        { name: 'ban_duration', label: 'Duration (hours)', type: 'number', placeholder: '24 (0 for permanent)' }
      ]
    }
  ];

  // Extended player info is not fetched: the backend route
  // (EXTENDED_ENDPOINT) does not exist, so we do not issue a dead read.
  // The panel renders from the player props already passed in.
  useEffect(() => {
    setPlayerInfo(null);
  }, [player.id]);

  const handleFieldChange = (fieldName: string, value: any) => {
    setOperationData((prev: any) => ({
      ...prev,
      [fieldName]: value
    }));
  };

  // Disabled: the emergency-operation backend endpoint does not exist.
  // Rather than wiring a dead write, this surfaces an inline notice.
  const executeOperation = () => {
    setExecutionResult(
      `⚠ Emergency operations are unavailable: the backend endpoint ${EMERGENCY_OP_ENDPOINT} is not implemented.`
    );
  };

  const groupedOperations = operations.reduce((groups, operation) => {
    const category = operation.category;
    if (!groups[category]) {
      groups[category] = [];
    }
    groups[category].push(operation);
    return groups;
  }, {} as Record<string, EmergencyOperation[]>);

  const selectedOp = operations.find(op => op.id === selectedOperation);
  const severityColors = {
    low: '#27ae60',
    medium: '#f39c12',
    high: '#e67e22',
    critical: '#e74c3c'
  };

  return (
    <div className="emergency-operations-panel" onClick={(e) => e.stopPropagation()}>
      <div className="panel-header">
        <h3>🚨 Emergency Operations</h3>
        <div className="player-info">
          <span className="player-name">{player.username}</span>
          <span className="player-status" style={{ color: severityColors[player.status === 'banned' ? 'critical' : 'low'] }}>
            {player.status}
          </span>
        </div>
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
          Emergency operations are unavailable: the backend endpoints{' '}
          <code style={{ color: '#fde68a' }}>{EMERGENCY_OP_ENDPOINT}</code> and{' '}
          <code style={{ color: '#fde68a' }}>{EXTENDED_ENDPOINT}</code> are not implemented.
          The controls below are shown to document intended capability.
        </div>
        {/* Current Player Status */}
        <div className="player-status-card">
          <h4>Current Status</h4>
          <div className="status-grid">
            <div className="status-item">
              <span className="label">Location:</span>
              <span className="value">Sector {player.current_sector_id || 'Unknown'}</span>
            </div>
            <div className="status-item">
              <span className="label">Credits:</span>
              <span className={`value ${player.credits < 0 ? 'negative' : 'positive'}`}>
                {player.credits.toLocaleString()}
              </span>
            </div>
            <div className="status-item">
              <span className="label">Turns:</span>
              <span className={`value ${player.turns < 10 ? 'low' : ''}`}>
                {player.turns}
              </span>
            </div>
            <div className="status-item">
              <span className="label">Last Login:</span>
              <span className="value">
                {player.activity.last_login ? new Date(player.activity.last_login).toLocaleString() : '—'}
              </span>
            </div>
          </div>
        </div>

        {/* Operation Categories */}
        <div className="operations-section">
          <h4>Available Operations</h4>
          {Object.entries(groupedOperations).map(([category, ops]) => (
            <div key={category} className="operation-category">
              <h5>{category.charAt(0).toUpperCase() + category.slice(1)} Operations</h5>
              <div className="operation-grid">
                {ops.map((operation) => (
                  <div
                    key={operation.id}
                    className={`operation-card ${selectedOperation === operation.id ? 'selected' : ''} severity-${operation.severity}`}
                    onClick={() => setSelectedOperation(operation.id)}
                  >
                    <div className="operation-icon">{operation.icon}</div>
                    <div className="operation-info">
                      <h6>{operation.name}</h6>
                      <p>{operation.description}</p>
                      <span className={`severity-badge ${operation.severity}`}>
                        {operation.severity.toUpperCase()}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* Operation Configuration */}
        {selectedOp && (
          <div className="operation-config">
            <h4>Configure {selectedOp.name}</h4>
            {selectedOp.fields && selectedOp.fields.length > 0 ? (
              <div className="config-form">
                {selectedOp.fields.map((field) => (
                  <div key={field.name} className="form-group">
                    <label>
                      {field.label}
                      {field.required && <span className="required">*</span>}:
                    </label>
                    {field.type === 'select' ? (
                      <select
                        value={operationData[field.name] || ''}
                        onChange={(e) => handleFieldChange(field.name, e.target.value)}
                        disabled={isExecuting}
                        required={field.required}
                      >
                        {field.options?.map(option => (
                          <option key={option} value={option}>
                            {option === '' ? 'Select option...' : option}
                          </option>
                        ))}
                      </select>
                    ) : field.type === 'textarea' ? (
                      <textarea
                        value={operationData[field.name] || ''}
                        onChange={(e) => handleFieldChange(field.name, e.target.value)}
                        placeholder={field.placeholder}
                        disabled={isExecuting}
                        required={field.required}
                        rows={3}
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
                        required={field.required}
                      />
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="no-config">This operation requires no additional configuration.</p>
            )}
          </div>
        )}

        {/* Execution Result */}
        {executionResult && (
          <div className={`execution-result ${executionResult.includes('✅') ? 'success' : 'error'}`}>
            {executionResult}
          </div>
        )}
      </div>

      <div className="panel-actions">
        <button onClick={onClose} className="btn btn-secondary" disabled={isExecuting}>
          Close
        </button>
        <button
          onClick={executeOperation}
          className={`btn ${selectedOp?.severity === 'critical' ? 'btn-critical' : 'btn-danger'}`}
          disabled
          title={`Disabled — missing backend endpoint ${EMERGENCY_OP_ENDPOINT}`}
        >
          {selectedOp ? `Execute ${selectedOp.name}` : 'Select Operation'}
        </button>
      </div>
    </div>
  );
};

export default EmergencyOperationsPanel;