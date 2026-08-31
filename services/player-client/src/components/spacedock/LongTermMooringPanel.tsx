import React, { useMemo, useState } from 'react';
import { tradingAPI } from '../../services/api';
import { useGame } from '../../contexts/GameContext';
import { formatCredits } from '../../utils/formatters';

/**
 * LEG-438 — Long-term mooring acquire/release (tip GS contract only).
 *
 * Canon: FEATURES/economy/docking-slips.md — 1–30 days @ tip
 * docking_service.LONG_TERM_MOORING_RATE_PER_DAY (200 cr/day).
 * Routes: POST /api/v1/trading/mooring/long-term + .../release.
 */

/** Tip `docking_service.LONG_TERM_MOORING_RATE_PER_DAY` — preview only, not invent. */
export const LONG_TERM_MOORING_RATE_PER_DAY = 200;
export const LONG_TERM_MOORING_MAX_DAYS = 30;

type Busy = 'acquire' | 'release' | null;

const MOORING_FAILED_FALLBACK = 'Long-term mooring request failed';

/** Exported for TypeError/network honesty Vitest (LEG-3255). */
export function errMessage(e: unknown): string {
  if (e instanceof TypeError) return MOORING_FAILED_FALLBACK;
  if (e && typeof e === 'object') {
    const any = e as { message?: string; status?: number; data?: any };
    if (any.status === 409) {
      const slips = any.data?.slips;
      const detail =
        typeof any.data?.detail === 'string'
          ? any.data.detail
          : any.message;
      if (slips && typeof slips.capacity === 'number') {
        return (
          (detail || 'All long-term mooring slips are occupied') +
          ` (${slips.occupied ?? '?'}/${slips.capacity})`
        );
      }
      return detail || 'All long-term mooring slips are occupied';
    }
    if (typeof any.message === 'string' && any.message) return any.message;
  }
  return MOORING_FAILED_FALLBACK;
}

const LongTermMooringPanel: React.FC = () => {
  const { playerState, stationsInSector, refreshPlayerState, updatePlayerCredits } =
    useGame();
  const [days, setDays] = useState(7);
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const stationId = useMemo(() => {
    if (playerState?.current_port_id) return String(playerState.current_port_id);
    if (stationsInSector?.length === 1) return String(stationsInSector[0].id);
    return '';
  }, [playerState?.current_port_id, stationsInSector]);

  const clampedDays = Math.max(1, Math.min(LONG_TERM_MOORING_MAX_DAYS, days || 1));
  const feePreview = clampedDays * LONG_TERM_MOORING_RATE_PER_DAY;
  const docked = Boolean(playerState?.is_docked);
  const canAcquire = docked && Boolean(stationId) && !busy;

  const acquire = async () => {
    if (!canAcquire) return;
    setBusy('acquire');
    setError(null);
    setSuccess(null);
    try {
      const result = await tradingAPI.acquireLongTermMooring(stationId, clampedDays);
      if (typeof result?.credits_remaining === 'number') {
        updatePlayerCredits(result.credits_remaining);
      } else {
        await refreshPlayerState();
      }
      setSuccess(
        typeof result?.message === 'string'
          ? result.message
          : `Long-term mooring secured for ${clampedDays} day(s)`,
      );
    } catch (e) {
      setError(errMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const release = async () => {
    setBusy('release');
    setError(null);
    setSuccess(null);
    try {
      const result = await tradingAPI.releaseLongTermMooring();
      await refreshPlayerState();
      setSuccess(
        typeof result?.message === 'string'
          ? result.message
          : result?.released
            ? 'Long-term mooring released'
            : 'No long-term mooring slip to release',
      );
    } catch (e) {
      setError(errMessage(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section
      className="long-term-mooring-panel service-card"
      data-testid="long-term-mooring-panel"
      aria-label="Long-term mooring"
    >
      <div className="service-icon">⚓</div>
      <h3>Long-term mooring</h3>
      <p>
        Reserve a multi-day slip (1–{LONG_TERM_MOORING_MAX_DAYS} days) at{' '}
        {LONG_TERM_MOORING_RATE_PER_DAY} cr/day. Distinct from transient docking.
      </p>

      {!docked && (
        <div className="service-status" data-testid="mooring-undocked-hint">
          Dock at a station to acquire a long-term mooring slip.
        </div>
      )}
      {docked && !stationId && (
        <div className="service-status" role="alert" data-testid="mooring-no-station">
          Docked station id unavailable — cannot file mooring.
        </div>
      )}

      <div className="service-action" style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', alignItems: 'center' }}>
        <label>
          Days{' '}
          <input
            data-testid="mooring-days"
            type="number"
            min={1}
            max={LONG_TERM_MOORING_MAX_DAYS}
            value={clampedDays}
            disabled={!!busy}
            onChange={(e) => setDays(Number(e.target.value) || 1)}
          />
        </label>
        <span data-testid="mooring-fee-preview">
          Fee preview: {formatCredits(feePreview)}
        </span>
        <button
          type="button"
          className="service-btn"
          data-testid="mooring-acquire"
          disabled={!canAcquire}
          onClick={() => void acquire()}
        >
          {busy === 'acquire' ? 'Reserving…' : 'Acquire long-term slip'}
        </button>
        <button
          type="button"
          className="service-btn"
          data-testid="mooring-release"
          disabled={!!busy}
          onClick={() => void release()}
        >
          {busy === 'release' ? 'Releasing…' : 'Release long-term slip'}
        </button>
      </div>

      {success && (
        <div className="genesis-success-message" data-testid="mooring-success">
          <span className="success-icon">✅</span>
          {success}
        </div>
      )}
      {error && (
        <div className="genesis-error-message" role="alert" data-testid="mooring-error">
          <span className="error-icon">❌</span>
          {error}
        </div>
      )}
    </section>
  );
};

export default LongTermMooringPanel;
