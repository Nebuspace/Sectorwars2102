import React, { useEffect, useState } from 'react';
import { api } from '../../utils/auth';
import { formatAdminApiError } from '../../utils/adminApiError';
import './ranking-leaderboard-panel.css';

/** Keys match rank tiers the backend emits (RANK_DEFINITIONS). Copied from player-client RankDisplay.tsx. */
export const TIER_COLORS: Record<string, string> = {
  Enlisted: '#888888',
  NCO: '#4a9eff',
  Warrant: '#ffaa44',
  Officer: '#ff44ff',
  Flag: '#ff4444',
};

export type AdminLeaderboardEntry = {
  position: number;
  player_id: string;
  username: string;
  military_rank: string;
  rank_points: number;
  rank_level: number;
  rank_tier?: string;
  is_game_complete?: boolean;
  is_suspect?: boolean;
  is_wanted?: boolean;
};

function rankTierColor(tier: string | undefined): string {
  if (!tier) return '#888888';
  return TIER_COLORS[tier] ?? '#ffffff';
}

type LeaderboardResponse = {
  entries: AdminLeaderboardEntry[];
  total_players: number;
};

/**
 * Admin military ranking leaderboard — WO-WIRE-ADMIN-RANKING-LEADERBOARD.
 * Calls GET /api/v1/ranking/leaderboard (PLAYERS_VIEW scope).
 */
const RankingLeaderboardPanel: React.FC = () => {
  const [entries, setEntries] = useState<AdminLeaderboardEntry[]>([]);
  const [totalPlayers, setTotalPlayers] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setLoading(true);
        setError(null);
        const { data } = await api.get<LeaderboardResponse>('/api/v1/ranking/leaderboard', {
          params: { limit: 20 },
        });
        if (cancelled) return;
        setEntries(Array.isArray(data?.entries) ? data.entries : []);
        setTotalPlayers(typeof data?.total_players === 'number' ? data.total_players : null);
      } catch (err: unknown) {
        if (cancelled) return;
        setError(
          formatAdminApiError(err, {
            fallback: 'Failed to load leaderboard',
            scopeHint: 'admin.players.view scope (PLAYERS_VIEW) required for ranking leaderboard',
          }),
        );
        setEntries([]);
        setTotalPlayers(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section className="section ranking-leaderboard-panel" data-testid="admin-ranking-leaderboard">
      <div className="card">
        <div className="card-body">
          <div className="ranking-leaderboard-header">
            <h3>Military ranking leaderboard</h3>
            {totalPlayers != null && (
              <span className="ranking-leaderboard-total" data-testid="admin-ranking-total">
                {totalPlayers} active players
              </span>
            )}
          </div>
          {loading && <p className="ranking-leaderboard-status">Loading…</p>}
          {!loading && error && (
            <p className="ranking-leaderboard-status ranking-leaderboard-error" role="alert">
              {error}
            </p>
          )}
          {!loading && !error && entries.length === 0 && (
            <p className="ranking-leaderboard-status">No ranking entries yet.</p>
          )}
          {!loading && !error && entries.length > 0 && (
            <table className="ranking-leaderboard-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Player</th>
                  <th>Rank</th>
                  <th>Points</th>
                  <th>Flags</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((e) => (
                  <tr key={e.player_id} data-testid={`admin-ranking-row-${e.player_id}`}>
                    <td>{e.position}</td>
                    <td>{e.username}</td>
                    <td className="ranking-leaderboard-rank-cell">
                      <span
                        className="admin-rank-badge"
                        data-testid={`admin-rank-badge-${e.player_id}`}
                        style={{ borderColor: rankTierColor(e.rank_tier) }}
                      >
                        <span className="admin-rank-level">{e.rank_level}</span>
                      </span>
                      <span
                        className="admin-rank-name"
                        style={{ color: e.rank_tier ? rankTierColor(e.rank_tier) : undefined }}
                      >
                        {e.military_rank}
                      </span>
                    </td>
                    <td>{e.rank_points}</td>
                    <td>
                      {[
                        e.is_wanted ? 'wanted' : null,
                        e.is_suspect ? 'suspect' : null,
                        e.is_game_complete ? 'complete' : null,
                      ]
                        .filter(Boolean)
                        .join(', ') || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </section>
  );
};

export default RankingLeaderboardPanel;
