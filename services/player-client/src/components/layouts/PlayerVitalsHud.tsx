import React from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { useGame } from '../../contexts/GameContext';
import LogoutButton from '../auth/LogoutButton';
import { formatCredits } from '../../utils/formatters';
import { TurnsIcon } from '../icons/TurnsIcon';
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

  // id=145d — turns regen: +N/hr subscript + hover→time-to-full (id=142 fields).
  const regenPerHr = playerState?.turn_regen_per_hour ?? 0;
  const turnsNow = playerState?.turns ?? 0;
  const maxTurns = playerState?.max_turns;
  const turnsTitle = (() => {
    if (typeof maxTurns !== 'number') return 'Turns';
    if (turnsNow >= maxTurns) return 'Turns — full';
    if (regenPerHr <= 0) return 'Turns';
    const hrs = (maxTurns - turnsNow) / regenPerHr;
    const h = Math.floor(hrs);
    const m = Math.round((hrs - h) * 60);
    return `Turns — ${h > 0 ? `${h}h ${m}m` : `${m}m`} to full (+${Math.round(regenPerHr)}/hr)`;
  })();
  const bounty = playerState?.bounty_total ?? 0;

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
        <span className="pvh-stat" title={turnsTitle}>
          <span className="pvh-k"><TurnsIcon /></span>
          <span className="pvh-v">
            {turnsNow.toLocaleString()}
            {typeof maxTurns === 'number' && (
              <span className="pvh-sub">/{maxTurns.toLocaleString()}</span>
            )}
            {regenPerHr > 0 && (
              <span className="pvh-regen">+{Math.round(regenPerHr)}/hr</span>
            )}
          </span>
        </span>
        <span className="pvh-stat pvh-drones" title="Attack / Defense drones (current ship)">
          <span className="pvh-k">DRONES</span>
          <span className="pvh-v">
            <span className="pvh-drone" title="Attack drones">⚔ {playerState?.attack_drones ?? 0}</span>
            <span className="pvh-drone" title="Defense drones">🛡 {playerState?.defense_drones ?? 0}</span>
          </span>
        </span>
        <span className="pvh-stat" title="Mines">
          <span className="pvh-k">MINE</span>
          <span className="pvh-v">{playerState?.mines ?? 0}</span>
        </span>
        {bounty > 0 && (
          <span className="pvh-stat pvh-bounty" title="Bounty on your head">
            <span className="pvh-k">BOUNTY</span>
            <span className="pvh-v">{formatCredits(bounty)}</span>
          </span>
        )}
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
