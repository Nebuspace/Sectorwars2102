import React, { useState, useEffect, useCallback } from 'react';
import { useAIUpdates } from '../../contexts/WebSocketContext';
import { api } from '../../utils/auth';
import './player-behavior-analytics.css';

interface BehaviorTrend {
  metric: string;
  current: number;
  previous: number;
  change: number;
  trend: 'increasing' | 'stable' | 'decreasing';
  insight?: string;
}

interface PlayerSegment {
  pattern: string;
  count: number;
  description: string;
  avgProfit: number;
  aiEngagement: number;
}

/**
 * Honesty: /api/v1/admin/ai/behavior-analytics returns aggregate patterns +
 * insights only — not per-player profile rows. Do not invent filters, empty
 * profile tables, or detail panels that imply that data exists.
 */
export const PlayerBehaviorAnalytics: React.FC = () => {
  const [segments, setSegments] = useState<PlayerSegment[]>([]);
  const [trends, setTrends] = useState<BehaviorTrend[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const handleSegmentUpdate = useCallback((data: any) => {
    setSegments(data);
  }, []);

  const handleTrendUpdate = useCallback((data: any) => {
    setTrends(data);
  }, []);

  useAIUpdates(undefined, undefined, undefined, undefined, undefined, undefined, undefined, undefined, handleSegmentUpdate, handleTrendUpdate);

  useEffect(() => {
    fetchAggregates();
  }, []);

  const fetchAggregates = async () => {
    try {
      setLoading(true);
      const response = await api.get('/api/v1/admin/ai/behavior-analytics');
      setSegments(response.data.player_patterns || []);
      setTrends(response.data.recent_insights ? response.data.recent_insights.map((insight: string, index: number) => ({
        metric: `Insight ${index + 1}`,
        current: 0,
        previous: 0,
        change: 0,
        trend: 'stable' as const,
        insight: insight
      })) : []);
    } catch (err: any) {
      const errorMessage = err.response?.data?.detail || err.message || 'Failed to load behavior analytics';
      if (err.response?.status === 401) {
        setError('Authentication required. Please log in as an admin user.');
      } else {
        setError(errorMessage);
      }
    } finally {
      setLoading(false);
    }
  };

  if (loading) return <div className="loading">Loading behavior analytics...</div>;
  if (error) return <div className="error">Error: {error}</div>;

  return (
    <div className="player-behavior-analytics">
      <div className="analytics-header">
        <div className="segment-overview">
          <h3>Player Segments</h3>
          <div className="segments-grid">
            {segments.map(segment => (
              <div key={segment.pattern} className="segment-card">
                <h4>{segment.pattern}</h4>
                <div className="segment-stats">
                  <div className="segment-stat">
                    <span className="stat-value">{segment.count}</span>
                    <span className="stat-label">Players</span>
                  </div>
                  <div className="segment-stat">
                    <span className="stat-value">{segment.avgProfit.toFixed(1)}%</span>
                    <span className="stat-label">Avg Profit</span>
                  </div>
                  <div className="segment-stat">
                    <span className="stat-value">{segment.aiEngagement}%</span>
                    <span className="stat-label">AI Engagement</span>
                  </div>
                </div>
                <div className="segment-description">
                  <p>{segment.description}</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="behavior-trends">
          <h3>Recent Insights</h3>
          <div className="insights-list">
            {trends.map(trend => (
              <div key={trend.metric} className="insight-item">
                <span className="insight-text">{trend.insight}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div
        role="note"
        style={{
          margin: '16px 0 0', padding: '10px 12px',
          background: 'rgba(234, 179, 8, 0.12)', border: '1px solid rgba(234, 179, 8, 0.35)',
          borderRadius: '6px', color: '#fbbf24', fontSize: '0.82rem', lineHeight: 1.4
        }}
      >
        Per-player behavior profiles are unavailable: the behavior-analytics endpoint
        returns aggregate patterns and insights only (shown above). This panel does not
        invent profile filters, empty profile tables, or detail drawers.
      </div>
    </div>
  );
};
