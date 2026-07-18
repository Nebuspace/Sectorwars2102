import React from 'react';
import { PlayerModel } from '../../types/playerManagement';
import './emergency-operations-panel.css';

interface EmergencyOperationsPanelProps {
  player: PlayerModel;
  onClose: () => void;
  onUpdate: (updatedPlayer: PlayerModel) => void;
}

/**
 * Honesty: emergency-op + extended-player endpoints do not exist.
 * Keep the modal shell + live Current Status fields; do not invent
 * selectable operation cards / execute chrome.
 */
const EmergencyOperationsPanel: React.FC<EmergencyOperationsPanelProps> = ({
  player,
  onClose,
  onUpdate: _onUpdate
}) => {
  const EMERGENCY_OP_ENDPOINT = 'POST /api/v1/admin/players/emergency-operation';
  const EXTENDED_ENDPOINT = 'GET /api/v1/admin/players/{id}/extended';

  return (
    <div className="emergency-operations-panel" onClick={(e) => e.stopPropagation()}>
      <div className="panel-header">
        <h3>🚨 Emergency Operations</h3>
        <div className="player-info">
          <span className="player-name">{player.username}</span>
          <span className="player-status">{player.status}</span>
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
          This panel does not invent teleport / rescue / reset-turns operation cards or Execute controls.
        </div>

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
      </div>

      <div className="panel-actions">
        <button onClick={onClose} className="btn btn-secondary">
          Close
        </button>
      </div>
    </div>
  );
};

export default EmergencyOperationsPanel;
