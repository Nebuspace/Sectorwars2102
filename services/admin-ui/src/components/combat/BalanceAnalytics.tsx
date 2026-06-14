import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../../utils/auth';
import './balance-analytics.css';

// =============================================================================
// Types — mirror the response shapes in
// services/gameserver/src/api/routes/admin_combat.py
//   GET /api/v1/admin/combat/balance -> CombatBalanceResponse
//   GET /api/v1/admin/combat/stats   -> CombatStatsResponse
// and the analytics dicts built in
// services/gameserver/src/services/combat_analytics_service.py
// =============================================================================

type GroupBy = 'ship_type' | 'player_level' | 'combat_type';

// A single analytics entry. The CombatBalanceResponse.analytics dict is keyed by
// the group label (ship type name, military rank, or combat type) and the value
// shape depends on group_by. All numeric fields are optional because the backend
// only fills win_rate / averages once a group has at least one combat, and the
// per-group_by shapes differ. We model every known field as optional.
interface BalanceAnalyticsEntry {
  // ship_type + player_level
  wins?: number;
  losses?: number;
  total?: number;
  win_rate?: number;
  // ship_type only
  damage_dealt?: number;
  damage_taken?: number;
  avg_damage_dealt?: number;
  avg_damage_taken?: number;
  // combat_type only
  count?: number;
  avg_duration?: number;
  avg_rounds?: number;
  total_damage?: number;
  avg_damage?: number;
}

// Matches _calculate_balance_metrics(); min/max/spread are absent when no group
// has a win_rate (returns {balance_score, variance} only).
interface BalanceMetrics {
  balance_score: number;
  variance: number;
  min_win_rate?: number;
  max_win_rate?: number;
  spread?: number;
}

// Matches _identify_balance_outliers()
interface BalanceOutlier {
  entity: string;
  type: 'overpowered' | 'underpowered';
  win_rate: number;
  sample_size: number;
  severity: 'high' | 'medium';
}

// Matches CombatBalanceResponse
interface CombatBalance {
  timeframe: string;
  total_combats: number;
  group_by: string;
  analytics: Record<string, BalanceAnalyticsEntry>;
  balance_metrics: BalanceMetrics;
  outliers: BalanceOutlier[];
  recommendations: string[];
}

// Matches CombatStatsResponse
interface CombatStats {
  total_combats_today: number;
  total_ships_destroyed: number;
  total_credits_looted: number;
  average_combat_duration: number;
  most_active_combatant: string;
  deadliest_ship_type: string;
}

const GROUP_OPTIONS: { value: GroupBy; label: string }[] = [
  { value: 'ship_type', label: 'Ship Type' },
  { value: 'player_level', label: 'Player Rank' },
  { value: 'combat_type', label: 'Combat Type' },
];

const formatPct = (rate: number): string => `${(rate * 100).toFixed(1)}%`;
const formatNum = (n: number): string =>
  n.toLocaleString(undefined, { maximumFractionDigits: 1 });

const winRateClass = (rate: number | undefined): string => {
  if (rate === undefined) return '';
  if (rate > 0.7 || rate < 0.3) return 'imbalanced';
  return 'balanced';
};

