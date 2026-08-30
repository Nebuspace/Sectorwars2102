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
function hasRankDisplayServerDetail(err: unknown, message: string | undefined): boolean {
  // Network collapse (fetch TypeError) is not gameserver copy — use the caller fallback.
  if (err instanceof TypeError) return false;
  return (
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim())
  );
}

/** apiRequest throws Error with `.status`; surface actionable server detail on load failure. */
export function formatRankDisplayLoadError(err: unknown): string {
  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  const hasServerDetail = hasRankDisplayServerDetail(err, message);

  if (status === 403) {
    if (hasServerDetail) return message!;
    return 'Access denied — you cannot view rank information right now.';
  }

  if (status === 429) {
    return 'Rank lookup rate limit exceeded — wait a moment and try again.';
  }

  if (hasServerDetail) return message!;
  return 'Failed to load rank info';
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
      } catch (err) {
        setError(formatRankDisplayLoadError(err));
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
  // Defensive: RankInfo's shape is enforced by the TS type, not at runtime --
  // a malformed/incomplete 200 (e.g. a brand-new player row the backend
  // hasn't backfilled bonuses for yet) must not crash the panel.
  const bonuses = rankInfo.bonuses ?? {
    trading_discount_percent: 0,
    max_turns_bonus: 0,
    combat_damage_bonus_percent: 0,
  };

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
        {bonuses.trading_discount_percent > 0 && (
          <div className="bonus-item">
            <span className="bonus-icon">💰</span>
            <span className="bonus-value">-{bonuses.trading_discount_percent}%</span>
            <span className="bonus-label">Trade</span>
          </div>
        )}
        {bonuses.combat_damage_bonus_percent > 0 && (
          <div className="bonus-item">
            <span className="bonus-icon">⚔️</span>
            <span className="bonus-value">+{bonuses.combat_damage_bonus_percent}%</span>
            <span className="bonus-label">Damage</span>
          </div>
        )}
        {bonuses.max_turns_bonus > 0 && (
          <div className="bonus-item">
            <span className="bonus-icon">⏱️</span>
            <span className="bonus-value">+{bonuses.max_turns_bonus}</span>
            <span className="bonus-label">Turns</span>
          </div>
        )}
      </div>
    </div>
  );
};

export default RankDisplay;
