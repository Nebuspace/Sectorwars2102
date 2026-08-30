/**
 * BountyBoard — public Federation bounty board (LEG-156).
 *
 * Canon: FEATURES/gameplay/bounties.md § Browsing bounties.
 * Binds only fields returned by GET /ranking/bounties/available. Portraits and
 * recent-kill logs are NOT in the shipped response — shown as honest omitted
 * states rather than invented data. Refreshes when bountyEventSignal bumps
 * (place / cancel / collect WS frames).
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { bountyAPI } from '../../services/api';
import { useWebSocket } from '../../contexts/WebSocketContext';
import './bounty-board.css';

export interface AvailableBountyRow {
  player_id: string;
  player_name: string;
  reputation_tier?: string | null;
  total_bounty: number;
  bounty_count: number;
  current_sector?: number | string | null;
  /** Not in shipped API — if ever present, render; otherwise omit honestly. */
  portrait_url?: string | null;
  last_seen_sector?: number | string | null;
  recent_kills?: unknown;
}

export interface AvailableBountiesResponse {
  success?: boolean;
  bounties?: AvailableBountyRow[];
  total_targets?: number;
}

function sectorLabel(row: AvailableBountyRow): string | null {
  const raw = row.current_sector ?? row.last_seen_sector;
  if (raw === null || raw === undefined || raw === '') return null;
  return String(raw);
}

function hasPortrait(row: AvailableBountyRow): boolean {
  return typeof row.portrait_url === 'string' && row.portrait_url.length > 0;
}

function hasKillLog(row: AvailableBountyRow): boolean {
  return Array.isArray(row.recent_kills) && row.recent_kills.length > 0;
}

function killLogCount(row: AvailableBountyRow): number {
  return Array.isArray(row.recent_kills) ? row.recent_kills.length : 0;
}

function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

/** True when err looks like gameserver detail (not bare API Error: N / TypeError noise). */
function hasBountyBoardServerDetail(err: unknown, message: string | undefined): boolean {
  // Network collapse (fetch TypeError) is not gameserver copy — use the caller fallback.
  if (err instanceof TypeError) return false;
  return (
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim())
  );
}

/** apiRequest throws Error with `.status`; mirror admin formatAdminApiError honesty on LIST. */
export function formatBountyBoardLoadError(err: unknown): string {
  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  const hasServerDetail = hasBountyBoardServerDetail(err, message);

  if (status === 403) {
    if (hasServerDetail) return message!;
    return 'Access denied — you cannot view the Federation bounty board right now.';
  }

  if (status === 429) {
    return 'Bounty board rate limit exceeded — wait a moment and try again.';
  }

  if (hasServerDetail) return message!;
  return 'Failed to load bounty board';
}

export interface BountyBoardProps {
  /** Override fetch for tests; defaults to bountyAPI.getAvailable. */
  fetchAvailable?: (limit?: number) => Promise<AvailableBountiesResponse>;
  limit?: number;
  /**
   * Optional signal override for unit tests. Production reads
   * useWebSocket().bountyEventSignal when omitted.
   */
  bountyEventSignal?: number;
}

