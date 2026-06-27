import React from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { useGame } from '../../contexts/GameContext';
import LogoutButton from '../auth/LogoutButton';
import { formatCredits } from '../../utils/formatters';
import './player-vitals-hud.css';

/**
 * PlayerVitalsHud — the always-on cockpit HUD overlaying the full-width
 * windshield band (WO-INVERTED-L + WO-PLAYERINFO id=145). Three zones:
 *   • commander NAME (left) — tinted by playerState.name_color;
 *   • live vitals (center) — a SINGLE-ROW inline strip (Max: "too tall, make it
 *     wider"): ₡credits · TRN turns(/max) · ATK/DEF drones · MINE;
 *   • LOGOUT (right) — reuses the shared LogoutButton.
 *
 * Credits use the shared ₡ glyph (formatCredits, id=148) — never 'CRED'/'cr'.
 * The bar is click-through (pointer-events:none); only logout / the
 * failure-only refresh re-enable pointer events. All sizing is rem so the HUD
 * rides the #root UI-scale zoom.
 */
const PlayerVitalsHud: React.FC = () => {
  const { user } = useAuth();
  const { playerState, isLoading, refreshPlayerState } = useGame();

  return (
    <div className="player-vitals-hud">
      <div
        className="pvh-name"
        title={user?.username}
        style={{ '--pilot-color': playerState?.name_color || '#00D9FF' } as React.CSSProperties}
      >
        {user?.username || '—'}
      </div>

      <div className="pvh-vitals">
        <span className="pvh-stat pvh-credits" title="Credits">
          {formatCredits(playerState?.credits)}
        </span>
        <span className="pvh-stat" title="Turns">
          <span className="pvh-k">TRN</span>
          <span className="pvh-v">
            {playerState?.turns?.toLocaleString() || '0'}
            {typeof playerState?.max_turns === 'number' && (
              <span className="pvh-sub">/{playerState.max_turns.toLocaleString()}</span>
            )}
          </span>
        </span>
        <span className="pvh-stat" title="Attack drones (current ship)">
          <span className="pvh-k">ATK</span>
          <span className="pvh-v">{playerState?.attack_drones ?? 0}</span>
        </span>
        <span className="pvh-stat" title="Defense drones (current ship)">
          <span className="pvh-k">DEF</span>
          <span className="pvh-v">{playerState?.defense_drones ?? 0}</span>
        </span>
        <span className="pvh-stat" title="Mines">
          <span className="pvh-k">MINE</span>
          <span className="pvh-v">{playerState?.mines ?? 0}</span>
        </span>
        {!playerState && !isLoading && (
          <button
            onClick={refreshPlayerState}
            className="refresh-btn pvh-refresh"
            title="Refresh player state"
            aria-label="Refresh"
          >
            ⟳
          </button>
        )}
      </div>

      <div className="pvh-logout">
        <LogoutButton className="pvh-logout-btn" />
      </div>
    </div>
  );
};

export default PlayerVitalsHud;
