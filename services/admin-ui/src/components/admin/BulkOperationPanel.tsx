import React from 'react';
import { PlayerModel } from '../../types/playerManagement';
import './bulk-operation-panel.css';

interface BulkOperationPanelProps {
  selectedPlayers: PlayerModel[];
  onClose: () => void;
  onComplete: (operation: string, results: any) => void;
}

/**
 * Honesty: POST /api/v1/admin/players/bulk-operation does not exist.
 * Keep the modal shell + real selected-player list; do not invent
 * operation cards / config forms / disabled Execute.
 */
const BulkOperationPanel: React.FC<BulkOperationPanelProps> = ({
  selectedPlayers,
  onClose,
  onComplete: _onComplete
}) => {
  const BULK_OP_ENDPOINT = 'POST /api/v1/admin/players/bulk-operation';

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
          This panel does not invent Adjust Credits / Turns / Status / Teleport cards or an Execute control.
        </div>

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
      </div>

      <div className="panel-actions">
        <button onClick={onClose} className="btn btn-secondary">
          Close
        </button>
      </div>
    </div>
  );
};

export default BulkOperationPanel;
