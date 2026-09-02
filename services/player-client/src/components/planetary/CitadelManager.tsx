import React, { useState, useEffect, useCallback } from 'react';
import { citadelAPI } from '../../services/api';
import { useResourceCatalog } from '../../hooks/useResourceCatalog';
import CitadelStructure from './CitadelStructure';
import './citadel.css';

interface CitadelNextLevel {
  level: number;
  name: string;
  upgrade_cost: number;
  upgrade_hours: number;
  resource_cost: Record<string, number>;
  max_population: number;
  safe_storage: number;
  drone_capacity: number;
}

interface CitadelInfo {
  success: boolean;
  planet_id: string;
  planet_name: string;
  citadel_level: number;
  citadel_name: string;
  max_population: number;
  safe_storage: number;
  safe_credits: number;
  drone_capacity: number;
  is_upgrading: boolean;
  upgrade_started_at?: string;
  upgrade_complete_at?: string;
  upgrade_remaining_seconds?: number;
  next_level: CitadelNextLevel | null;
  /**
   * Highest citadel level this planet's *size* can ever reach (3–5). Small
   * worlds physically can't host the larger key buildings, so the ladder is
   * gated by surface area, not just credits/defense. Supplied by the citadel
   * status API; absent on older responses, in which case no size ceiling is
   * shown (the canon max of 5 still applies via next_level === null).
   */
  max_citadel_level?: number;
}

interface CitadelManagerProps {
  planetId: string;
  playerCredits: number;
  /** Drones currently stationed on the planet, if the caller has that data. */
  stationedDrones?: number;
  onUpdate?: () => void;
}

/** Canon 5-level progression (mirrors gameserver CITADEL_LEVELS). */
const CITADEL_TRACK = [
  { level: 1, name: 'Outpost', maxColonists: 1000, safeStorage: 100000, droneCapacity: 10 },
  { level: 2, name: 'Settlement', maxColonists: 5000, safeStorage: 500000, droneCapacity: 25 },
  { level: 3, name: 'Colony', maxColonists: 15000, safeStorage: 2000000, droneCapacity: 50 },
  { level: 4, name: 'Major Colony', maxColonists: 50000, safeStorage: 10000000, droneCapacity: 100 },
  { level: 5, name: 'Planetary Capital', maxColonists: 200000, safeStorage: 50000000, droneCapacity: 200 },
] as const;

/** One-line in-fiction descriptions per level (UI flavor copy only). */
const CITADEL_FLAVOR: Record<number, string> = {
  1: 'A lone dome against the dust, one antenna whispering back to civilization.',
  2: 'Domes cluster behind the first wall; strangers start calling this place home.',
  3: 'Watchtowers sweep the horizon while the comm spire sings to passing ships.',
  4: 'An orbital defense ring crowns the skyline — this world answers to you.',
  5: 'Twin rings, layered towers, a beacon burning: the capital of a world.',
};

/**
 * Preflight copy for citadel upgrades — building names match
 * CITADEL_UPGRADE_PREREQS / citadels.md (docs win). The gameserver
 * re-validates on POST; 400 detail is the source of truth for which
 * building is missing/offline (do not invent a client-side formula).
 */
const CITADEL_PREREQS: Record<number, string> = {
  3: 'Requires Defense Grid L1 or Automated Turret Network',
  4: 'Requires Defense Grid L2, Automated Turret Network, and Shield Generator L4',
  5: 'Requires Defense Grid L2, Orbital Defense Platform, Rail Gun Battery, and Shield Generator L8',
};

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
    /^network\s*error$/i.test(trimmed) ||
    /^networkerror$/i.test(trimmed)
  );
};

/** apiRequest throws Error with `.status`; surface gameserver detail on citadel load. */
export function formatCitadelLoadError(err: unknown): string {
  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  // Network collapse (fetch TypeError / axios transport) is not gameserver copy — use the caller fallback.
  const hasServerDetail =
    !(err instanceof TypeError) &&
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim()) &&
    !isNetworkCollapseMessage(message);

  if (status === 403) {
    if (hasServerDetail) return message!;
    return 'You do not own this planet.';
  }

  if (status === 429) {
    return 'Citadel lookup rate limit exceeded — wait a moment and try again.';
  }

  if (hasServerDetail) return message!;
  return 'Failed to load citadel info';
}

