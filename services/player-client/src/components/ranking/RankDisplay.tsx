import React, { useState, useEffect } from 'react';
import { rankingAPI } from '../../services/api';
import './ranking.css';

interface RankBonuses {
  trading_discount_percent: number;
  max_turns_bonus: number;
  combat_damage_bonus_percent: number;
}

interface RankInfo {
  player_id: string;
  username: string;
  current_rank: string;
  rank_level: number;
  rank_tier: string;
  rank_points: number;
  points_to_next_rank: number;
  next_rank: string | null;
  next_rank_points_required: number | null;
  progress_percent: number;
  bonuses: RankBonuses;
  is_max_rank: boolean;
  is_game_complete?: boolean;
  rank_victory_at?: string | null;
}

/** Keys match the rank tiers the backend emits (RANK_DEFINITIONS). */
export const TIER_COLORS: Record<string, string> = {
  Enlisted: '#888888',
  NCO: '#4a9eff',
  Warrant: '#ffaa44',
  Officer: '#ff44ff',
  Flag: '#ff4444',
};

const RankDisplay: React.FC = () => {
  const [rankInfo, setRankInfo] = useState<RankInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchRank = async () => {
      try {
        setLoading(true);
        const data = await rankingAPI.getRank();
        setRankInfo(data);
        setError(null);
      } catch (err: any) {
        setError(err.message || 'Failed to load rank info');
      } finally {
        setLoading(false);
      }
    };
    fetchRank();
  }, []);

  if (loading) {
    return (
      <div className="rank-display rank-loading">
        <div className="rank-spinner" />
        <span>Loading rank...</span>
      </div>
    );
  }

  if (error || !rankInfo) {
    return (
      <div className="rank-display rank-error">
        <span>{error || 'Rank unavailable'}</span>
      </div>
    );
  }

  const tierColor = TIER_COLORS[rankInfo.rank_tier] || '#ffffff';

  return (
    <div className="rank-display">
      <div className="rank-badge" style={{ borderColor: tierColor }}>
        <span className="rank-level">{rankInfo.rank_level}</span>
      </div>
      <div className="rank-info">
        <div className="rank-name" style={{ color: tierColor }}>
          {rankInfo.current_rank}
        </div>
        <div className="rank-tier">{rankInfo.rank_tier}</div>
        <div className="rank-progress-bar">
          <div
            className="rank-progress-fill"
            style={{
              width: `${rankInfo.progress_percent}%`,
              backgroundColor: tierColor,
            }}
          />
        </div>
        {!rankInfo.is_max_rank && rankInfo.next_rank && (
          <div className="rank-next">
            {rankInfo.rank_points} / {rankInfo.next_rank_points_required} pts &rarr; {rankInfo.next_rank}
          </div>
        )}
        {rankInfo.is_max_rank && !rankInfo.is_game_complete && (
          <div className="rank-next rank-max">Maximum Rank Achieved</div>
        )}
        {rankInfo.is_max_rank && rankInfo.is_game_complete && (
          <div className="rank-victory-banner">
            <span className="rank-victory-title">★ FLEET ADMIRAL — JOURNEY COMPLETE ★</span>
            {rankInfo.rank_victory_at && (
              <span className="rank-victory-date">
                {new Date(rankInfo.rank_victory_at).toLocaleDateString(undefined, {
                  year: 'numeric',
                  month: 'short',
                  day: 'numeric',
                })}
              </span>
            )}
          </div>
        )}
      </div>
      <div className="rank-bonuses">
        {rankInfo.bonuses.trading_discount_percent > 0 && (
          <div className="bonus-item">
            <span className="bonus-icon">💰</span>
            <span className="bonus-value">-{rankInfo.bonuses.trading_discount_percent}%</span>
            <span className="bonus-label">Trade</span>
          </div>
        )}
        {rankInfo.bonuses.combat_damage_bonus_percent > 0 && (
          <div className="bonus-item">
            <span className="bonus-icon">⚔️</span>
            <span className="bonus-value">+{rankInfo.bonuses.combat_damage_bonus_percent}%</span>
            <span className="bonus-label">Damage</span>
          </div>
        )}
        {rankInfo.bonuses.max_turns_bonus > 0 && (
          <div className="bonus-item">
            <span className="bonus-icon">⏱️</span>
            <span className="bonus-value">+{rankInfo.bonuses.max_turns_bonus}</span>
            <span className="bonus-label">Turns</span>
          </div>
        )}
      </div>
    </div>
  );
};

export default RankDisplay;
