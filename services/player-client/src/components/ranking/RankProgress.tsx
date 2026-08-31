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
const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed)
  );
};

/** True when err looks like gameserver detail (not bare API Error: N / TypeError noise). */
function hasRankProgressServerDetail(err: unknown, message: string | undefined): boolean {
  // Network collapse (fetch TypeError / axios transport) is not gameserver copy.
  if (err instanceof TypeError) return false;
  if (typeof message === 'string' && isNetworkCollapseMessage(message)) return false;
  return (
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim())
  );
}

/** apiRequest throws Error with `.status`; surface gameserver detail on rank progress load. */
export function formatRankProgressLoadError(err: unknown): string {
  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  const hasServerDetail = hasRankProgressServerDetail(err, message);

  if (status === 404) {
    if (hasServerDetail) return message!;
    return 'Failed to load rank progress';
  }

  if (status === 403) {
    if (hasServerDetail) return message!;
    return 'Access denied — you cannot view rank progress right now.';
  }

  if (status === 429) {
    return 'Rank progress rate limit exceeded — wait a moment and try again.';
  }

  if (hasServerDetail) return message!;
  return 'Failed to load rank progress';
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
      } catch (err: unknown) {
        setError(formatRankProgressLoadError(err));
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
  const showCompactBadge =
    data.rank_tier != null && data.rank_level != null;
  // Defensive: RankProgressData's shape is enforced by the TS type, not at
  // runtime -- a malformed/incomplete 200 must not crash the panel. Missing
  // numeric fields render as 0 (the same "nothing yet" reading a brand-new
  // player would legitimately see); missing arrays render as empty lists.
  const progressPercent = data.progress_percent ?? 0;
  const requirements = data.requirements ?? [];
  const stats = data.stats ?? {
    combat_victories: 0,
    total_trades: 0,
    trade_volume: 0,
    exploration_score: 0,
    credits: 0,
    turns_remaining: 0,
  };

  return (
    <div className="rank-progress">
      <div className="rank-progress-header">
        <h3>Rank Progress</h3>
      </div>

      <div className="rank-progress-ranks">
        <div className="rank-progress-current">
          <span className="rank-progress-label">Current</span>
          <div className="rank-progress-current-row">
            {showCompactBadge && (
              <span
                className="rank-badge rank-badge--compact"
                style={{ borderColor: tierColor }}
                data-testid="rank-progress-compact-badge"
              >
                <span className="rank-level">{data.rank_level}</span>
              </span>
            )}
            <span className="rank-progress-value" style={{ color: tierColor }}>
              {data.current_rank}
            </span>
          </div>
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
          style={{ width: `${progressPercent}%`, backgroundColor: tierColor }}
        />
      </div>
      <div className="rank-progress-pct">{progressPercent.toFixed(1)}%</div>

      <div className="rank-progress-reqs">
        <h4>Requirements</h4>
        {requirements.map((req) => (
          <div key={req.name} className={`rank-progress-req-item ${req.met ? 'met' : 'unmet'}`}>
            <span className="rank-progress-req-icon">{req.met ? '\u2705' : '\u274C'}</span>
            <span className="rank-progress-req-name">{req.name}</span>
            <span className="rank-progress-req-value">
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
            <span className="stat-value">{stats.combat_victories}</span>
            <span className="stat-label">Combat Wins</span>
          </div>
          <div className="stat-item">
            <span className="stat-value">{stats.total_trades}</span>
            <span className="stat-label">Trades</span>
          </div>
          <div className="stat-item">
            <span className="stat-value">{stats.trade_volume.toLocaleString()}</span>
            <span className="stat-label">Trade Volume</span>
          </div>
          <div className="stat-item">
            <span className="stat-value">{stats.exploration_score}</span>
            <span className="stat-label">ARIA Activity</span>
          </div>
        </div>
      </div>
    </div>
  );
};

export default RankProgress;
