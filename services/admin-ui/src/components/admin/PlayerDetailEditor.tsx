import React, { useState, useEffect } from 'react';
import { api } from '../../utils/auth';
import { PlayerModel } from '../../types/playerManagement';
import './player-detail-editor.css';

interface PlayerDetailEditorProps {
  player: PlayerModel;
  onClose: () => void;
  onSave: (updatedPlayer: PlayerModel) => void;
}

interface PlayerEditData {
  username: string;
  email: string;
  credits: number;
  turns: number;
  current_sector_id: number | null;
  current_region_id: string | null;
  status: string;
  team_id: string | null;
  is_active: boolean;
}

const PlayerDetailEditor: React.FC<PlayerDetailEditorProps> = ({ player, onClose, onSave }) => {
  const [editData, setEditData] = useState<PlayerEditData>({
    username: player.username,
    email: player.email,
    credits: player.credits,
    turns: player.turns,
    current_sector_id: player.current_sector_id,
    current_region_id: player.current_region_id || null,
    status: player.status,
    team_id: player.team_id,
    is_active: player.status === 'active'
  });

  const [loading, setLoading] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [availableTeams, setAvailableTeams] = useState<any[]>([]);
  const [availableRegions, setAvailableRegions] = useState<any[]>([]);
  const [unsavedChanges, setUnsavedChanges] = useState(false);

  // Honesty: player-scoped emergency route does not exist (only ship-scoped
  // at admin_ships.py:205). Do not invent teleport/rescue/reset/clear chrome.
  const EMERGENCY_ENDPOINT = 'POST /api/v1/admin/players/{id}/emergency';

  useEffect(() => {
    loadAvailableTeams();
    loadAvailableRegions();
  }, []);

  useEffect(() => {
    // Check for unsaved changes
    const hasChanges =
      editData.username !== player.username ||
      editData.email !== player.email ||
      editData.credits !== player.credits ||
      editData.turns !== player.turns ||
      editData.current_sector_id !== player.current_sector_id ||
      editData.current_region_id !== (player.current_region_id || null) ||
      editData.status !== player.status ||
      editData.team_id !== player.team_id;

    setUnsavedChanges(hasChanges);
  }, [editData, player]);

  const loadAvailableTeams = async () => {
    try {
      const response = await api.get('/api/v1/admin/teams');
      setAvailableTeams((response.data as any)?.teams || []);
    } catch (error) {
      console.error('Failed to load teams:', error);
    }
  };

  const loadAvailableRegions = async () => {
    try {
      const response = await api.get('/api/v1/admin/regions');
      setAvailableRegions((response.data as any)?.regions || []);
    } catch (error) {
      console.error('Failed to load regions:', error);
    }
  };

  const handleFieldChange = (field: keyof PlayerEditData, value: any) => {
    setEditData(prev => ({
      ...prev,
      [field]: value
    }));
    
    // Clear errors when user starts typing
    if (errors.length > 0) {
      setErrors([]);
    }
  };

  const validateForm = (): boolean => {
    const newErrors: string[] = [];

    if (!editData.username.trim()) {
      newErrors.push('Username is required');
    }

    if (!editData.email.trim()) {
      newErrors.push('Email is required');
    } else if (!/\S+@\S+\.\S+/.test(editData.email)) {
      newErrors.push('Email format is invalid');
    }

    if (editData.credits < 0) {
      newErrors.push('Credits cannot be negative');
    }

    if (editData.turns < 0) {
      newErrors.push('Turns cannot be negative');
    }

    if (editData.current_sector_id && editData.current_sector_id < 1) {
      newErrors.push('Sector ID must be valid');
    }

    setErrors(newErrors);
    return newErrors.length === 0;
  };

  const handleSave = async () => {
    if (!validateForm()) {
      return;
    }

    setLoading(true);
    try {
      const updateData = {
        ...editData,
        is_active: editData.status === 'active'
      };

      await api.patch(`/api/v1/admin/players/${player.id}`, updateData);
      
      // Update the player object with new data
      const updatedPlayer = {
        ...player,
        ...editData,
        status: editData.status as "active" | "inactive" | "banned",
        is_active: editData.status === 'active'
      };

      onSave(updatedPlayer);
      onClose();
    } catch (error: any) {
      console.error('Failed to update player:', error);
      const errorMessage = error.response?.data?.detail || 'Failed to update player';
      setErrors([errorMessage]);
    } finally {
      setLoading(false);
    }
  };

  const handleCreditsAdjustment = (amount: number) => {
    const newCredits = Math.max(0, editData.credits + amount);
    handleFieldChange('credits', newCredits);
  };

  const handleTurnsAdjustment = (amount: number) => {
    const newTurns = Math.max(0, editData.turns + amount);
    handleFieldChange('turns', newTurns);
  };

  return (
    <div className="player-detail-editor" onClick={(e) => e.stopPropagation()}>
      <div className="editor-header">
        <h3>Edit Player: {player.username}</h3>
        <div className="header-actions">
          {unsavedChanges && <span className="unsaved-indicator">Unsaved Changes</span>}
          <button onClick={onClose} className="close-btn">×</button>
        </div>
      </div>

      {errors.length > 0 && (
        <div className="error-banner">
          {errors.map((error, index) => (
            <div key={index} className="error-message">{error}</div>
          ))}
        </div>
      )}

      <div className="editor-content">
        <div className="editor-section">
          <h4>Account Information</h4>
          <div className="form-grid">
            <div className="form-group">
              <label>Username:</label>
              <input
                type="text"
                value={editData.username}
                onChange={(e) => handleFieldChange('username', e.target.value)}
                disabled={loading}
              />
            </div>
            
            <div className="form-group">
              <label>Email:</label>
              <input
                type="email"
                value={editData.email}
                onChange={(e) => handleFieldChange('email', e.target.value)}
                disabled={loading}
              />
            </div>
            
            <div className="form-group">
              <label>Status:</label>
              <select
                value={editData.status}
                onChange={(e) => handleFieldChange('status', e.target.value)}
                disabled={loading}
              >
                <option value="active">Active</option>
                <option value="inactive">Inactive</option>
                <option value="banned">Banned</option>
                <option value="suspended">Suspended</option>
              </select>
            </div>

            <div className="form-group">
              <label>Team:</label>
              <select
                value={editData.team_id || ''}
                onChange={(e) => handleFieldChange('team_id', e.target.value || null)}
                disabled={loading}
              >
                <option value="">No Team</option>
                {availableTeams.map(team => (
                  <option key={team.id} value={team.id}>
                    {team.name}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>

        <div className="editor-section">
          <h4>Game Statistics</h4>
          <div className="form-grid">
            <div className="form-group">
              <label>Credits:</label>
              <div className="input-with-controls">
                <input
                  type="number"
                  value={editData.credits}
                  onChange={(e) => handleFieldChange('credits', parseInt(e.target.value) || 0)}
                  disabled={loading}
                />
                <div className="adjustment-controls">
                  <button onClick={() => handleCreditsAdjustment(1000)}>+1K</button>
                  <button onClick={() => handleCreditsAdjustment(10000)}>+10K</button>
                  <button onClick={() => handleCreditsAdjustment(100000)}>+100K</button>
                  <button onClick={() => handleCreditsAdjustment(-1000)}>-1K</button>
                </div>
              </div>
            </div>

            <div className="form-group">
              <label>Turns:</label>
              <div className="input-with-controls">
                <input
                  type="number"
                  value={editData.turns}
                  onChange={(e) => handleFieldChange('turns', parseInt(e.target.value) || 0)}
                  disabled={loading}
                />
                <div className="adjustment-controls">
                  <button onClick={() => handleTurnsAdjustment(100)}>+100</button>
                  <button onClick={() => handleTurnsAdjustment(500)}>+500</button>
                  <button onClick={() => handleTurnsAdjustment(1000)}>+1000</button>
                  <button onClick={() => handleTurnsAdjustment(-100)}>-100</button>
                </div>
              </div>
            </div>

            <div className="form-group">
              <label>Current Region:</label>
              <select
                value={editData.current_region_id || ''}
                onChange={(e) => handleFieldChange('current_region_id', e.target.value || null)}
                disabled={loading}
              >
                <option value="">No Region</option>
                {availableRegions.map(region => (
                  <option key={region.id} value={region.id}>
                    {region.display_name || region.name} ({region.total_sectors} sectors)
                  </option>
                ))}
              </select>
            </div>

            <div className="form-group">
              <label>Current Sector:</label>
              <input
                type="number"
                value={editData.current_sector_id || ''}
                onChange={(e) => handleFieldChange('current_sector_id', parseInt(e.target.value) || null)}
                placeholder="Sector ID"
                disabled={loading}
              />
            </div>
          </div>
        </div>

        <div className="editor-section">
          <h4>Emergency Operations</h4>
          <div
            role="note"
            style={{
              margin: '0', padding: '10px 12px',
              background: 'rgba(234, 179, 8, 0.12)', border: '1px solid rgba(234, 179, 8, 0.35)',
              borderRadius: '6px', color: '#fbbf24', fontSize: '0.82rem', lineHeight: 1.4
            }}
          >
            Emergency operations are unavailable: the backend endpoint{' '}
            <code style={{ color: '#fde68a' }}>{EMERGENCY_ENDPOINT}</code> is not implemented.
            This editor does not invent teleport / reset-turns / rescue / clear-debt controls.
          </div>
        </div>

        {/* ARIA Personal Assistant Section */}
        <div className="editor-section aria-assistant-section">
          <h4>🤖 ARIA Personal Assistant</h4>
          <div className="aria-subtitle">
            Autonomous Resource Intelligence Assistant - {player.username}&apos;s Personal AI
          </div>

          {player.aria ? (
            <>
              <div className="aria-metrics-grid">
                <div className="aria-metric-item">
                  <span className="aria-metric-label">AI Trust Level:</span>
                  <div className="aria-trust-bar">
                    <div
                      className="aria-trust-fill"
                      style={{ width: `${player.aria.trust_level}%` }}
                    />
                    <span className="aria-trust-text">{player.aria.trust_level}%</span>
                  </div>
                </div>
                <div className="aria-metric-item">
                  <span className="aria-metric-label">Recommendations Accepted:</span>
                  <span className="aria-metric-value">
                    {player.aria.recommendations_accepted} / {player.aria.recommendations_total}
                    ({Math.round((player.aria.recommendations_accepted / player.aria.recommendations_total) * 100)}%)
                  </span>
                </div>
                <div className="aria-metric-item">
                  <span className="aria-metric-label">Data Collection Points:</span>
                  <span className="aria-metric-value">{player.aria.data_points.toLocaleString()} interactions</span>
                </div>
                <div className="aria-metric-item">
                  <span className="aria-metric-label">Personal Model Status:</span>
                  <span className="aria-metric-value trained">{player.aria.model_status}</span>
                </div>
                <div className="aria-metric-item">
                  <span className="aria-metric-label">Trading Style Learned:</span>
                  <span className="aria-metric-value">{player.aria.trading_style}</span>
                </div>
                <div className="aria-metric-item">
                  <span className="aria-metric-label">Last ARIA Interaction:</span>
                  <span className="aria-metric-value">
                    {player.aria.last_interaction ? new Date(player.aria.last_interaction).toLocaleString() : 'Never'}
                  </span>
                </div>
                <div className="aria-metric-item">
                  <span className="aria-metric-label">AI-Generated Profits (7d):</span>
                  <span className="aria-metric-value credits">
                    {player.aria.ai_generated_profits_7d >= 0 ? '+' : ''}
                    {player.aria.ai_generated_profits_7d.toLocaleString()} credits
                  </span>
                </div>
                <div className="aria-metric-item">
                  <span className="aria-metric-label">Behavioral Classification:</span>
                  <span className="aria-metric-value">{player.aria.behavioral_classification}</span>
                </div>
              </div>

              {player.aria.most_used_features && player.aria.most_used_features.length > 0 && (
                <div className="aria-features-used">
                  <h5>Most Used ARIA Features</h5>
                  <div className="aria-feature-list">
                    {player.aria.most_used_features.map((feature, index) => (
                      <div key={index} className="aria-feature-item">
                        <span className="feature-icon">🔹</span>
                        <span className="feature-name">{feature.feature_name}</span>
                        <span className="feature-usage">{feature.usage_count} uses</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div
                role="note"
                className="aria-controls"
                style={{
                  margin: '12px 0 0', padding: '10px 12px',
                  background: 'rgba(234, 179, 8, 0.12)', border: '1px solid rgba(234, 179, 8, 0.35)',
                  borderRadius: '6px', color: '#fbbf24', fontSize: '0.82rem', lineHeight: 1.4
                }}
              >
                ARIA reset / retrain / export actions are unavailable: no admin API exists for them.
                Metrics above are read-only from the player payload.
              </div>
            </>
          ) : (
            <div className="aria-empty-state">
              <div className="empty-state-icon">🤖</div>
              <h5>ARIA Data Collection Not Started</h5>
              <p>
                This player&apos;s ARIA personal intelligence system has not collected any data yet.
                ARIA will begin learning once the player:
              </p>
              <ul>
                <li>Explores sectors and discovers new locations</li>
                <li>Engages in trading at various ports</li>
                <li>Makes strategic decisions in the game</li>
              </ul>
              <p className="text-muted">
                Data collection is automatic and privacy-protected. Each player&apos;s ARIA is
                completely isolated and learns only from their personal gameplay.
              </p>
            </div>
          )}
        </div>

        <div className="editor-section">
          <h4>Player Assets Summary</h4>
          <div className="assets-readonly">
            <div className="asset-item">
              <span className="asset-label">Ships Owned:</span>
              <span className="asset-value">{player.assets.ships_count ?? '—'}</span>
            </div>
            <div className="asset-item">
              <span className="asset-label">Planets Owned:</span>
              <span className="asset-value">{player.assets.planets_count ?? '—'}</span>
            </div>
            <div className="asset-item">
              <span className="asset-label">Ports Owned:</span>
              <span className="asset-value">{player.assets.stations_count ?? '—'}</span>
            </div>
            <div className="asset-item">
              <span className="asset-label">Total Asset Value:</span>
              <span className="asset-value credits">
                {player.assets.total_value != null
                  ? player.assets.total_value.toLocaleString()
                  : '—'}
              </span>
            </div>
          </div>
        </div>
      </div>

      <div className="editor-actions">
        <button 
          onClick={onClose} 
          className="btn btn-secondary"
          disabled={loading}
        >
          Cancel
        </button>
        <button 
          onClick={handleSave} 
          className="btn btn-primary"
          disabled={loading || !unsavedChanges}
        >
          {loading ? 'Saving...' : 'Save Changes'}
        </button>
      </div>
    </div>
  );
};

export default PlayerDetailEditor;