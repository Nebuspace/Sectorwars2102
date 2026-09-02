import React, { useEffect, useState } from 'react';
import { stationSecurityAPI, type StationSecurityStatus } from '../../services/api';
import { formatStationSecurityError } from './StationSecurityMonitoringPane';
import './station-security-banner.css';

export const STATION_SECURITY_BANNER_LOAD_FALLBACK = 'Failed to load security tier';

/** Exported for TypeError/network honesty Vitest (LEG-3750). */
export function formatStationSecurityBannerLoadError(err: unknown): string {
  return formatStationSecurityError(err, STATION_SECURITY_BANNER_LOAD_FALLBACK);
}

export type StationSecurityTier = StationSecurityStatus['tier'];

/** Title-case a GS security tier slug for display. */
export function formatSecurityTierLabel(tier: string | null | undefined): string {
  if (!tier || tier === 'none') return 'None';
  return tier.charAt(0).toUpperCase() + tier.slice(1);
}

/** Human-readable banner line from GET /station-security/stations/{id}. */
export function formatStationSecurityBanner(status: StationSecurityStatus): string {
  const tierLabel = formatSecurityTierLabel(status.tier);
  if (status.pending_upgrade_to) {
    return `Security tier: ${tierLabel} (upgrading to ${formatSecurityTierLabel(status.pending_upgrade_to)})`;
  }
  if (status.pending_downgrade) {
    return `Security tier: ${tierLabel} (downgrade pending)`;
  }
  return `Security tier: ${tierLabel}`;
}

export interface StationSecurityBannerProps {
  stationId?: string | null;
}

const StationSecurityBanner: React.FC<StationSecurityBannerProps> = ({ stationId }) => {
  const [status, setStatus] = useState<StationSecurityStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (!stationId) {
      setStatus(null);
      setLoadError(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const next = await stationSecurityAPI.getSecurityStatus(stationId);
        if (!cancelled) {
          setStatus(next);
          setLoadError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setStatus(null);
          setLoadError(formatStationSecurityBannerLoadError(err));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [stationId]);

  if (loadError) {
    return (
      <span
        className="station-security-banner station-security-banner--error"
        data-testid="station-security-banner-error"
        role="alert"
      >
        {loadError}
      </span>
    );
  }

  if (!status) return null;

  const pending = Boolean(status.pending_upgrade_to || status.pending_downgrade);
  const label = formatStationSecurityBanner(status);

  return (
    <span
      className={`station-security-banner${pending ? ' station-security-banner--pending' : ''}`}
      data-testid="station-security-banner"
      title={label}
    >
      {label}
    </span>
  );
};

export default StationSecurityBanner;
