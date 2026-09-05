import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../../utils/auth';
import { formatAdminApiError } from '../../utils/adminApiError';
import './predictive-analytics.css';

const PLAYERS_VIEW_SCOPE_HINT =
  'admin players view scope (PLAYERS_VIEW) required for market predictions';

const PREDICTION_TIMEFRAMES = ['1h', '4h', '1d', '1w'] as const;
type PredictionTimeframe = (typeof PREDICTION_TIMEFRAMES)[number];

interface PredictionRow {
  commodity: string;
  station_id: string;
  current_price: number;
  predicted_price: number;
  price_change_pct: number;
  trend: string;
  confidence: number;
  volatility?: number;
  lower_bound?: number;
  upper_bound?: number;
  prediction_horizon_hours?: number;
  factors: string[];
  timestamp: string;
}

interface PredictionsResponse {
  timeframe: string;
  hours_ahead: number;
  resource?: string | null;
  station_id?: string | null;
  predictions: PredictionRow[];
  count: number;
  generated_at: string;
}

function confidenceClass(confidence: number): string {
  if (confidence >= 0.7) return 'high';
  if (confidence >= 0.4) return 'medium';
  return 'low';
}

function trendArrow(trend: string): string {
  if (trend === 'rising') return '↑';
  if (trend === 'falling') return '↓';
  return '→';
}

function trendClass(trend: string): string {
  if (trend === 'rising') return 'trend-up';
  if (trend === 'falling') return 'trend-down';
  return 'trend-stable';
}

export const PredictiveAnalytics: React.FC = () => {
  const [timeframe, setTimeframe] = useState<PredictionTimeframe>('1h');
  const [data, setData] = useState<PredictionsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadPredictions = useCallback(async (tf: PredictionTimeframe) => {
    setLoading(true);
    setError(null);
    try {
      const { data: payload } = await api.get<PredictionsResponse>(
        '/api/v1/admin/analytics/predictions',
        { params: { timeframe: tf } },
      );
      setData(payload);
    } catch (err) {
      setData(null);
      setError(
        formatAdminApiError(err, {
          fallback: 'Failed to load market predictions',
          scopeHint: PLAYERS_VIEW_SCOPE_HINT,
        }),
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadPredictions(timeframe);
  }, [timeframe, loadPredictions]);

  if (loading && !data && !error) {
    return (
      <div className="predictive-analytics">
        <div className="predictive-loading" role="status">
          Loading predictions…
        </div>
      </div>
    );
  }

  return (
    <div className="predictive-analytics">
      <div className="analytics-header">
        <h2>Predictive Analytics</h2>
        <div
          className="timeframe-selector"
          role="group"
          aria-label="Prediction timeframe"
        >
          {PREDICTION_TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              type="button"
              className={`timeframe-btn${timeframe === tf ? ' active' : ''}`}
              onClick={() => setTimeframe(tf)}
              aria-pressed={timeframe === tf}
            >
              {tf}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div role="alert" style={{ color: '#f87171', marginBottom: 16 }}>
          {error}
        </div>
      )}

      {!error && data && data.count === 0 && (
        <div role="status" className="predictive-empty">
          No predictions returned for timeframe <strong>{data.timeframe}</strong> (horizon{' '}
          {data.hours_ahead}h). The engine may have insufficient market history.
        </div>
      )}

      {!error && data && data.count > 0 && (
        <>
          <p
            style={{
              fontSize: '0.85rem',
              color: 'var(--text-secondary)',
              marginBottom: 16,
            }}
          >
            {data.count} prediction(s) · horizon {data.hours_ahead}h · generated{' '}
            {data.generated_at}
          </p>
          <div className="predictions-grid">
            {data.predictions.map((row) => {
              const confPct = Math.round(row.confidence * 100);
              const changeSign = row.price_change_pct >= 0 ? '+' : '';
              return (
                <div
                  key={`${row.commodity}-${row.station_id}`}
                  className="prediction-card"
                >
                  <div className="prediction-header">
                    <h3>{row.commodity.replace(/_/g, ' ')}</h3>
                    <span
                      className={`confidence-badge ${confidenceClass(row.confidence)}`}
                    >
                      {confPct}% confidence
                    </span>
                  </div>
                  <div className="prediction-values">
                    <div className="current-value">
                      <span className="label">Current</span>
                      <span className="value">{row.current_price.toFixed(2)}</span>
                    </div>
                    <span className={`arrow ${trendClass(row.trend)}`}>
                      {trendArrow(row.trend)}
                    </span>
                    <div className="predicted-value">
                      <span className="label">Predicted</span>
                      <span className="value">{row.predicted_price.toFixed(2)}</span>
                    </div>
                  </div>
                  <div
                    className={`change-indicator ${
                      row.price_change_pct >= 0 ? 'positive' : 'negative'
                    }`}
                  >
                    {changeSign}{row.price_change_pct.toFixed(1)}% · {row.trend} · station{' '}
                    {row.station_id}
                  </div>
                  {row.factors?.length > 0 && (
                    <div className="prediction-factors">
                      <h4>Factors</h4>
                      <ul>
                        {row.factors.map((f) => (
                          <li key={f}>{f}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
};

export default PredictiveAnalytics;
