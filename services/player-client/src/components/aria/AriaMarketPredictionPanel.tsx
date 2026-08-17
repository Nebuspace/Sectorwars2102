/**
 * AriaMarketPredictionPanel — docked ARIA market expectations (LEG-375).
 * Consumes tip GET /api/v1/market-prediction/predict/all (statistical, not LLM).
 * Surfaces tip payload fields only — does not invent observation/visit counts
 * (canon example copy mentions them; tip PricePredictionResponse omits them).
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useGame } from '../../contexts/GameContext';
import {
  marketPredictionAPI,
  type MarketPricePrediction,
} from '../../services/api';
import './aria-market-prediction.css';

function formatExpectation(p: MarketPricePrediction): string {
  const mid = Math.round(p.predicted_price);
  const halfBand = Math.max(
    0,
    Math.round((p.upper_bound - p.lower_bound) / 2),
  );
  const hours = p.prediction_horizon_hours || 24;
  const confPct = Math.round((p.confidence ?? 0) * 100);
  const commodity =
    p.commodity.charAt(0).toUpperCase() + p.commodity.slice(1);
  return `I expect ${commodity} to trade around ${mid}±${halfBand} credits in the next ${hours} hours (confidence ${confPct}%).`;
}

export const AriaMarketPredictionPanel: React.FC = () => {
  const { playerState } = useGame();
  const docked = !!playerState?.is_docked;
  const stationId = playerState?.current_port_id ?? null;

  const [rows, setRows] = useState<MarketPricePrediction[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (portId: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await marketPredictionAPI.predictAll({ stationId: portId });
      setRows(Array.isArray(res) ? res : []);
    } catch (e) {
      setRows(null);
      setError(
        e instanceof Error ? e.message : 'Market prediction unavailable',
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!docked || !stationId) {
      setRows(null);
      setError(null);
      setLoading(false);
      return;
    }
    void load(stationId);
  }, [docked, stationId, load]);

  if (!docked || !stationId) return null;

  return (
    <section
      className="aria-market-prediction"
      data-testid="aria-market-prediction"
      aria-label="ARIA market predictions"
    >
      <header className="amp-header">
        <span className="amp-glyph" aria-hidden="true">
          ▸ ARIA
        </span>
        <h3 className="amp-title">Market expectations</h3>
      </header>

      {loading && !rows && (
        <p className="amp-loading" data-testid="amp-loading">
          Reading the local market…
        </p>
      )}

      {error && (
        <p className="amp-error" data-testid="amp-error" role="alert">
          {error}
        </p>
      )}

      {!loading && !error && rows && rows.length === 0 && (
        <p className="amp-empty" data-testid="amp-empty">
          Not enough market signal yet at this port.
        </p>
      )}

      {rows && rows.length > 0 && (
        <ul className="amp-list" data-testid="amp-list">
          {rows.map((p) => (
            <li
              key={`${p.station_id}-${p.commodity}-${p.timestamp}`}
              className="amp-line"
              data-testid="amp-line"
            >
              <span className="amp-prefix">ARIA&gt;</span>
              <span className="amp-text">{formatExpectation(p)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
};

export default AriaMarketPredictionPanel;
