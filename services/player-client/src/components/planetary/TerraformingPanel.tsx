import React, { useState, useEffect, useCallback } from 'react';
import { getAuthToken } from '../../utils/auth';
import './terraforming-panel.css';

/**
 * TerraformingPanel — planetary habitability engineering.
 *
 * Binds to:
 *   GET  /api/v1/planets/{id}/terraforming/status   (feature-detect: 404 → unavailable)
 *   POST /api/v1/planets/{id}/terraforming/start    { target_level }
 *   POST /api/v1/planets/{id}/terraforming/cancel
 *   GET  /api/v1/planets/terraforming/levels        (level table fallback)
 *
 * The per-planet routes are being added in parallel on the backend, so this
 * panel feature-detects via the status call and renders an "unavailable"
 * notice on 404 instead of erroring.
 */

const API_BASE_URL = import.meta.env.VITE_API_URL || '';

/** Error carrying the HTTP status so callers can feature-detect on 404. */
class TerraformingApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'TerraformingApiError';
    this.status = status;
  }
}

interface RequestOptions {
  method?: string;
  body?: string;
  headers?: Record<string, string>;
}

/** Mirrors the apiRequest pattern in services/api.ts (same headers/auth/error shape). */
async function terraformingRequest(endpoint: string, options: RequestOptions = {}): Promise<any> {
  const token = getAuthToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...options.headers,
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE_URL}${endpoint}`, { ...options, headers });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
    throw new TerraformingApiError(
      typeof error.detail === 'string' ? error.detail : `API Error: ${response.status}`,
      response.status
    );
  }

  return response.json();
}

const terraformingAPI = {
  getStatus: (planetId: string) =>
    terraformingRequest(`/api/v1/planets/${planetId}/terraforming/status`),
  start: (planetId: string, targetLevel: number) =>
    terraformingRequest(`/api/v1/planets/${planetId}/terraforming/start`, {
      method: 'POST',
      body: JSON.stringify({ target_level: targetLevel }),
    }),
  cancel: (planetId: string) =>
    terraformingRequest(`/api/v1/planets/${planetId}/terraforming/cancel`, { method: 'POST' }),
  getLevels: () => terraformingRequest('/api/v1/planets/terraforming/levels'),
};

interface TerraformingLevel {
  level: number;
  name: string;
  creditCost: number;
  durationHours: number;
  habitabilityBoost: number;
  organicsCost: number;
  equipmentCost: number;
}

/** Terraforming status payload; optional fields read defensively (backend in flux). */
interface TerraformingStatus {
  active: boolean;
  planetId?: string;
  planetName?: string;
  currentHabitability?: number;
  terraformingTarget?: number | null;
  progress?: number | null;
  startedAt?: string | null;
  estimatedTicksRemaining?: number | null;
  populationBonus?: string;
  availableLevels?: Record<string, unknown>;
  // Possible future additions — used if present
  estimatedCompletion?: string;
  durationHours?: number;
  level?: number;
  levelName?: string;
}

/** Habitability at or above this makes terraforming unnecessary (mirrors gameserver). */
const TERRAFORMING_MIN_TARGET = 90;
/** Cancellation refunds this fraction of the credit cost (mirrors gameserver). */
const CANCEL_REFUND_PERCENT = 50;

/** Normalize one raw level entry, tolerating camelCase or snake_case field names. */
const normalizeLevel = (raw: unknown): TerraformingLevel | null => {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const num = (...keys: string[]): number => {
    for (const key of keys) {
      const v = r[key];
      if (typeof v === 'number') return v;
    }
    return 0;
  };
  const level = num('level');
  if (level < 1) return null;
  return {
    level,
    name: typeof r.name === 'string' ? r.name : `Level ${level}`,
    creditCost: num('creditCost', 'cost', 'credit_cost'),
    durationHours: num('durationHours', 'duration_hours'),
    habitabilityBoost: num('habitabilityBoost', 'habitability_boost'),
    organicsCost: num('organicsCost', 'organics_cost'),
    equipmentCost: num('equipmentCost', 'equipment_cost'),
  };
};

const normalizeLevels = (raw: unknown): TerraformingLevel[] => {
  if (!raw || typeof raw !== 'object') return [];
  const entries = Array.isArray(raw) ? raw : Object.values(raw);
  return entries
    .map(normalizeLevel)
    .filter((l): l is TerraformingLevel => l !== null)
    .sort((a, b) => a.level - b.level);
};

const formatDuration = (hours: number): string => {
  if (hours <= 0) return '—';
  const days = Math.floor(hours / 24);
  const rem = hours % 24;
  if (days === 0) return `${hours}h`;
  return rem === 0 ? `${days}d` : `${days}d ${rem}h`;
};

const formatTimeRemaining = (ms: number): string => {
  if (ms <= 0) return 'finishing up';
  const totalMinutes = Math.floor(ms / 60_000);
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const minutes = totalMinutes % 60;
  const parts: string[] = [];
  if (days > 0) parts.push(`${days}d`);
  if (hours > 0) parts.push(`${hours}h`);
  parts.push(`${minutes}m`);
  return `${parts.join(' ')} remaining`;
};

interface TerraformingPanelProps {
  planetId: string;
  playerCredits: number;
  /** Habitability from the planet payload, used as a fallback readout. */
  habitabilityScore?: number | null;
  onUpdate?: () => void;
}

type Availability = 'checking' | 'available' | 'unavailable' | 'error';

const TerraformingPanel: React.FC<TerraformingPanelProps> = ({
  planetId,
  playerCredits,
  habitabilityScore = null,
  onUpdate,
}) => {
  const [availability, setAvailability] = useState<Availability>('checking');
  const [status, setStatus] = useState<TerraformingStatus | null>(null);
  const [levels, setLevels] = useState<TerraformingLevel[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState<number>(() => Date.now());

  const fetchStatus = useCallback(
    async (silent = false) => {
      try {
        if (!silent) setAvailability('checking');
        const data: TerraformingStatus = await terraformingAPI.getStatus(planetId);
        setStatus(data);
        setNowMs(Date.now());
        // The inactive status payload carries the level table; otherwise fetch it.
        if (data.availableLevels) {
          setLevels(normalizeLevels(data.availableLevels));
        } else if (!data.active) {
          try {
            const levelData = await terraformingAPI.getLevels();
            setLevels(normalizeLevels(levelData));
          } catch {
            setLevels([]);
          }
        }
        setAvailability('available');
        setErrorMessage(null);
      } catch (err) {
        if (err instanceof TerraformingApiError && err.status === 404) {
          setAvailability('unavailable');
        } else if (!silent) {
          setAvailability('error');
          setErrorMessage(err instanceof Error ? err.message : 'Failed to load terraforming status');
        }
      }
    },
    [planetId]
  );

  useEffect(() => {
    setStatus(null);
    setActionMessage(null);
    fetchStatus();
  }, [fetchStatus]);

  // While a project is active, silently refresh every 60s so progress
  // and the time-remaining readout stay honest.
  useEffect(() => {
    if (availability !== 'available' || !status?.active) return;
    const interval = window.setInterval(() => {
      setNowMs(Date.now());
      fetchStatus(true);
    }, 60_000);
    return () => window.clearInterval(interval);
  }, [availability, status?.active, fetchStatus]);

  const handleStart = async (level: TerraformingLevel) => {
    if (actionLoading) return;
    try {
      setActionLoading(true);
      setActionMessage(null);
      await terraformingAPI.start(planetId, level.level);
      setActionMessage(`${level.name} initiated — terraformers are deploying.`);
      await fetchStatus(true);
      onUpdate?.();
    } catch (err) {
      setActionMessage(err instanceof Error ? err.message : 'Failed to start terraforming');
    } finally {
      setActionLoading(false);
    }
  };

  const handleCancel = async () => {
    if (actionLoading) return;
    try {
      setActionLoading(true);
      setActionMessage(null);
      const result = await terraformingAPI.cancel(planetId);
      const refund = typeof result?.refundAmount === 'number' ? result.refundAmount : null;
      setActionMessage(
        refund !== null
          ? `Terraforming cancelled — ${refund.toLocaleString()} credits refunded.`
          : 'Terraforming cancelled.'
      );
      await fetchStatus(true);
      onUpdate?.();
    } catch (err) {
      setActionMessage(err instanceof Error ? err.message : 'Failed to cancel terraforming');
    } finally {
      setActionLoading(false);
    }
  };

  if (availability === 'checking') {
    return (
      <div className="terraforming-panel terraforming-loading">
        <div className="terraforming-spinner" />
        <span>Establishing terraforming uplink...</span>
      </div>
    );
  }

  if (availability === 'unavailable') {
    return (
      <div className="terraforming-panel terraforming-unavailable">
        <span className="unavailable-icon" aria-hidden="true">🛰️</span>
        <span>
          Terraforming uplink unavailable — planetary engineering systems are not yet online
          for this sector.
        </span>
      </div>
    );
  }

  if (availability === 'error') {
    return (
      <div className="terraforming-panel terraforming-error">
        <span>{errorMessage || 'Terraforming status unavailable'}</span>
        <button onClick={() => fetchStatus()} className="terraforming-retry-btn">Retry</button>
      </div>
    );
  }

  const currentHab = status?.currentHabitability ?? habitabilityScore ?? null;
  const isActive = Boolean(status?.active);
  const progress =
    typeof status?.progress === 'number' ? Math.min(100, Math.max(0, status.progress)) : null;

  // Time remaining, best-effort: prefer an explicit completion timestamp,
  // else derive from startedAt + durationHours when the backend provides it.
  let timeRemaining: string | null = null;
  if (isActive) {
    const completionMs = status?.estimatedCompletion ? Date.parse(status.estimatedCompletion) : NaN;
    if (Number.isFinite(completionMs)) {
      timeRemaining = formatTimeRemaining(completionMs - nowMs);
    } else if (status?.startedAt && typeof status.durationHours === 'number') {
      const endMs = Date.parse(status.startedAt) + status.durationHours * 3_600_000;
      if (Number.isFinite(endMs)) timeRemaining = formatTimeRemaining(endMs - nowMs);
    } else if (typeof status?.estimatedTicksRemaining === 'number') {
      timeRemaining = `≈ ${status.estimatedTicksRemaining} ticks remaining`;
    }
  }

  const habMaxed = currentHab !== null && currentHab >= TERRAFORMING_MIN_TARGET;

  const startDisabledReason = (level: TerraformingLevel): string | null => {
    if (actionLoading) return 'Action in progress';
    if (isActive) return 'A terraforming project is already underway on this planet';
    if (habMaxed)
      return `Habitability is already ${currentHab} — terraforming requires below ${TERRAFORMING_MIN_TARGET}`;
    if (playerCredits < level.creditCost)
      return `Insufficient credits: need ${level.creditCost.toLocaleString()}, you have ${playerCredits.toLocaleString()}`;
    return null;
  };

  return (
    <div className="terraforming-panel">
      <div className="terraforming-header">
        <h3>Terraforming</h3>
        {currentHab !== null && (
          <span className="terraforming-hab-badge" title="Current habitability score">
            HAB {currentHab}/100
          </span>
        )}
      </div>

      {isActive && (
        <div className="terraforming-active">
          <div className={'terraforming-planet-pulse'} aria-hidden="true">
            <span className="pulse-ring" />
            <span className="pulse-ring pulse-ring-delay" />
            <span className="planet-glyph">🪐</span>
          </div>
          <div className="terraforming-active-info">
            <span className="active-title">
              Terraforming in progress
              {typeof status?.levelName === 'string' ? ` — ${status.levelName}` : ''}
            </span>
            <span className="active-target">
              {currentHab !== null ? `Habitability ${currentHab}` : 'Habitability rising'}
              {typeof status?.terraformingTarget === 'number'
                ? ` → target ${status.terraformingTarget}`
                : ''}
            </span>
            {progress !== null && (
              <div className="terraforming-progress-row">
                <div
                  className="terraforming-progress-bar"
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.round(progress)}
                  aria-label="Terraforming progress"
                >
                  <div className="terraforming-progress-fill" style={{ width: `${progress}%` }} />
                </div>
                <span className="terraforming-progress-pct">{Math.round(progress)}%</span>
              </div>
            )}
            {timeRemaining && <span className="active-time">{timeRemaining}</span>}
            {status?.populationBonus && (
              <span className="active-bonus">{status.populationBonus}</span>
            )}
            <button
              onClick={handleCancel}
              disabled={actionLoading}
              className="terraforming-btn cancel-btn"
              title={`Cancel the project. ${CANCEL_REFUND_PERCENT}% of credits are refunded; consumed resources are not.`}
            >
              Cancel Project
            </button>
            <span className="cancel-note">
              Cancelling refunds {CANCEL_REFUND_PERCENT}% of the credit cost. Organics and
              equipment already consumed are not recovered.
            </span>
          </div>
        </div>
      )}

      {!isActive && habMaxed && (
        <div className="terraforming-maxed">
          This world already breathes easy — habitability {currentHab}/100. Terraforming is
          reserved for planets below {TERRAFORMING_MIN_TARGET}.
        </div>
      )}

      {!isActive && levels.length > 0 && (
        <>
          <div className="terraforming-levels-intro">
            Boost habitability with planetary engineering. Credits are paid by you; organics
            and equipment are drawn from this planet&apos;s stockpile.
          </div>
          <div className="terraforming-levels">
            {levels.map((lvl) => {
              const reason = startDisabledReason(lvl);
              return (
                <div key={lvl.level} className="terraforming-level-card">
                  <div className="level-card-header">
                    <span className="level-card-tier">L{lvl.level}</span>
                    <span className="level-card-name">{lvl.name}</span>
                    <span className="level-card-boost" title="Habitability gained on completion">
                      +{lvl.habitabilityBoost} HAB
                    </span>
                  </div>
                  <div className="level-card-costs">
                    <span className="level-cost credits" title="Credit cost (paid by you)">
                      💰 {lvl.creditCost.toLocaleString()}
                    </span>
                    <span className="level-cost" title="Organics drawn from planet stockpile">
                      🌿 {lvl.organicsCost.toLocaleString()}
                    </span>
                    <span className="level-cost" title="Equipment drawn from planet stockpile">
                      ⚙️ {lvl.equipmentCost.toLocaleString()}
                    </span>
                    <span className="level-cost duration" title={`${lvl.durationHours} hours`}>
                      ⏱ {formatDuration(lvl.durationHours)}
                    </span>
                  </div>
                  <button
                    onClick={() => handleStart(lvl)}
                    disabled={reason !== null}
                    className="terraforming-btn start-btn"
                    title={reason ?? `Begin ${lvl.name} (+${lvl.habitabilityBoost} habitability)`}
                  >
                    Start
                  </button>
                </div>
              );
            })}
          </div>
        </>
      )}

      {!isActive && levels.length === 0 && !habMaxed && (
        <div className="terraforming-no-levels">
          No terraforming programs are currently offered. Check back after the next uplink sync.
        </div>
      )}

      {actionMessage && <div className="terraforming-message">{actionMessage}</div>}
    </div>
  );
};

export default TerraformingPanel;
