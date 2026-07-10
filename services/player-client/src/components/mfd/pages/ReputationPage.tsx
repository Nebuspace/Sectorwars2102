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
              <div className="mfd-page-warnline">{loadError}</div>
            ) : rows === null ? (
              <MFDEmpty text="LOADING…" />
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
