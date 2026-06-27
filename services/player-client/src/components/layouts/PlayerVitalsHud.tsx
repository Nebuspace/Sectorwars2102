import React from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { useGame } from '../../contexts/GameContext';
import LogoutButton from '../auth/LogoutButton';
import './player-vitals-hud.css';

/**
 * PlayerVitalsHud — the always-on cockpit HUD that overlays the full-width
 * windshield band (WO-INVERTED-L, id=139 addendum). Three zones across the band:
 *   • commander NAME (left) — tinted by playerState.name_color;
 *   • live vitals CRED / TURN / DRONE / MINE (center) — the CRT readout strip;
 *   • LOGOUT (right) — reuses the shared LogoutButton.
 *
 * The bar itself is click-through (pointer-events: none) so it never steals
 * scene clicks; only the logout control and the failure-only refresh re-enable
 * pointer events. All sizing is rem so the HUD rides the #root UI-scale zoom.
 * The commander name + Logout were relocated here from the route rail (the rail
 * .rr-pilot row and rail LogoutButton are removed) so logout is always visible
 * regardless of console state.
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
        <div className="header-stat">
          <span className="header-stat-label">CRED</span>
          <span className="data-readout credits">{playerState?.credits?.toLocaleString() || '0'}</span>
        </div>
        <div className="header-stat">
          <span className="header-stat-label">TURN</span>
          <span className="data-readout turns">
            {playerState?.turns?.toLocaleString() || '0'}
            {typeof playerState?.max_turns === 'number' && (
              <span className="data-readout-max">/{playerState.max_turns.toLocaleString()}</span>
            )}
          </span>
        </div>
        <div className="header-stat">
          <span className="header-stat-label">DRONE</span>
          <span className="data-readout">{playerState?.defense_drones || '0'}</span>
        </div>
        <div className="header-stat">
          <span className="header-stat-label">MINE</span>
          <span className="data-readout">{playerState?.mines || '0'}</span>
        </div>
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