const BountyBoard: React.FC<BountyBoardProps> = ({
  fetchAvailable,
  limit = 20,
  bountyEventSignal: bountyEventSignalProp,
}) => {
  const ws = useWebSocket();
  const bountyEventSignal =
    typeof bountyEventSignalProp === 'number' ? bountyEventSignalProp : ws.bountyEventSignal;
  const [rows, setRows] = useState<AvailableBountyRow[]>([]);
  const [totalTargets, setTotalTargets] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const requestId = useRef(0);
  const signalRef = useRef(bountyEventSignal);

  const load = useCallback(async () => {
    const id = ++requestId.current;
    setLoading(true);
    setError(null);
    try {
      const getter = fetchAvailable ?? bountyAPI.getAvailable;
      const result = await getter(limit);
      if (id !== requestId.current) return;
      const list = Array.isArray(result?.bounties) ? result.bounties : [];
      setRows(list);
      setTotalTargets(
        typeof result?.total_targets === 'number' ? result.total_targets : list.length,
      );
    } catch (err) {
      if (id !== requestId.current) return;
      setError(formatBountyBoardLoadError(err));
      setRows([]);
      setTotalTargets(null);
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, [fetchAvailable, limit]);

  useEffect(() => {
    void load();
  }, [load]);

  // Realtime refresh — skip the initial mount (signal 0 / unchanged).
  useEffect(() => {
    if (bountyEventSignal === signalRef.current) return;
    signalRef.current = bountyEventSignal;
    void load();
  }, [bountyEventSignal, load]);

  const anyPortrait = rows.some(hasPortrait);
  const anyKillLog = rows.some(hasKillLog);

  return (
    <div
      className="bounty-board"
      data-testid="bounty-board"
      role="region"
      aria-label="Federation bounty board"
    >
      <div className="bounty-board-header">
        <h3 className="bounty-board-title">Federation Bounty Board</h3>
        {totalTargets != null && !loading && !error && (
          <span className="bounty-board-total" data-testid="bounty-board-total">
            {totalTargets} target{totalTargets === 1 ? '' : 's'}
          </span>
        )}
      </div>

      {loading && (
        <div className="bounty-board-body bounty-board-loading" data-testid="bounty-board-loading">
          <div className="rank-spinner" aria-hidden="true" />
          <span>Scanning bounty board…</span>
        </div>
      )}

      {error && !loading && (
        <div className="bounty-board-body bounty-board-error" role="alert" data-testid="bounty-board-error">
          {error}
        </div>
      )}

      {!loading && !error && rows.length === 0 && (
        <div className="bounty-board-body bounty-board-empty" data-testid="bounty-board-empty">
          No active bounties on the board.
        </div>
      )}

      {!loading && !error && rows.length > 0 && (
        <table className="bounty-board-table" data-testid="bounty-board-table">
          <thead>
            <tr>
              <th scope="col">Target</th>
              <th scope="col">Reputation</th>
              <th scope="col">Bounty</th>
              <th scope="col">Offers</th>
              <th scope="col">Sector</th>
              <th scope="col">Portrait</th>
              <th scope="col">Recent kills</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const sector = sectorLabel(row);
              return (
                <tr key={row.player_id} data-testid="bounty-board-row">
                  <td className="bb-name">{row.player_name || 'Unknown'}</td>
                  <td className="bb-tier">{row.reputation_tier || '—'}</td>
                  <td className="bb-total">
                    {typeof row.total_bounty === 'number'
                      ? row.total_bounty.toLocaleString()
                      : '—'}
                  </td>
                  <td className="bb-count">{row.bounty_count ?? '—'}</td>
                  <td className="bb-sector">
                    {sector != null ? (
                      <span data-testid="bounty-board-sector">{sector}</span>
                    ) : (
                      <span className="bb-omitted" data-testid="bounty-board-sector-omitted">
                        Unavailable
                      </span>
                    )}
                  </td>
                  <td className="bb-portrait">
                    {hasPortrait(row) ? (
                      <img
                        src={row.portrait_url as string}
                        alt=""
                        className="bb-portrait-img"
                        data-testid="bounty-board-portrait"
                      />
                    ) : (
                      <span className="bb-omitted" data-testid="bounty-board-portrait-omitted">
                        Not provided
                      </span>
                    )}
                  </td>
                  <td className="bb-kills">
                    {hasKillLog(row) ? (
                      <span data-testid="bounty-board-kills">{String(killLogCount(row))} logged</span>
                    ) : (
                      <span className="bb-omitted" data-testid="bounty-board-kills-omitted">
                        Not provided
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {!loading && !error && (
        <p className="bounty-board-footnote" data-testid="bounty-board-optional-residual">
          {!anyPortrait && !anyKillLog
            ? 'Portraits and recent-kill logs are not in the available-bounties API response — omitted until the server ships them.'
            : !anyPortrait
              ? 'Portraits are not in the available-bounties API response for these targets.'
              : !anyKillLog
                ? 'Recent-kill logs are not in the available-bounties API response for these targets.'
                : null}
        </p>
      )}
    </div>
  );
};

export default BountyBoard;
