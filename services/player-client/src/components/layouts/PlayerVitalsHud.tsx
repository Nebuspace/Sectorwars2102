import React from 'react';
import { useGame } from '../../contexts/GameContext';
import './player-vitals-hud.css';

/**
 * PlayerVitalsHud — compact top-center readout strip that rides inside the
 * cockpit viewport (relocated from the deleted top game-header bar). Shows the
 * live vitals — CRED / TURN / DRONE / MINE — in the same CRT formatting as the
 * old commander bar. The commander NAME lives on the route rail, not here.
 *
 * The strip itself is click-through (pointer-events: none) so it never steals
 * cockpit clicks; only the refresh control re-enables pointer events, and it
 * only appears when player state failed to load (matching the old header).
 */
const PlayerVitalsHud: React.FC = () => {
  const { playerState, isLoading, refreshPlayerState } = useGame();

  return (
    <div className="player-vitals-hud">
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
  );
};

export default PlayerVitalsHud;
