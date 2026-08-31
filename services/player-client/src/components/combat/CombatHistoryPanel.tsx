/**
 * CombatHistoryPanel — paginated browse of the player's own combat logs
 * (LEG-372). Consumes GET /api/v1/combat/history (LEG-304). Server scopes
 * to the authenticated player; this UI never invents cross-player queries.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  combatAPI,
  type CombatHistoryItem,
  type CombatHistoryOpponent,
  type CombatHistoryResponse,
} from '../../services/api';
import PlayerNamePlate from '../common/PlayerNamePlate';
import './combat-interface.css';

const DEFAULT_LIMIT = 20;

const COMBAT_HISTORY_LOAD_FALLBACK = 'Failed to load combat history';

/** Exported for TypeError/network honesty Vitest (LEG-3163). */
export function formatCombatHistoryError(err: unknown, fallback: string): string {
  if (err instanceof TypeError) return fallback;
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

function opponentLabel(opponent?: CombatHistoryOpponent): string {
  if (!opponent) return '—';
  return opponent.name || opponent.displayName || '—';
}

function counterpartLabel(item: CombatHistoryItem): string {
  if (item.opponent) return opponentLabel(item.opponent);
  if (item.target?.name) return item.target.name;
  if (item.target?.type) return item.target.type;
  return '—';
}

export const CombatHistoryPanel: React.FC = () => {
  const [limit] = useState(DEFAULT_LIMIT);
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<CombatHistoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (nextOffset: number) => {
      setLoading(true);
      setError(null);
      try {
        const res = await combatAPI.getHistory({ limit, offset: nextOffset });
        setData(res);
        setOffset(nextOffset);
      } catch (e) {
        setData(null);
        setError(formatCombatHistoryError(e, COMBAT_HISTORY_LOAD_FALLBACK));
      } finally {
        setLoading(false);
      }
    },
    [limit],
  );

  useEffect(() => {
    void load(0);
  }, [load]);

  const total = data?.total ?? 0;
  const items = data?.items ?? [];
  const canPrev = offset > 0 && !loading;
  const canNext = offset + limit < total && !loading;

  return (
    <section className="combat-history-panel" data-testid="combat-history-panel">
      <div className="combat-history-header">
        <h3>Combat History</h3>
        {data && (
          <span className="combat-history-meta" data-testid="combat-history-meta">
            {total === 0 ? '0 logged' : `${offset + 1}–${Math.min(offset + items.length, total)} of ${total}`}
          </span>
        )}
      </div>

      {loading && !data && (
        <p className="combat-history-loading" data-testid="combat-history-loading">
          Loading history…
        </p>
      )}

      {error && (
        <p className="combat-history-error" data-testid="combat-history-error" role="alert">
          {error}
        </p>
      )}

      {!loading && !error && items.length === 0 && (
        <p className="combat-history-empty" data-testid="combat-history-empty">
          No combat history yet.
        </p>
      )}

      {items.length > 0 && (
        <ul className="combat-history-list" data-testid="combat-history-list">
          {items.map((item) => (
            <li key={item.id} className="combat-history-row" data-testid="combat-history-row">
              <span className="ch-time">
                {item.timestamp ? new Date(item.timestamp).toLocaleString() : '—'}
              </span>
              <span className="ch-type">{item.combat_type}</span>
              <span className="ch-role">{item.role}</span>
              <span className="ch-result">{item.result ?? '—'}</span>
              <span className="ch-foe">
                {item.opponent ? (
                  <PlayerNamePlate
                    name={opponentLabel(item.opponent)}
                    size="sm"
                    pinnedMedalId={item.opponent.pinned_medal_id}
                    medalCount={item.opponent.medal_count}
                  />
                ) : (
                  counterpartLabel(item)
                )}
              </span>
              <span className="ch-sector">
                {item.sector_id != null ? `Sec ${item.sector_id}` : '—'}
              </span>
            </li>
          ))}
        </ul>
      )}

      <div className="combat-history-pager">
        <button
          type="button"
          className="cockpit-btn secondary"
          data-testid="combat-history-prev"
          disabled={!canPrev}
          onClick={() => void load(Math.max(0, offset - limit))}
        >
          Prev
        </button>
        <button
          type="button"
          className="cockpit-btn secondary"
          data-testid="combat-history-next"
          disabled={!canNext}
          onClick={() => void load(offset + limit)}
        >
          Next
        </button>
      </div>
    </section>
  );
};

export default CombatHistoryPanel;