const compact = (n: number): string => {
  if (n >= 1_000_000) return `${n % 1_000_000 === 0 ? n / 1_000_000 : (n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${n % 1_000 === 0 ? n / 1_000 : (n / 1_000).toFixed(1)}k`;
  return `${n}`;
};

/** Format a millisecond duration as a "2d 5h 12m" countdown string. */
const formatCountdown = (ms: number): string => {
  if (ms <= 0) return 'moments';
  const totalMinutes = Math.floor(ms / 60_000);
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const minutes = totalMinutes % 60;
  const parts: string[] = [];
  if (days > 0) parts.push(`${days}d`);
  if (hours > 0) parts.push(`${hours}h`);
  parts.push(`${minutes}m`);
  return parts.join(' ');
};

interface CitadelUpgradeErrorPayload {
  error_code?: string;
  reason?: string;
  building_key?: string;
  building_name?: string;
  message?: string;
}

function readString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

function extractCitadelUpgradePayload(data: unknown): CitadelUpgradeErrorPayload | undefined {
  if (!data || typeof data !== 'object') return undefined;
  const root = data as Record<string, unknown>;
  const detail = root.detail;
  const src =
    detail && typeof detail === 'object' && !Array.isArray(detail)
      ? (detail as Record<string, unknown>)
      : root;

  const payload: CitadelUpgradeErrorPayload = {
    error_code: readString(src.error_code),
    reason: readString(src.reason),
    building_key: readString(src.building_key),
    building_name: readString(src.building_name),
    message: readString(src.message),
  };

  return Object.values(payload).some(Boolean) ? payload : undefined;
}

function serverUpgradeDetail(err: unknown): string | undefined {
  // Network collapse (fetch TypeError / axios transport) is not gameserver copy.
  if (err instanceof TypeError) return undefined;
  if (err && typeof err === 'object') {
    const data = (err as { data?: unknown }).data;
    if (data && typeof data === 'object') {
      const body = data as Record<string, unknown>;
      if (typeof body.detail === 'string' && body.detail.trim()) {
        const trimmed = body.detail.trim();
        if (!isNetworkCollapseMessage(trimmed)) return trimmed;
      }
      if (typeof body.message === 'string' && body.message.trim()) {
        const trimmed = body.message.trim();
        if (!isNetworkCollapseMessage(trimmed)) return trimmed;
      }
    }
  }
  const message = err instanceof Error ? err.message : undefined;
  if (
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim()) &&
    !isNetworkCollapseMessage(message)
  ) {
    return message.trim();
  }
  return undefined;
}

/** POST /citadel/upgrade refusals — surface GS detail or structured building identity. */
export function formatCitadelUpgradeError(err: unknown): string {
  const status = httpStatus(err);
  const earlyDetail = serverUpgradeDetail(err);

  if (status === 403) {
    if (earlyDetail) return earlyDetail;
    return 'You do not have permission to upgrade this citadel.';
  }

  if (status === 429) {
    return 'Citadel upgrade rate limit exceeded — wait a moment and try again.';
  }

  const data = err && typeof err === 'object' ? (err as { data?: unknown }).data : undefined;
  const payload = extractCitadelUpgradePayload(data);

  if (payload) {
    const msg = payload.message || serverUpgradeDetail(err);
    if (msg) return msg;

    const name = payload.building_name;
    if (name) {
      if (payload.reason === 'prerequisite_building_offline') {
        return `${name} must be operational before upgrading your citadel.`;
      }
      if (
        payload.reason === 'prerequisite_building_missing' ||
        payload.error_code === 'ERR_CITADEL_PREREQUISITE_MISSING'
      ) {
        return `Upgrade requires ${name} — build it first.`;
      }
      if (payload.error_code === 'ERR_CITADEL_PREREQUISITE_OFFLINE') {
        return `${name} is not operational yet — finish construction first.`;
      }
      return `Upgrade requires ${name}.`;
    }
  }

  const detail = serverUpgradeDetail(err);
  if (detail) return detail;
  return 'Upgrade failed';
}

