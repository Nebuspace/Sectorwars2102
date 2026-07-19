/**
 * REPUTATION — MFD ops page (NEON15 B3, upgraded WO-UIPC-REP-MFD-FACTION).
 *
 * Personal standing straight off GameContext.playerState (unchanged from
 * v1) plus a per-faction standings list from the already-shipped
 * factionAPI.getReputation() (GET /api/v1/factions/reputation —
 * ReputationResponse[], one row per faction: faction_id, faction_name,
 * current_value, current_level, title, trade_modifier, port_access_level,
 * combat_response). The list patches LIVE from two WebSocket frames
 * (WebSocketContext.reputationEventSignal / lastReputationChanged /
 * lastTeamReputationChanged), no refetch:
 *   - reputation_changed      — the player's OWN standing with a faction
 *     ticked a tier; patches current_value/current_level/title in place.
 *   - team_reputation_changed — the player's TEAM's aggregated standing
 *     with a faction ticked a tier (factions-and-teams.md average/lowest/
 *     leader methods) — a genuinely DIFFERENT number from the player's own,
 *     so it is kept as a separate teamValue/teamLevel badge rather than
 *     overwriting the personal reading.
 * Both frames fire only on a tier boundary crossing (never on every point
 * delta) and already carry the new standing.
 */

import React from 'react';
import { useGame } from '../../../contexts/GameContext';
import { useWebSocket } from '../../../contexts/WebSocketContext';
import { factionAPI } from '../../../services/api';
import { MFDPageHeader, MFDPageBody, MFDField, MFDEmpty, MFDInsufficient } from '../atoms';
import './pages-ops.css';

const ACCENT = '#FFD700';

// Reputation is clamped server-side to [-800, +800]
// (faction_service.py: `max(-800, min(800, ...))`) — the bar's fill % is
// this range normalized to 0-100, never the raw value.
const REP_MIN = -800;
const REP_MAX = 800;

// Color-graded per current_level, mirroring RankDisplay.tsx's TIER_COLORS
// pattern (a Record<level, color> keyed off the backend's own level string,
// fed straight into a `backgroundColor` style — same shape, different
// palette). Keys are the ReputationLevel enum values
// (models/reputation.py) in ascending standing order; a red->gray->green
// ramp so the bar reads "hostile" to "trusted" at a glance.
const LEVEL_COLORS: Record<string, string> = {
  PUBLIC_ENEMY: '#ff1a1a',
  CRIMINAL: '#ff3b3b',
  OUTLAW: '#ff5c33',
  PIRATE: '#ff7a33',
  SMUGGLER: '#ff9d3d',
  UNTRUSTWORTHY: '#ffbf47',
  SUSPICIOUS: '#e0c04d',
  QUESTIONABLE: '#b8b8b8',
  NEUTRAL: '#888888',
  RECOGNIZED: '#8fcf8f',
  ACKNOWLEDGED: '#6fd28f',
  TRUSTED: '#4dd68f',
  RESPECTED: '#2fd98f',
  VALUED: '#1fe094',
  HONORED: '#0fe89e',
  REVERED: '#00f0a8',
  EXALTED: '#00ff9d',
};

const repBarColor = (level: string): string => LEVEL_COLORS[level] || '#888888';

const repBarPercent = (value: number): number => {
  const clamped = Math.max(REP_MIN, Math.min(REP_MAX, value));
  return ((clamped - REP_MIN) / (REP_MAX - REP_MIN)) * 100;
};

interface FactionReputationRow {
  faction_id: string;
  faction_name: string;
  faction_type: string;
  current_value: number;
  current_level: string;
  title: string;
  trade_modifier: number;
  port_access_level: number;
  combat_response: string;
  // Patched in from a team_reputation_changed frame — absent until one
  // arrives for this faction.
  teamValue?: number;
  teamLevel?: string;
}

const formatLevel = (level: string): string => level.replace(/_/g, ' ');

const formatSigned = (value: number): string => (value > 0 ? `+${value}` : `${value}`);

