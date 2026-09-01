import React, { useCallback, useEffect, useState } from 'react';
import { teamAPI } from '../../services/api';
import websocketService from '../../services/websocket';
import type { TeamWar, WarEntryApiResponse } from '../../types/team';
import './team-war-panel.css';

/**
 * TeamWarPanel — declare / list / ceasefire surface for team wars
 * (factions-and-teams.md § War system; LEG-73).
 *
 * Backend: POST/GET/POST …/wars/* already shipped. No team-registry list
 * endpoint exists — declare takes a target team UUID (same constraint as
 * TeamManager's disabled Browse Teams). Victory threshold and payout are
 * server-owned; UI only mirrors returned state.
 *
 * Realtime: subscribes to team_war_victory when the WS client is available;
 * always exposes a Refresh control (honest residual if the push is missed).
 */

export interface TeamWarPanelProps {
  teamId: string;
  /** True when the local player is this team's leader (declare + ceasefire). */
  isLeader: boolean;
  /** Optional compact styling for StatusBar dossier. */
  compact?: boolean;
}

const mapWar = (raw: WarEntryApiResponse): TeamWar => ({
  targetTeamId: raw.target_team_id,
  declaredBy: raw.declared_by,
  declaredAt: raw.declared_at,
  reason: raw.reason ?? '',
  status: raw.status,
  score: {
    us: typeof raw.score?.us === 'number' ? raw.score.us : 0,
    them: typeof raw.score?.them === 'number' ? raw.score.them : 0,
  },
  ceasedAt: raw.ceased_at,
  ceasedBy: raw.ceased_by,
  ceaseReason: raw.cease_reason,
  winnerTeamId: raw.winner_team_id,
  loserTeamId: raw.loser_team_id,
  victoryAt: raw.victory_at,
});

const shortId = (id: string): string =>
  id.length > 12 ? `${id.slice(0, 8)}…` : id;

function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

/** Transport collapse copy is not gameserver detail (network-collapse densify). */
const isTeamWarNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed) ||
    /^networkerror$/i.test(trimmed)
  );
};

function serverDetail(err: unknown): string | undefined {
  // Network collapse (fetch TypeError) is not gameserver copy.
  if (err instanceof TypeError) return undefined;

  if (err && typeof err === 'object') {
    const rawDetail = (err as { response?: { data?: { detail?: unknown } } }).response?.data
      ?.detail;
    if (typeof rawDetail === 'string' && rawDetail.trim()) return rawDetail.trim();
  }
  const message = err instanceof Error ? err.message : undefined;
  if (typeof message === 'string' && isTeamWarNetworkCollapseMessage(message)) return undefined;
  if (
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim())
  ) {
    return message.trim();
  }
  return undefined;
}

/** apiRequest throws Error with `.status`; teams.py surfaces 404 detail on war list. */
export function formatTeamWarLoadError(err: unknown): string {
  const status = httpStatus(err);
  const detail = serverDetail(err);

  if (status === 404) {
    if (detail) return detail;
    return 'Failed to load wars';
  }

  if (detail) return detail;
  return 'Failed to load wars';
}

/** Declare/ceasefire refusals — surface gameserver detail (403/404/400). */
export function formatTeamWarActionError(err: unknown): string {
  const detail = serverDetail(err);
  if (detail) return detail;
  return 'Action failed';
}

