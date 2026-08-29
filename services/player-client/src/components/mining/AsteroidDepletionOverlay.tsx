/**
 * Asteroid-field HUD overlay — GS asteroid_depletion from current-sector
 * (mining.md:255). Consumes the sector payload GameDashboard already loads.
 * Not a yield-preview; no extra GET; HARVEST POST stays on handleHarvest.
 */
import React, { useEffect, useState } from 'react';
import './asteroid-depletion-overlay.css';

export type AsteroidDepletionReadout = {
  band?: string | null;
  yield_modifier?: number | null;
  depletion_pool?: number | null;
  pool_size?: number | null;
  consumed_fraction?: number | null;
  richness_tier?: number | null;
  replenish_hours?: number | null;
  replenish_eta?: string | null;
  last_harvest_at?: string | null;
};

type Props = {
  readout: AsteroidDepletionReadout | null | undefined;
};

/** Canon four-step labels plus honest GS `exhausted`. Unknown bands pass through. */
export const DEPLETION_BAND_LABEL: Record<string, string> = {
  fresh: 'Fresh',
  light: 'Light',
  moderate: 'Moderate',
  heavy: 'Heavy',
  exhausted: 'Exhausted',
};

const COUNTDOWN_BANDS = new Set(['heavy', 'exhausted']);

const asRecord = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;

export function parseDepletionReadout(
  raw: unknown,
): AsteroidDepletionReadout | null {
  const rec = asRecord(raw);
  if (!rec) return null;
  const band = typeof rec.band === 'string' ? rec.band.toLowerCase() : null;
  return {
    band,
    yield_modifier: typeof rec.yield_modifier === 'number' ? rec.yield_modifier : null,
    depletion_pool: typeof rec.depletion_pool === 'number' ? rec.depletion_pool : null,
    pool_size: typeof rec.pool_size === 'number' ? rec.pool_size : null,
    consumed_fraction:
      typeof rec.consumed_fraction === 'number' ? rec.consumed_fraction : null,
    richness_tier: typeof rec.richness_tier === 'number' ? rec.richness_tier : null,
    replenish_hours:
      typeof rec.replenish_hours === 'number' ? rec.replenish_hours : null,
    replenish_eta: typeof rec.replenish_eta === 'string' ? rec.replenish_eta : null,
    last_harvest_at:
      typeof rec.last_harvest_at === 'string' ? rec.last_harvest_at : null,
  };
}

export function formatRemaining(ms: number): string {
  const sec = Math.max(0, Math.floor(ms / 1000));
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

export function formatReplenishCountdown(etaIso: string, nowMs: number): string | null {
  const eta = Date.parse(etaIso);
  if (Number.isNaN(eta)) return null;
  return formatRemaining(eta - nowMs);
}

export function bandLabel(band: string | null | undefined): string {
  if (!band) return 'Unknown';
  return DEPLETION_BAND_LABEL[band] ?? band;
}

export const AsteroidDepletionOverlay: React.FC<Props> = ({ readout: raw }) => {
  const readout = parseDepletionReadout(raw);
  const showCountdown = Boolean(readout && readout.band && COUNTDOWN_BANDS.has(readout.band));
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    if (!showCountdown || !readout?.replenish_eta) return undefined;
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [showCountdown, readout?.replenish_eta]);

  if (!readout) {
    return (
      <div
        className="depletion-overlay"
        data-testid="asteroid-depletion-overlay"
        data-empty="true"
      >
        <div data-testid="asteroid-depletion-empty" role="status" />
      </div>
    );
  }

  const countdown =
    showCountdown && readout.replenish_eta
      ? formatReplenishCountdown(readout.replenish_eta, nowMs)
      : null;

  return (
    <div className="depletion-overlay" data-testid="asteroid-depletion-overlay">
      <div className="depletion-head">ASTEROID DEPLETION</div>
      <div
        className="depletion-line depletion-band"
        data-testid="asteroid-depletion-band"
      >
        {bandLabel(readout.band)}
      </div>
      {showCountdown && countdown && (
        <div
          className="depletion-line"
          data-testid="asteroid-depletion-countdown"
          role="status"
        >
          Replenish {countdown}
        </div>
      )}
      {showCountdown && !countdown && readout.replenish_hours != null && (
        <div
          className="depletion-line"
          data-testid="asteroid-depletion-countdown"
          role="status"
        >
          Replenish {readout.replenish_hours}h
        </div>
      )}
    </div>
  );
};

export default AsteroidDepletionOverlay;
