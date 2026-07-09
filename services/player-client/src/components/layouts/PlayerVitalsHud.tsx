import React from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { useGame } from '../../contexts/GameContext';
import { useWebSocket } from '../../contexts/WebSocketContext';
import LogoutButton from '../auth/LogoutButton';
import { formatCredits } from '../../utils/formatters';
import { TurnsIcon } from '../icons/TurnsIcon';
import { MineIcon } from '../icons/MineIcon';
import './player-vitals-hud.css';

/**
 * PlayerVitalsHud — the always-on cockpit HUD overlaying the full-width
 * windshield band (WO-INVERTED-L + WO-PLAYERINFO id=145). Three zones:
 *   • commander NAME (left) — tinted by playerState.name_color;
 *   • live vitals (center) — a SINGLE-ROW inline strip (Max: "too tall, make it
 *     wider"): ₡credits · TRN turns(/max) · ATK/DEF drones · MINE · LINK;
 *   • LOGOUT (right) — reuses the shared LogoutButton.
 *
 * LINK (WO-PUX-UPLINK-HUD) — always-on uplink-health chip, the ONE indicator
 * a pilot can't miss (previously buried in the COMMS MFD page only). Driven
 * by WebSocketContext.linkStatus; copy/thresholds NO-CANON.
 *
 * Credits use the shared ₡ glyph (formatCredits, id=148) — never 'CRED'/'cr'.
 * The bar is click-through (pointer-events:none); only logout / the
 * failure-only refresh re-enable pointer events. All sizing is rem so the HUD
 * rides the #root UI-scale zoom.
 */
const PlayerVitalsHud: React.FC = () => {
  const { user } = useAuth();
  const { playerState, isLoading, refreshPlayerState } = useGame();
  const { linkStatus } = useWebSocket();

  // id=145d — turns regen: +N/hr subscript + hover→time-to-full (id=142 fields).
  const regenPerHr = playerState?.turn_regen_per_hour ?? 0;
  const turnsNow = playerState?.turns ?? 0;
  const maxTurns = playerState?.max_turns;
  // WO-PROG-TURN-VISIBILITY: scarcity warning per canon (turns.md "Player-facing
  // affordances": "Low-turn warning UI hints when the pool is below thresholds
  // (design: <50)"). Gated on playerState existing so a loading/absent state
  // (turnsNow defaulting to 0 above) never falsely reads as "low".
  const lowTurns = !!playerState && turnsNow < 50;
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

  // id=LINK — reuses the "LINK OK" / "LINK DOWN" copy already established in
  // CommsCrewPage's UPLINK field; RELINK is new (mid-reconnect), no prior
  // canon term to match. Value text is intentionally SHORT (OK/RELINK/DOWN)
  // and width-pinned in CSS so a state change never reflows neighboring chips.
  const linkLabel = linkStatus === 'up' ? 'OK' : linkStatus === 'reconnecting' ? 'RELINK' : 'DOWN';
  const linkTitle = linkStatus === 'up'
    ? 'Uplink connected'
    : linkStatus === 'reconnecting'
      ? 'Uplink lost — reconnecting'
      : 'Uplink down';

  return (
    <div className="player-vitals-hud">
      {/* LEFT — commander identity: callsign + rank + reputation (WO-PLAYER-HEADER) */}
      <div
        className="pvh-name"
        title={user?.username}
        style={{ '--pilot-color': playerState?.name_color || '#00D9FF' } as React.CSSProperties}
      >
        <span className="pvh-callsign">{user?.username || '—'}</span>
        {playerState && (
          <span className="pvh-identity">
            {playerState.military_rank?.toUpperCase() || ''}{playerState.military_rank ? ' · ' : ''}{playerState.reputation_tier || 'Neutral'} ({((playerState.personal_reputation ?? 0) >= 0 ? '+' : '')}{playerState.personal_reputation ?? 0})
          </span>
        )}
      </div>

      <div className="pvh-vitals">
        <span className="pvh-stat pvh-credits" title="Credits">
          {formatCredits(playerState?.credits)}
        </span>
        {/* Change 1: TurnsIcon sized to match digit cap-height (0.8rem = pvh-v font-size). */}
        {/* Change 3: regen is a sub-line beneath the turn count, not inline-right. */}
        <span className={lowTurns ? 'pvh-stat pvh-turns-low' : 'pvh-stat'} title={turnsTitle}>
          <span className="pvh-k"><TurnsIcon size="0.8rem" /></span>
          <span className="pvh-v pvh-turns-stack">
            <span className="pvh-turns-count">
              {turnsNow.toLocaleString()}
              {typeof maxTurns === 'number' && (
                <span className="pvh-sub">/{maxTurns.toLocaleString()}</span>
              )}
            </span>
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
        {/* Change 6: MineIcon glyph (naval-mine sphere+spikes) replaces the MINE text label. */}
        <span className="pvh-stat" title="Mines">
          <span className="pvh-k"><MineIcon size="0.8rem" /></span>
          <span className="pvh-v">{playerState?.mines ?? 0}</span>
        </span>
        <span className={`pvh-stat pvh-link pvh-link--${linkStatus}`} title={linkTitle}>
          <span className="pvh-k">LINK</span>
          <span className="pvh-v">{linkLabel}</span>
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