export const TeamWarPanel: React.FC<TeamWarPanelProps> = ({
  teamId,
  isLeader,
  compact = false,
}) => {
  const [wars, setWars] = useState<TeamWar[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | 'active' | 'ceased'>('all');

  const [targetTeamId, setTargetTeamId] = useState('');
  const [reason, setReason] = useState('');
  const [declaring, setDeclaring] = useState(false);
  const [confirmingDeclare, setConfirmingDeclare] = useState(false);
  const [confirmingCeaseId, setConfirmingCeaseId] = useState<string | null>(null);

  const loadWars = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const status = filter === 'all' ? undefined : filter;
      const raw = (await teamAPI.listWars(teamId, status)) as WarEntryApiResponse[];
      setWars(Array.isArray(raw) ? raw.map(mapWar) : []);
    } catch (err) {
      setError(formatTeamWarLoadError(err));
      setWars([]);
    } finally {
      setLoading(false);
    }
  }, [teamId, filter]);

  useEffect(() => {
    void loadWars();
  }, [loadWars]);

  useEffect(() => {
    const unsub = websocketService.onTeamWarVictory(() => {
      void loadWars();
    });
    return unsub;
  }, [loadWars]);

  const handleDeclare = async () => {
    if (!isLeader) return;
    const target = targetTeamId.trim();
    if (!target) {
      setActionError('Target team ID is required.');
      return;
    }
    if (!confirmingDeclare) {
      setConfirmingDeclare(true);
      return;
    }
    setConfirmingDeclare(false);
    try {
      setDeclaring(true);
      setActionError(null);
      await teamAPI.declareWar(teamId, target, reason.trim());
      setTargetTeamId('');
      setReason('');
      await loadWars();
    } catch (err) {
      setActionError(formatTeamWarActionError(err));
    } finally {
      setDeclaring(false);
    }
  };

  const handleCeasefire = async (targetId: string) => {
    if (!isLeader) return;
    if (confirmingCeaseId !== targetId) {
      setConfirmingCeaseId(targetId);
      return;
    }
    setConfirmingCeaseId(null);
    try {
      setActionError(null);
      await teamAPI.ceasefire(teamId, targetId);
      await loadWars();
    } catch (err) {
      setActionError(formatTeamWarActionError(err));
    }
  };

  const statusLabel = (war: TeamWar): string => {
    if (war.status === 'active') return 'Active';
    if (war.ceaseReason === 'victory') return 'Ceased — victory';
    return 'Ceased';
  };

  const outcomeLine = (war: TeamWar): string | null => {
    if (war.status !== 'ceased') return null;
    if (war.winnerTeamId && war.loserTeamId) {
      const weWon = war.winnerTeamId === teamId;
      return weWon
        ? `Victory (winner ${shortId(war.winnerTeamId)})`
        : `Defeat (winner ${shortId(war.winnerTeamId)})`;
    }
    if (war.ceaseReason === 'victory') return 'Resolved by victory';
    return 'Ended by ceasefire';
  };

  return (
    <div
      className={`team-war-panel${compact ? ' team-war-panel--compact' : ''}`}
      data-testid="team-war-panel"
    >
      <div className="team-war-header">
        <h3 className="team-war-title">Wars</h3>
        <div className="team-war-toolbar">
          <label className="team-war-filter" htmlFor={`team-war-filter-${teamId}`}>
            <span className="sr-only">Filter wars</span>
            <select
              id={`team-war-filter-${teamId}`}
              data-testid="team-war-filter"
              value={filter}
              onChange={(e) => setFilter(e.target.value as typeof filter)}
            >
              <option value="all">All</option>
              <option value="active">Active</option>
              <option value="ceased">Ceased</option>
            </select>
          </label>
          <button
            type="button"
            className="team-war-refresh"
            data-testid="team-war-refresh"
            onClick={() => {
              void loadWars();
            }}
          >
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="team-war-error" role="alert" data-testid="team-war-load-error">
          {error}
        </div>
      )}
      {actionError && (
        <div className="team-war-error" role="alert" data-testid="team-war-action-error">
          {actionError}
        </div>
      )}

      {loading ? (
        <div className="team-war-loading" role="status" aria-live="polite">
          Loading wars…
        </div>
      ) : wars.length === 0 ? (
        <p className="team-war-empty" data-testid="team-war-empty">
          No wars recorded for this filter.
        </p>
      ) : (
        <ul className="team-war-list" data-testid="team-war-list">
          {wars.map((war) => {
            const outcome = outcomeLine(war);
            return (
              <li
                key={`${war.targetTeamId}-${war.declaredAt}-${war.status}`}
                className={`team-war-row team-war-row--${war.status}`}
                data-testid="team-war-row"
                data-status={war.status}
              >
                <div className="team-war-row-main">
                  <span className="team-war-target">
                    vs {shortId(war.targetTeamId)}
                  </span>
                  <span className="team-war-status" data-testid="team-war-status">
                    {statusLabel(war)}
                  </span>
                  <span className="team-war-score" data-testid="team-war-score">
                    Score {war.score.us}–{war.score.them}
                  </span>
                </div>
                {war.reason ? (
                  <p className="team-war-reason">{war.reason}</p>
                ) : null}
                {outcome ? (
                  <p className="team-war-outcome" data-testid="team-war-outcome">
                    {outcome}
                  </p>
                ) : null}
                {isLeader && war.status === 'active' ? (
                  <button
                    type="button"
                    className="team-war-cease"
                    data-testid="team-war-cease"
                    onClick={() => {
                      void handleCeasefire(war.targetTeamId);
                    }}
                  >
                    {confirmingCeaseId === war.targetTeamId
                      ? 'Confirm ceasefire'
                      : 'Ceasefire'}
                  </button>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}

      {isLeader ? (
        <div className="team-war-declare" data-testid="team-war-declare">
          <h4>Declare war</h4>
          <p className="team-war-declare-hint">
            Enter the opposing team&apos;s ID. Team registry browse is offline —
            IDs must be known out-of-band.
          </p>
          <div className="team-war-declare-fields">
            <label htmlFor={`team-war-target-${teamId}`}>
              Target team ID
              <input
                id={`team-war-target-${teamId}`}
                data-testid="team-war-target-input"
                type="text"
                value={targetTeamId}
                onChange={(e) => {
                  setConfirmingDeclare(false);
                  setTargetTeamId(e.target.value);
                }}
                placeholder="uuid"
                autoComplete="off"
              />
            </label>
            <label htmlFor={`team-war-reason-${teamId}`}>
              Reason (optional)
              <input
                id={`team-war-reason-${teamId}`}
                data-testid="team-war-reason-input"
                type="text"
                maxLength={500}
                value={reason}
                onChange={(e) => {
                  setConfirmingDeclare(false);
                  setReason(e.target.value);
                }}
              />
            </label>
          </div>
          <button
            type="button"
            className="team-war-declare-btn"
            data-testid="team-war-declare-btn"
            disabled={declaring}
            onClick={() => {
              void handleDeclare();
            }}
          >
            {declaring
              ? 'Declaring…'
              : confirmingDeclare
                ? 'Confirm declare war'
                : 'Declare war'}
          </button>
        </div>
      ) : (
        <p className="team-war-leader-only" data-testid="team-war-leader-only">
          Only the team leader can declare war or request a ceasefire.
        </p>
      )}
    </div>
  );
};

export default TeamWarPanel;