/** Cap on rendered drone pips; above this, each pip represents a share of capacity. */
const MAX_DRONE_PIPS = 25;

const CitadelManager: React.FC<CitadelManagerProps> = ({
  planetId,
  playerCredits,
  stationedDrones,
  onUpdate,
}) => {
  const [citadel, setCitadel] = useState<CitadelInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState<number>(() => Date.now());
  const { getIcon, getLabel } = useResourceCatalog();

  const fetchCitadel = useCallback(async () => {
    try {
      setLoading(true);
      const data = await citadelAPI.getInfo(planetId);
      setCitadel(data);
      setError(null);
    } catch (err: unknown) {
      setError(formatCitadelLoadError(err));
    } finally {
      setLoading(false);
    }
  }, [planetId]);

  useEffect(() => {
    fetchCitadel();
  }, [fetchCitadel]);

  // Tick the construction countdown once a second while an upgrade is active;
  // refetch once the completion timestamp passes so the new level lights up.
  useEffect(() => {
    if (!citadel?.is_upgrading || !citadel.upgrade_complete_at) return;
    const completeMs = Date.parse(citadel.upgrade_complete_at);
    const interval = window.setInterval(() => {
      const now = Date.now();
      setNowMs(now);
      if (Number.isFinite(completeMs) && now >= completeMs) {
        window.clearInterval(interval);
        fetchCitadel();
      }
    }, 1000);
    return () => window.clearInterval(interval);
  }, [citadel?.is_upgrading, citadel?.upgrade_complete_at, fetchCitadel]);

  const handleUpgrade = async () => {
    if (!citadel || actionLoading) return;
    try {
      setActionLoading(true);
      setActionMessage(null);
      await citadelAPI.upgrade(planetId);
      setActionMessage('Citadel upgrade initiated!');
      await fetchCitadel();
      onUpdate?.();
    } catch (err: unknown) {
      setActionMessage(formatCitadelUpgradeError(err));
    } finally {
      setActionLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="citadel-manager citadel-loading">
        <div className="citadel-spinner" />
        <span>Loading citadel...</span>
      </div>
    );
  }

  if (error || !citadel) {
    return (
      <div className="citadel-manager citadel-error">
        <span>{error || 'Citadel unavailable'}</span>
        <button onClick={fetchCitadel} className="citadel-retry-btn">Retry</button>
      </div>
    );
  }

  const level = citadel.citadel_level ?? 0;
  const safeCapacity = citadel.safe_storage ?? 0;
  const safeCredits = citadel.safe_credits ?? 0;
  const storagePercent = safeCapacity > 0 ? (safeCredits / safeCapacity) * 100 : 0;

  // --- Planet-size ceiling ---
  // The citadel ladder is gated by the planet's surface area: small worlds
  // physically can't host the larger key buildings, so they cap below L5.
  // The API returns max_citadel_level (3–5); when absent, fall back to the
  // canon ceiling of 5 so older responses behave exactly as before.
  const sizeCap = citadel.max_citadel_level ?? 5;
  // A planet is AT its size ceiling when the next rung the server offers would
  // exceed what its size can host. next_level is purely "current + 1 if < 5",
  // so it can be populated even on a size-capped world — the cap check is what
  // turns that into a visible, intended ceiling rather than a confusing reject.
  const atSizeCap = (citadel.next_level?.level ?? level + 1) > sizeCap;

  // Hide the next-rung upgrade panel once the size ceiling is reached, even if
  // the server still reports a next_level (it doesn't know this planet's cap).
  const next = atSizeCap ? null : citadel.next_level;
  const upgradeCost = next?.upgrade_cost ?? 0;
  const canAffordUpgrade = playerCredits >= upgradeCost;

  // --- Upgrade-in-progress timing ---
  const upgradingToLevel = next?.level ?? (level < 5 ? level + 1 : null);
  const upgradingToName =
    next?.name ?? CITADEL_TRACK.find((s) => s.level === (level + 1))?.name ?? `Level ${level + 1}`;
  const upgradeStartMs = citadel.upgrade_started_at ? Date.parse(citadel.upgrade_started_at) : NaN;
  const upgradeEndMs = citadel.upgrade_complete_at ? Date.parse(citadel.upgrade_complete_at) : NaN;
  const upgradeRemainingMs = Number.isFinite(upgradeEndMs)
    ? Math.max(0, upgradeEndMs - nowMs)
    : typeof citadel.upgrade_remaining_seconds === 'number'
      ? Math.max(0, citadel.upgrade_remaining_seconds * 1000)
      : null;
  const upgradeProgressPct =
    Number.isFinite(upgradeStartMs) && Number.isFinite(upgradeEndMs) && upgradeEndMs > upgradeStartMs
      ? Math.min(100, Math.max(0, ((nowMs - upgradeStartMs) / (upgradeEndMs - upgradeStartMs)) * 100))
      : null;

  // --- Drone bay pips ---
  const droneCapacity = citadel.drone_capacity ?? 0;
  const pipCount = Math.min(droneCapacity, MAX_DRONE_PIPS);
  const dronesPerPip = pipCount > 0 ? droneCapacity / pipCount : 0;
  const hasStationedData = typeof stationedDrones === 'number';
  const filledPips =
    typeof stationedDrones === 'number' && dronesPerPip > 0
      ? Math.min(pipCount, Math.max(stationedDrones > 0 ? 1 : 0, Math.round(stationedDrones / dronesPerPip)))
      : 0;

  const upgradeDisabledReason = actionLoading
    ? 'Action in progress'
    : !canAffordUpgrade
      ? `Insufficient credits: need ${upgradeCost.toLocaleString()}, you have ${playerCredits.toLocaleString()}`
      : next && CITADEL_PREREQS[next.level]
        ? `${CITADEL_PREREQS[next.level]} (validated on upgrade)`
        : 'Begin citadel upgrade';

  return (
    <div className="citadel-manager">
      <div className="citadel-header">
        <h3>Citadel</h3>
        <span className="citadel-level-badge">Level {level}</span>
      </div>

      <div className="citadel-level-name">
        {citadel.citadel_name || (level === 0 ? 'No Citadel' : `Level ${level}`)}
      </div>

      {/* Planet-size ceiling — always visible so players understand why small
          worlds cap lower; it's intended, never a silent rejection. */}
      {citadel.max_citadel_level !== undefined && (
        <div
          className={`citadel-size-cap${atSizeCap ? ' at-cap' : ''}`}
          title={
            sizeCap < 5
              ? `This planet's surface area limits its citadel to Level ${sizeCap}. Larger worlds can build higher.`
              : `This planet is large enough to reach the maximum citadel Level ${sizeCap}.`
          }
        >
          <span className="size-cap-icon" aria-hidden="true">{atSizeCap ? '🛑' : '📐'}</span>
          <span className="size-cap-text">
            Max citadel for this planet size: <strong>L{sizeCap}</strong>
            {atSizeCap && <span className="size-cap-flag"> — ceiling reached</span>}
          </span>
        </div>
      )}

      {/* Citadel Structure Visualization — the city you can see grow */}
      <CitadelStructure
        level={level}
        isUpgrading={citadel.is_upgrading}
        upgradingToLevel={upgradingToLevel}
      />
      {level >= 1 && level <= 5 && (
        <div className="citadel-flavor">
          <span className="citadel-flavor-name">
            {CITADEL_TRACK.find((s) => s.level === level)?.name ?? citadel.citadel_name}
          </span>
          <span className="citadel-flavor-text">{CITADEL_FLAVOR[level]}</span>
        </div>
      )}

      {/* Upgrade Section — rendered ABOVE the read-only track/vault stats
          (WO-UI4-COLONY-CONSOLE) so the primary CTA clears the fold instead
          of sitting buried behind the ladder + vault gauge (Scroll Law). The
          level stays visible via the panel header's "Lv N" readout. */}
      {next && !citadel.is_upgrading && (
        <div className="citadel-upgrade">
          <div className="upgrade-info">
            <span className="upgrade-label">
              Upgrade to L{next.level} — {next.name}
            </span>
            <span className="upgrade-cost">
              {upgradeCost > 0 ? `${upgradeCost.toLocaleString()} credits` : 'Free'}
            </span>
            {next.upgrade_hours > 0 && (
              <span className="upgrade-time">{next.upgrade_hours}h build time</span>
            )}
            {next.resource_cost && Object.keys(next.resource_cost).length > 0 && (
              <span className="upgrade-resources">
                {Object.entries(next.resource_cost)
                  .map(([res, amt]) => `${getIcon(res)} ${getLabel(res)} ${amt.toLocaleString()}`)
                  .join(' · ')}
              </span>
            )}
            {CITADEL_PREREQS[next.level] && (
              <span className="upgrade-prereq">⚠ {CITADEL_PREREQS[next.level]}</span>
            )}
            <span className="upgrade-gains">
              Gains: 👥 {compact(next.max_population)} · 🔒 {compact(next.safe_storage)} · ✈️ {next.drone_capacity}
            </span>
          </div>
          <button
            onClick={handleUpgrade}
            disabled={actionLoading || !canAffordUpgrade}
            className="citadel-btn upgrade-btn"
            title={upgradeDisabledReason}
          >
            {!canAffordUpgrade ? 'Insufficient Credits' : 'Upgrade'}
          </button>
        </div>
      )}

      {citadel.is_upgrading && (
        <div className="citadel-upgrading">
          <div className="upgrading-header">
            <span className="upgrading-icon" aria-hidden="true">🏗️</span>
            <span className="upgrading-label">
              Constructing {upgradingToName}
              {upgradingToLevel !== null ? ` (L${upgradingToLevel})` : ''}
            </span>
            {upgradeRemainingMs !== null && (
              <span className="upgrading-countdown" title="Time until construction completes">
                {formatCountdown(upgradeRemainingMs)} remaining
              </span>
            )}
          </div>
          {upgradeProgressPct !== null && (
            <div
              className="upgrading-bar"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(upgradeProgressPct)}
              aria-label="Citadel construction progress"
            >
              <div className="upgrading-fill" style={{ width: `${upgradeProgressPct}%` }} />
            </div>
          )}
          {citadel.upgrade_complete_at && (
            <div className="upgrading-eta">
              Completes {new Date(citadel.upgrade_complete_at).toLocaleString()}
            </div>
          )}
        </div>
      )}

      {/* 5-Level Stepped Progression Track */}
      <div className="citadel-track" role="list" aria-label="Citadel progression">
        {CITADEL_TRACK.map((step) => {
          const state = step.level < level ? 'completed' : step.level === level ? 'current' : 'locked';
          const isNext = next?.level === step.level;
          // Steps above this planet's size ceiling are physically unreachable
          // here — render them dimmed so the ladder itself shows the cap.
          const beyondSizeCap = step.level > sizeCap;
          return (
            <div
              key={step.level}
              role="listitem"
              tabIndex={0}
              data-flavor={CITADEL_FLAVOR[step.level]}
              className={`citadel-step ${state} ${isNext ? 'next-up' : ''}${beyondSizeCap ? ' beyond-size-cap' : ''}`}
              title={
                beyondSizeCap
                  ? `L${step.level} ${step.name} — unreachable on this planet (size caps the citadel at L${sizeCap}). Build on a larger world to reach this level.`
                  : `L${step.level} ${step.name} — "${CITADEL_FLAVOR[step.level]}" Workforce cap ${step.maxColonists.toLocaleString()}, safe storage ${step.safeStorage.toLocaleString()} cr, ${step.droneCapacity} drones${CITADEL_PREREQS[step.level] ? `. ${CITADEL_PREREQS[step.level]}` : ''}`
              }
            >
              <div className="step-node">
                <span className="step-level">L{step.level}</span>
              </div>
              <div className="step-name">{step.name}</div>
              <div className="step-stats">
                <span className="step-stat" title={`Max colonists: ${step.maxColonists.toLocaleString()}`}>
                  👥 {compact(step.maxColonists)}
                </span>
                <span className="step-stat" title={`Safe storage: ${step.safeStorage.toLocaleString()} credits`}>
                  🔒 {compact(step.safeStorage)}
                </span>
                <span className="step-stat" title={`Drone capacity: ${step.droneCapacity}`}>
                  ✈️ {step.droneCapacity}
                </span>
              </div>
            </div>
          );
        })}
      </div>

      <div className="citadel-stats">
        <div className="citadel-stat">
          <span className="stat-label">Vault — Safe Storage</span>
          <div
            className="vault-gauge"
            role="meter"
            aria-valuemin={0}
            aria-valuemax={safeCapacity}
            aria-valuenow={safeCredits}
            aria-label={`Vault: ${safeCredits.toLocaleString()} of ${safeCapacity.toLocaleString()} credits secured`}
            title={`${storagePercent.toFixed(1)}% of vault capacity in use`}
          >
            <div
              className="vault-fill"
              style={{ width: `${Math.min(100, storagePercent)}%` }}
            />
            <div className="vault-segments" aria-hidden="true" />
          </div>
          <div className="vault-readout">
            <span className="vault-amount">{safeCredits.toLocaleString()}</span>
            <span className="vault-capacity">/ {safeCapacity.toLocaleString()} cr</span>
            <span className="vault-percent">{storagePercent.toFixed(1)}%</span>
          </div>
        </div>
        <div className="citadel-stat-row">
          <div className="citadel-stat">
            <span className="stat-label">Workforce Cap</span>
            <span className="stat-value">{(citadel.max_population ?? 0).toLocaleString()}</span>
          </div>
          <div className="citadel-stat">
            <span className="stat-label">Drone Bay</span>
            <div
              className="drone-bay"
              role="img"
              aria-label={
                hasStationedData
                  ? `${stationedDrones} of ${droneCapacity} drones stationed`
                  : `Drone bay capacity: ${droneCapacity}`
              }
              title={
                hasStationedData
                  ? `${stationedDrones} of ${droneCapacity} drones stationed${dronesPerPip > 1 ? ` (each pip ≈ ${Math.round(dronesPerPip)} drones)` : ''}`
                  : `Drone bay capacity: ${droneCapacity}${dronesPerPip > 1 ? ` (each pip ≈ ${Math.round(dronesPerPip)} drones)` : ''}`
              }
            >
              <div className="drone-pips" aria-hidden="true">
                {Array.from({ length: pipCount }, (_, i) => (
                  <span
                    key={i}
                    className={`drone-pip ${hasStationedData ? (i < filledPips ? 'filled' : 'empty') : 'capacity'}`}
                  />
                ))}
              </div>
              <span className="drone-count">
                {hasStationedData
                  ? `${stationedDrones} / ${droneCapacity}`
                  : `${droneCapacity} capacity`}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Credit deposit / withdraw now lives on the Safe tab's unified vault
          (SafeVaultPanel) alongside commodity store/take — one vault, one
          cr-equivalent cap. The gauge above stays as a read-only reference. */}

      {!next && !citadel.is_upgrading && (
        <div className={`citadel-max-level${atSizeCap && sizeCap < 5 ? ' size-capped' : ''}`}>
          {atSizeCap && sizeCap < 5 ? (
            <>
              <span className="max-level-title">Size Ceiling Reached — Level {sizeCap}</span>
              <span className="max-level-sub">
                This planet is too small to build beyond L{sizeCap}. Establish a
                citadel on a larger world to reach higher levels.
              </span>
            </>
          ) : (
            'Maximum Level Reached'
          )}
        </div>
      )}

      {actionMessage && (
        <div className="citadel-message">{actionMessage}</div>
      )}
    </div>
  );
};

export default CitadelManager;