const BalanceAnalytics: React.FC = () => {
  const [groupBy, setGroupBy] = useState<GroupBy>('ship_type');
  const [balance, setBalance] = useState<CombatBalance | null>(null);
  const [stats, setStats] = useState<CombatStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadData = useCallback(async (selectedGroup: GroupBy) => {
    setLoading(true);
    setError(null);

    const [balanceRes, statsRes] = await Promise.allSettled([
      api.get<CombatBalance>('/api/v1/admin/combat/balance', {
        params: { group_by: selectedGroup, timeframe: '7d' },
      }),
      api.get<CombatStats>('/api/v1/admin/combat/stats', {
        params: { time_filter: '24h' },
      }),
    ]);

    if (balanceRes.status === 'fulfilled') {
      setBalance(balanceRes.value.data);
    } else {
      setBalance(null);
    }

    if (statsRes.status === 'fulfilled') {
      setStats(statsRes.value.data);
    } else {
      setStats(null);
    }

    const failed = [balanceRes, statsRes].filter((r) => r.status === 'rejected');
    if (failed.length === 2) {
      setError('Failed to load balance analytics. Please check if the gameserver is running.');
    } else if (balanceRes.status === 'rejected') {
      setError('Balance analytics unavailable.');
    } else if (statsRes.status === 'rejected') {
      setError('Combat statistics unavailable.');
    }

    setLoading(false);
  }, []);

  useEffect(() => {
    loadData(groupBy);
  }, [groupBy, loadData]);

  // The player_level grouping seeds a synthetic 'all_players' aggregate key that
  // sums every rank; exclude it from the per-entity table so it isn't shown as a
  // peer rank (it would double-count). Real per-group rows only.
  const analyticsEntries: [string, BalanceAnalyticsEntry][] = balance
    ? Object.entries(balance.analytics).filter(([key]) => key !== 'all_players')
    : [];

  return (
    <div className="balance-analytics">
      <div className="balance-analytics-header">
        <div className="balance-analytics-controls">
          <label htmlFor="balance-group-by" className="balance-analytics-label">
            Group by
          </label>
          <select
            id="balance-group-by"
            className="balance-analytics-select"
            value={groupBy}
            onChange={(e) => setGroupBy(e.target.value as GroupBy)}
          >
            {GROUP_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
        {balance && (
          <div className="balance-analytics-timeframe">
            {balance.total_combats.toLocaleString()} combats over {balance.timeframe}
          </div>
        )}
      </div>

      {error && (
        <div className="balance-analytics-alert error">
          <span className="balance-analytics-alert-icon">⚠</span>
          <span>{error}</span>
        </div>
      )}

      {loading ? (
        <div className="balance-analytics-loading">
          <div className="loading-spinner"></div>
          <span>Loading balance analytics...</span>
        </div>
      ) : (
        <>
          {/* Key combat stats (24h) */}
          {stats ? (
            <div className="balance-stats-grid">
              <div className="balance-stat">
                <span className="balance-stat-value">
                  {stats.total_combats_today.toLocaleString()}
                </span>
                <span className="balance-stat-label">Combats (24h)</span>
              </div>
              <div className="balance-stat">
                <span className="balance-stat-value">
                  {stats.total_ships_destroyed.toLocaleString()}
                </span>
                <span className="balance-stat-label">Ships Destroyed</span>
              </div>
              <div className="balance-stat">
                <span className="balance-stat-value">
                  {stats.total_credits_looted.toLocaleString()}
                </span>
                <span className="balance-stat-label">Credits Looted</span>
              </div>
              <div className="balance-stat">
                <span className="balance-stat-value">
                  {formatNum(stats.average_combat_duration)}s
                </span>
                <span className="balance-stat-label">Avg Duration</span>
              </div>
              <div className="balance-stat">
                <span className="balance-stat-value balance-stat-text">
                  {stats.most_active_combatant}
                </span>
                <span className="balance-stat-label">Most Active</span>
              </div>
              <div className="balance-stat">
                <span className="balance-stat-value balance-stat-text">
                  {stats.deadliest_ship_type}
                </span>
                <span className="balance-stat-label">Deadliest Ship</span>
              </div>
            </div>
          ) : (
            !error && (
              <div className="balance-analytics-empty">
                No combat statistics available for the last 24 hours.
              </div>
            )
          )}

          {/* Balance metrics summary */}
          {balance && (
            <div className="balance-metrics-row">
              <div className="balance-metric">
                <span className="balance-metric-label">Balance Score</span>
                <span className="balance-metric-value">
                  {balance.balance_metrics.balance_score.toFixed(1)}%
                </span>
              </div>
              {balance.balance_metrics.min_win_rate !== undefined && (
                <div className="balance-metric">
                  <span className="balance-metric-label">Win Rate Range</span>
                  <span className="balance-metric-value">
                    {formatPct(balance.balance_metrics.min_win_rate)}
                    {' – '}
                    {formatPct(balance.balance_metrics.max_win_rate ?? 0)}
                  </span>
                </div>
              )}
              {balance.balance_metrics.spread !== undefined && (
                <div className="balance-metric">
                  <span className="balance-metric-label">Spread</span>
                  <span className="balance-metric-value">
                    {formatPct(balance.balance_metrics.spread)}
                  </span>
                </div>
              )}
              <div className="balance-metric">
                <span className="balance-metric-label">Outliers</span>
                <span className="balance-metric-value">
                  {balance.outliers.length}
                </span>
              </div>
            </div>
          )}

          {/* Per-group analytics table */}
          {balance && analyticsEntries.length > 0 ? (
            <div className="balance-table-wrap">
              <table className="balance-table">
                <thead>
                  {groupBy === 'combat_type' ? (
                    <tr>
                      <th>Combat Type</th>
                      <th>Count</th>
                      <th>Avg Rounds</th>
                      <th>Avg Duration</th>
                      <th>Avg Damage</th>
                    </tr>
                  ) : (
                    <tr>
                      <th>{groupBy === 'ship_type' ? 'Ship Type' : 'Player Rank'}</th>
                      <th>Combats</th>
                      <th>Wins</th>
                      <th>Losses</th>
                      <th>Win Rate</th>
                      {groupBy === 'ship_type' && <th>Avg Dmg Dealt</th>}
                    </tr>
                  )}
                </thead>
                <tbody>
                  {analyticsEntries.map(([key, entry]) =>
                    groupBy === 'combat_type' ? (
                      <tr key={key}>
                        <td className="balance-entity">{key}</td>
                        <td>{(entry.count ?? 0).toLocaleString()}</td>
                        <td>{formatNum(entry.avg_rounds ?? 0)}</td>
                        <td>{formatNum(entry.avg_duration ?? 0)}s</td>
                        <td>{formatNum(entry.avg_damage ?? 0)}</td>
                      </tr>
                    ) : (
                      <tr key={key}>
                        <td className="balance-entity">{key}</td>
                        <td>{(entry.total ?? 0).toLocaleString()}</td>
                        <td>{(entry.wins ?? 0).toLocaleString()}</td>
                        <td>{(entry.losses ?? 0).toLocaleString()}</td>
                        <td className={`balance-winrate ${winRateClass(entry.win_rate)}`}>
                          {entry.win_rate !== undefined ? formatPct(entry.win_rate) : '—'}
                        </td>
                        {groupBy === 'ship_type' && (
                          <td>{formatNum(entry.avg_damage_dealt ?? 0)}</td>
                        )}
                      </tr>
                    )
                  )}
                </tbody>
              </table>
            </div>
          ) : (
            balance &&
            !error && (
              <div className="balance-analytics-empty">
                No completed combats in the last 7 days to analyze for this grouping.
              </div>
            )
          )}

          {/* Outliers */}
          {balance && balance.outliers.length > 0 && (
            <div className="balance-outliers">
              <h3 className="balance-subhead">Balance Outliers</h3>
              <div className="balance-outlier-grid">
                {balance.outliers.map((outlier) => (
                  <div
                    key={`${outlier.entity}-${outlier.type}`}
                    className={`balance-outlier-card ${outlier.type} severity-${outlier.severity}`}
                  >
                    <div className="balance-outlier-top">
                      <span className="balance-outlier-entity">{outlier.entity}</span>
                      <span className={`balance-outlier-tag ${outlier.type}`}>
                        {outlier.type === 'overpowered' ? 'Overpowered' : 'Underpowered'}
                      </span>
                    </div>
                    <div className="balance-outlier-stats">
                      <span>Win rate {formatPct(outlier.win_rate)}</span>
                      <span>n = {outlier.sample_size.toLocaleString()}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Recommendations */}
          {balance && balance.recommendations.length > 0 && (
            <div className="balance-recommendations">
              <h3 className="balance-subhead">Recommendations</h3>
              <ul className="balance-recommendation-list">
                {balance.recommendations.map((rec, idx) => (
                  <li key={idx} className="balance-recommendation">
                    {rec}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default BalanceAnalytics;
