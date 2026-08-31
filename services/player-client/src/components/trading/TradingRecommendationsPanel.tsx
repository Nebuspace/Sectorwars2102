import React, { useCallback, useEffect, useState } from 'react';
import { formatCredits } from '../../utils/formatters';
import aiTradingService from '../../services/aiTradingService';
import type { TradingRecommendation } from '../ai/types';
import { isTradingNetworkCollapseMessage } from './networkCollapse';
import './trading-recommendations.css';

function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

/** Surface GS recommendation detail; network collapse uses stable fallback (LEG-3217). */
export function formatRecommendationsLoadError(err: unknown): string {
  const fallback = 'Failed to load trading recommendations';
  if (err instanceof TypeError) return fallback;

  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  const hasServerDetail =
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !isTradingNetworkCollapseMessage(message) &&
    !/^Failed to fetch recommendations:/i.test(message.trim()) &&
    !/^API Error: \d+$/.test(message.trim());

  if (status === 503 || status === 500) {
    if (hasServerDetail) return message!;
    return fallback;
  }

  if (hasServerDetail) return message!;
  return fallback;
}

const TYPE_LABEL: Record<TradingRecommendation['type'], string> = {
  buy: 'Buy',
  sell: 'Sell',
  route: 'Route',
  avoid: 'Avoid',
  wait: 'Wait',
};

/**
 * LEG-3217 — first player consumer of GET /api/v1/ai/recommendations.
 * Browse persisted ARIA trading recommendations from the trading desk.
 */
const TradingRecommendationsPanel: React.FC = () => {
  const [collapsed, setCollapsed] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recommendations, setRecommendations] = useState<TradingRecommendation[]>([]);
  const [feedbackBusyId, setFeedbackBusyId] = useState<string | null>(null);
  const [feedbackNotice, setFeedbackNotice] = useState<string | null>(null);

  const loadRecommendations = useCallback(async () => {
    setLoading(true);
    setError(null);
    setFeedbackNotice(null);
    try {
      const rows = await aiTradingService.getRecommendations(10, false);
      setRecommendations(Array.isArray(rows) ? rows : []);
    } catch (err: unknown) {
      setRecommendations([]);
      setError(formatRecommendationsLoadError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (collapsed) return;
    void loadRecommendations();
  }, [collapsed, loadRecommendations]);

  const submitFeedback = async (rec: TradingRecommendation, accepted: boolean) => {
    if (feedbackBusyId) return;
    setFeedbackBusyId(rec.id);
    setFeedbackNotice(null);
    try {
      await aiTradingService.submitRecommendationFeedback(rec.id, { accepted });
      setFeedbackNotice(
        accepted
          ? 'Thanks — ARIA will weight similar suggestions higher.'
          : 'Noted — ARIA will deprioritize similar suggestions.',
      );
    } catch {
      setFeedbackNotice('Could not record feedback right now. Try again later.');
    } finally {
      setFeedbackBusyId(null);
    }
  };

  return (
    <div className="trading-recommendations-panel" data-testid="trading-recommendations-panel">
      <div
        className="trading-recommendations-header"
        onClick={() => setCollapsed(!collapsed)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setCollapsed(!collapsed);
          }
        }}
      >
        <h3>ARIA Trading Recommendations</h3>
        <span className="trading-recommendations-toggle">{collapsed ? '▶' : '▼'}</span>
      </div>
      {!collapsed && (
        <>
          <p className="trading-recommendations-subtitle">
            Persisted market suggestions from ARIA — review before acting; execution still requires your confirmation.
          </p>
          <div className="trading-recommendations-body">
            {loading && (
              <p className="trading-recommendations-status" data-testid="trading-recommendations-loading">
                Loading recommendations…
              </p>
            )}
            {!loading && error && (
              <p className="trading-recommendations-error" role="alert" data-testid="trading-recommendations-error">
                {error}
              </p>
            )}
            {!loading && !error && recommendations.length === 0 && (
              <p className="trading-recommendations-empty" data-testid="trading-recommendations-empty">
                ARIA has no active trading recommendations right now. Dock and trade, or check back after market activity.
              </p>
            )}
            {!loading && !error && recommendations.length > 0 && (
              <ul className="trading-recommendations-list">
                {recommendations.map((rec) => (
                  <li key={rec.id} className="trading-recommendation-row" data-testid={`recommendation-${rec.id}`}>
                    <div className="recommendation-head">
                      <span className={`recommendation-type recommendation-type-${rec.type}`}>
                        {TYPE_LABEL[rec.type] ?? rec.type}
                      </span>
                      <span className="recommendation-confidence">
                        {Math.round(rec.confidence * 100)}% confidence · {rec.risk_level} risk
                      </span>
                    </div>
                    <p className="recommendation-reasoning">{rec.reasoning}</p>
                    <div className="recommendation-meta">
                      {rec.expected_profit != null && (
                        <span>Est. profit {formatCredits(rec.expected_profit)}</span>
                      )}
                      {rec.commodity_id && <span>Commodity {rec.commodity_id}</span>}
                      {rec.sector_id && <span>Sector {rec.sector_id}</span>}
                    </div>
                    <div className="recommendation-feedback">
                      <button
                        type="button"
                        className="action-button"
                        disabled={feedbackBusyId === rec.id}
                        onClick={() => void submitFeedback(rec, true)}
                      >
                        Helpful
                      </button>
                      <button
                        type="button"
                        className="action-button"
                        disabled={feedbackBusyId === rec.id}
                        onClick={() => void submitFeedback(rec, false)}
                      >
                        Not useful
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
            {!loading && (
              <button
                type="button"
                className="action-button trading-recommendations-refresh"
                onClick={() => void loadRecommendations()}
              >
                Refresh
              </button>
            )}
            {feedbackNotice && (
              <p className="trading-recommendations-notice" data-testid="trading-recommendations-notice">
                {feedbackNotice}
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
};

export default TradingRecommendationsPanel;
