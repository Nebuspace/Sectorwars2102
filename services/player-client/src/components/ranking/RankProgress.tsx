import React, { useState, useEffect } from 'react';
import { rankingAPI } from '../../services/api';
import { TIER_COLORS } from './RankDisplay';
import './ranking.css';

interface RankRequirement {
  name: string;
  current: number;
  required: number | null;
  met: boolean;
}

interface RankProgressData {
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
  is_max_rank: boolean;
  stats: {
    combat_victories: number;
    total_trades: number;
    trade_volume: number;
    exploration_score: number;
    credits: number;
    turns_remaining: number;
  };
  requirements: RankRequirement[];
}

const RankProgress: React.FC = () => {
  const [data, setData] = useState<RankProgressData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchProgress = async () => {
      try {
        setLoading(true);
        const result = await rankingAPI.getProgress();
        setData(result);
        setError(null);
      } catch (err: any) {
        setError(err.message || 'Failed to load rank progress');
      } finally {
        setLoading(false);
      }
    };
    fetchProgress();
  }, []);

  if (loading) {
    return (
      <div className="rank-progress rank-progress-loading">
        <div className="rank-spinner" />
        <span>Loading progress...</span>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="rank-progress rank-progress-error">
        <span>{error || 'Progress unavailable'}</span>
      </div>
    );
  }

  const tierColor = TIER_COLORS[data.rank_tier] || '#ffffff';

  return (
    <div className="rank-progress">
      <div className="rank-progress-header">
        <h3>Rank Progress</h3>
      </div>

      <div className="rank-progress-ranks">
        <div className="rank-progress-current">
          <span className="rank-progress-label">Current</span>
          <span className="rank-progress-value" style={{ color: tierColor }}>
            {data.current_rank}
          </span>
          <span className="rank-progress-tier">{data.rank_tier}</span>
        </div>
        {!data.is_max_rank && data.next_rank && (
          <>
            <div className="rank-progress-arrow">&rarr;</div>
            <div className="rank-progress-next">
              <span className="rank-progress-label">Next</span>
              <span className="rank-progress-value">{data.next_rank}</span>
              <span className="rank-progress-pts">
                {data.rank_points} / {data.next_rank_points_required} pts
              </span>
            </div>
          </>
        )}
        {data.is_max_rank && (
          <div className="rank-progress-max">Maximum Rank Achieved</div>
        )}
      </div>

      <div className="rank-progress-bar">
        <div
          className="rank-progress-fill"
          style={{ width: `${data.progress_percent}%`, backgroundColor: tierColor }}
        />
      </div>
      <div className="rank-progress-pct">{data.progress_percent.toFixed(1)}%</div>

      <div className="rank-progress-reqs">
        <h4>Requirements</h4>
        {data.requirements.map((req) => (
          <div key={req.name} className={`req-item ${req.met ? 'met' : 'unmet'}`}>
            <span className="req-icon">{req.met ? '\u2705' : '\u274C'}</span>
            <span className="req-name">{req.name}</span>
            <span className="req-value">
              {req.current.toLocaleString()}
              {req.required != null && ` / ${req.required.toLocaleString()}`}
            </span>
          </div>
        ))}
      </div>

      <div className="rank-progress-stats">
        <h4>Stats</h4>
        <div className="stats-grid">
          <div className="stat-item">
            <span className="stat-value">{data.stats.combat_victories}</span>
            <span className="stat-label">Combat Wins</span>
          </div>
          <div className="stat-item">
            <span className="stat-value">{data.stats.total_trades}</span>
            <span className="stat-label">Trades</span>
          </div>
          <div className="stat-item">
            <span className="stat-value">{data.stats.trade_volume.toLocaleString()}</span>
            <span className="stat-label">Trade Volume</span>
          </div>
          <div className="stat-item">
            <span className="stat-value">{data.stats.exploration_score}</span>
            <span className="stat-label">ARIA Activity</span>
          </div>
        </div>
      </div>
    </div>
  );
};

export default RankProgress;