const ReputationPage: React.FC = () => {
  const { playerState } = useGame();
  const { reputationEventSignal, lastReputationChanged, lastTeamReputationChanged } = useWebSocket();

  const [rows, setRows] = React.useState<FactionReputationRow[] | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    factionAPI
      .getReputation()
      .then((data: any) => {
        if (cancelled) return;
        setRows(Array.isArray(data) ? data : []);
        setLoadError(null);
      })
      .catch((e: any) => {
        if (cancelled) return;
        setRows(null);
        setLoadError(e?.message || 'Failed to load faction standings');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Live personal-standing patch — reputation_changed already carries the
  // new value/level/title, so the matching row updates without a refetch.
  React.useEffect(() => {
    if (!lastReputationChanged) return;
    setRows((prev) => {
      if (!prev) return prev;
      const idx = prev.findIndex((r) => r.faction_id === lastReputationChanged.faction_id);
      if (idx === -1) return prev;
      const next = [...prev];
      next[idx] = {
        ...next[idx],
        current_value: lastReputationChanged.new_value,
        current_level: lastReputationChanged.new_level,
        title: lastReputationChanged.title,
      };
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reputationEventSignal, lastReputationChanged]);

  // Live team-standing patch — kept in its own teamValue/teamLevel fields,
  // never merged into current_value (see file header).
  React.useEffect(() => {
    if (!lastTeamReputationChanged) return;
    setRows((prev) => {
      if (!prev) return prev;
      const idx = prev.findIndex((r) => r.faction_id === lastTeamReputationChanged.faction_id);
      if (idx === -1) return prev;
      const next = [...prev];
      next[idx] = {
        ...next[idx],
        teamValue: lastTeamReputationChanged.new_value,
        teamLevel: lastTeamReputationChanged.new_level,
      };
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reputationEventSignal, lastTeamReputationChanged]);

  return (
    <div className="mfd-page-ops">
      <MFDPageHeader title="REPUTATION" accent={ACCENT} status="shipped" />
      <MFDPageBody scrollKey="reputation">
        {!playerState ? (
          <MFDInsufficient />
        ) : (
          <>
            <MFDField label="STANDING" value={playerState.personal_reputation ?? '—'} accent />
            <MFDField label="TIER" value={playerState.reputation_tier || '—'} />
            <MFDField label="RANK" value={playerState.military_rank || '—'} />

            <div className="mfd-page-section-label">FACTION STANDINGS</div>
            {loadError ? (
              <div className="mfd-page-warnline" role="alert">{loadError}</div>
            ) : rows === null ? (
              // Pixel a11y fix: MFDEmpty itself carries no a11y (shared
              // atoms.tsx, not edited here -- used elsewhere) -- wrap it
              // locally so the loading state is announced.
              <div role="status" aria-live="polite">
                <MFDEmpty text="LOADING…" />
              </div>
            ) : rows.length === 0 ? (
              <MFDEmpty text="NO FACTION DATA" />
            ) : (
              <ul className="mfd-page-faction-list">
                {rows.map((row) => (
                  <li key={row.faction_id} className="mfd-page-faction-row">
                    <span className="mfd-page-faction-name">{row.faction_name}</span>
                    <span className="mfd-page-faction-level">{formatLevel(row.current_level)}</span>
                    <span
                      className={`mfd-page-faction-value${row.current_value < 0 ? ' negative' : ''}`}
                    >
                      {formatSigned(row.current_value)}
                    </span>
                    {typeof row.teamValue === 'number' && (
                      <span className="mfd-page-faction-team">
                        TEAM {formatSigned(row.teamValue)} ({formatLevel(row.teamLevel || '')})
                      </span>
                    )}
                    <div
                      className="mfd-page-faction-bar"
                      role="img"
                      aria-label={`${row.faction_name} standing: ${formatSigned(row.current_value)} of ${REP_MIN} to ${REP_MAX}`}
                    >
                      <div
                        className="mfd-page-faction-bar-fill"
                        style={{
                          width: `${repBarPercent(row.current_value)}%`,
                          backgroundColor: repBarColor(row.current_level),
                        }}
                      />
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </MFDPageBody>
    </div>
  );
};

export default React.memo(ReputationPage);
