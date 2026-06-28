import React, { useState, useEffect } from 'react';
import { rankingAPI } from '../../services/api';
import './ranking.css';

interface LeaderboardEntry {
  position: number;
  player_id: string;
  nickname: string;
  military_rank: string;
  score: number;
}

interface LeaderboardData {
  category: string;
  entries: LeaderboardEntry[];
  player_position: number | null;
  total_players: number;
}

type Category = 'rank_points' | 'combat' | 'trading' | 'exploration';

const CATEGORY_LABELS: Record<Category, { label: string; icon: string; scoreLabel: string }> = {
  rank_points: { label: 'Rank Points', icon: '\u2B50', scoreLabel: 'Points' },
  combat:      { label: 'Combat',      icon: '\u2694\uFE0F', scoreLabel: 'Victories' },
  trading:     { label: 'Trading',     icon: '\uD83D\uDCB0', scoreLabel: 'Volume' },
  exploration: { label: 'ARIA Activity', icon: '\uD83C\uDF0C', scoreLabel: 'Activity' },
};

const CATEGORIES: Category[] = ['rank_points', 'combat', 'trading', 'exploration'];

interface LeaderboardProps {
  category?: Category;
  /** The viewing player's id (Player.id), used to highlight their row. */
  playerId?: string | null;
}

const Leaderboard: React.FC<LeaderboardProps> = ({
  category: initialCategory = 'rank_points',
  playerId,
}) => {
  const [activeCategory, setActiveCategory] = useState<Category>(initialCategory);
  const [data, setData] = useState<LeaderboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const currentPlayerId = playerId ?? localStorage.getItem('playerId');

  useEffect(() => {
    // Stale-response guard: rapid tab switches can resolve out of order,
    // so drop any response that lands after this effect is cleaned up or
    // that echoes a category other than the active one.
    let cancelled = false;

    const fetchLeaderboard = async () => {
      try {
        setLoading(true);
        setError(null);
        const result: LeaderboardData = await rankingAPI.getPublicLeaderboard(activeCategory, 20);
        if (cancelled || result.category !== activeCategory) return;
        setData(result);
        setLoading(false);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : 'Failed to load leaderboard');
        setLoading(false);
      }
    };

    fetchLeaderboard();
    return () => {
      cancelled = true;
    };
  }, [activeCategory]);

  const handleCategoryChange = (cat: Category) => {
    setActiveCategory(cat);
  };

  const meta = CATEGORY_LABELS[activeCategory];

  return (
    <div className="leaderboard">
      <div className="leaderboard-header">
        <h3>Leaderboard</h3>
        {data && <span className="leaderboard-total">{data.total_players} players</span>}
      </div>

      <div className="medal-categories">
        {CATEGORIES.map((cat) => (
          <button
            key={cat}
            className={`medal-cat-btn ${activeCategory === cat ? 'active' : ''}`}
            onClick={() => handleCategoryChange(cat)}
          >
            {CATEGORY_LABELS[cat].icon} {CATEGORY_LABELS[cat].label}
          </button>
        ))}
      </div>

      {loading && (
        <div className="leaderboard-body leaderboard-loading">
          <div className="rank-spinner" />
          <span>Loading leaderboard...</span>
        </div>
      )}

      {error && (
        <div className="leaderboard-body leaderboard-error">
          <span>{error}</span>
        </div>
      )}

      {!loading && !error && data && (
        <>
          <table className="leaderboard-table">
            <thead>
              <tr>
                <th className="col-pos">#</th>
                <th className="col-name">Player</th>
                <th className="col-rank">Rank</th>
                <th className="col-score">{meta.scoreLabel}</th>
              </tr>
            </thead>
            <tbody>
              {data.entries.map((entry) => (
                <tr
                  key={entry.player_id}
                  className={entry.player_id === currentPlayerId ? 'current-player' : ''}
                >
                  <td className="col-pos">{entry.position}</td>
                  <td className="col-name">{entry.nickname}</td>
                  <td className="col-rank">{entry.military_rank}</td>
                  <td className="col-score">{entry.score.toLocaleString()}</td>
                </tr>
              ))}
              {data.entries.length === 0 && (
                <tr>
                  <td colSpan={4} className="leaderboard-empty">No entries yet</td>
                </tr>
              )}
            </tbody>
          </table>

          {data.player_position != null && !data.entries.some((e) => e.position === data.player_position) && (
            <div className="leaderboard-your-rank">
              Your position: <strong>#{data.player_position}</strong> of {data.total_players}
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default Leaderboard;
