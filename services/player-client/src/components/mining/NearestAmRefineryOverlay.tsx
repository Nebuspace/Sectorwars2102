/**
 * ARIA / mining HUD companion — nearest AM-flagged refinery + ore buy_price
 * (mining.md:254). Displays tip GET /mining/nearest-am-refinery only.
 */
import React, { useEffect, useState } from 'react';
import { miningAPI } from '../../services/api';
import './nearest-am-refinery.css';

type NearestPayload = {
  found?: boolean;
  station?: { id?: string; name?: string; sector_id?: number } | null;
  hop_distance?: number | null;
  ore_buy_price?: number | null;
  reason?: string | null;
};

const NEAREST_AM_REFINERY_FALLBACK = 'Nearest AM refinery lookup failed';

/** Exported for TypeError/network honesty Vitest (LEG-3245). */
export function formatNearestAmRefineryError(err: unknown, fallback = NEAREST_AM_REFINERY_FALLBACK): string {
  if (err instanceof TypeError) return fallback;
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

const asRecord = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;

export const NearestAmRefineryOverlay: React.FC = () => {
  const [payload, setPayload] = useState<NearestPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    miningAPI
      .getNearestAmRefinery()
      .then((raw) => {
        if (cancelled) return;
        setError(null);
        setPayload(asRecord(raw) as NearestPayload);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setPayload(null);
        setError(formatNearestAmRefineryError(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="am-refinery-overlay" data-testid="am-refinery-overlay">
      <div className="am-refinery-head">ARIA — NEAREST AM REFINERY</div>
      {loading && (
        <div className="am-refinery-line" role="status">
          Linking market intel…
        </div>
      )}
      {error && (
        <div className="am-refinery-line am-refinery-err" role="alert">
          {error}
        </div>
      )}
      {!loading && !error && payload?.found && payload.station && (
        <div className="am-refinery-body" data-testid="am-refinery-found">
          <div className="am-refinery-line">
            {payload.station.name ?? 'Unnamed station'}
            {payload.station.sector_id != null ? ` · sector ${payload.station.sector_id}` : ''}
          </div>
          <div className="am-refinery-line">
            {payload.hop_distance != null ? `${payload.hop_distance} hop(s)` : 'hops unknown'}
            {' · '}
            ore buy {payload.ore_buy_price != null ? `${payload.ore_buy_price} cr` : '—'}
          </div>
        </div>
      )}
      {!loading && !error && !payload?.found && (
        <div className="am-refinery-line" role="status" data-testid="am-refinery-empty">
          No AM refinery reachable
          {payload?.reason ? ` (${payload.reason})` : ''}
        </div>
      )}
    </div>
  );
};

export default NearestAmRefineryOverlay;
