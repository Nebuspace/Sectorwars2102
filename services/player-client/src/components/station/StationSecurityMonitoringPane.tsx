import React, { useCallback, useEffect, useState } from 'react';
import {
  stationSecurityAPI,
  type StationSecurityStatus,
} from '../../services/api';
import { formatCredits } from '../../utils/formatters';

/** Title-case a GS security tier slug for display. */
export function formatSecurityTierLabel(tier: string | null | undefined): string {
  if (!tier || tier === 'none') return 'None';
  return tier.charAt(0).toUpperCase() + tier.slice(1);
}

const TIER_ORDER = ['none', 'basic', 'standard', 'premium'] as const;

const nextTierUp = (tier: string): string | null => {
  const idx = TIER_ORDER.indexOf(tier as (typeof TIER_ORDER)[number]);
  if (idx < 0 || idx >= TIER_ORDER.length - 1) return null;
  return TIER_ORDER[idx + 1];
};

const nextTierDown = (tier: string): string | null => {
  const idx = TIER_ORDER.indexOf(tier as (typeof TIER_ORDER)[number]);
  if (idx <= 0) return null;
  return TIER_ORDER[idx - 1];
};

const UPGRADE_COST_HINT: Record<string, number> = {
  basic: 50_000,
  standard: 200_000,
  premium: 750_000,
};

/** Exported for TypeError densify tests — fetch/upgrade/downgrade catch paths use this. */
export function formatStationSecurityError(err: unknown, fallback: string): string {
  if (err instanceof TypeError) return fallback;
  if (err && typeof err === 'object') {
    const resp = (err as { response?: { data?: unknown } }).response;
    const data = resp?.data ?? (err as { data?: unknown }).data;
    if (data && typeof data === 'object') {
      const detail = (data as Record<string, unknown>).detail;
      if (typeof detail === 'string' && detail) return detail;
    }
    const msg = (err as { message?: string }).message;
    if (typeof msg === 'string' && msg) return msg;
  }
  return fallback;
}

const fmtCountdown = (iso: string | null | undefined, nowMs: number): string => {
  if (!iso) return '—';
  const target = Date.parse(iso);
  if (Number.isNaN(target)) return '—';
  let diff = Math.floor((target - nowMs) / 1000);
  if (diff <= 0) return 'completing…';
  const h = Math.floor(diff / 3600);
  diff %= 3600;
  const m = Math.floor(diff / 60);
  const s = diff % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
};

export interface StationSecurityMonitoringPaneProps {
  stationId: string;
  isOwner: boolean;
}

const StationSecurityMonitoringPane: React.FC<StationSecurityMonitoringPaneProps> = ({
  stationId,
  isOwner,
}) => {
  const [status, setStatus] = useState<StationSecurityStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<'upgrade' | 'downgrade' | null>(null);
  const [actionMsg, setActionMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const fetchStatus = useCallback(async () => {
    if (!isOwner || !stationId) return;
    setLoading(true);
    setError(null);
    try {
      const next = await stationSecurityAPI.getSecurityStatus(stationId);
      setStatus(next);
    } catch (e: unknown) {
      setStatus(null);
      setError(formatStationSecurityError(e, 'Failed to load security tier'));
    } finally {
      setLoading(false);
    }
  }, [isOwner, stationId]);

  useEffect(() => {
    void fetchStatus();
  }, [fetchStatus]);

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  if (!isOwner) return null;

  const tier = status?.tier ?? 'none';
  const pending =
    Boolean(status?.pending_upgrade_to) || Boolean(status?.pending_downgrade);
  const upgradeTarget = nextTierUp(tier);
  const downgradeTarget = nextTierDown(tier);
  const upgradeCost = upgradeTarget ? UPGRADE_COST_HINT[upgradeTarget] : null;

  const runTierChange = async (kind: 'upgrade' | 'downgrade') => {
    if (busy || pending) return;
    setBusy(kind);
    setActionMsg(null);
    try {
      const result =
        kind === 'upgrade'
          ? await stationSecurityAPI.upgradeSecurity(stationId)
          : await stationSecurityAPI.downgradeSecurity(stationId);
      setActionMsg({ ok: true, text: result.message });
      await fetchStatus();
    } catch (e: unknown) {
      setActionMsg({
        ok: false,
        text: formatStationSecurityError(e, `${kind} failed`),
      });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="po-section" data-testid="po-security-monitoring">
      <h3 className="po-section-title">🔒 Security Monitoring</h3>
      <p className="section-description">
        Station protection tier, upgrade ladder, and pending construction windows.
      </p>

      {loading && !status && <div className="catalog-loading">Loading security tier…</div>}
      {error && (
        <div className="genesis-error-message">
          <span className="error-icon">❌</span>
          {error}
          <button className="action-button" type="button" onClick={() => void fetchStatus()}>
            Retry
          </button>
        </div>
      )}

      {status && (
        <>
          <div className="po-tariff-current" data-testid="po-security-tier">
            Current tier: <strong>{formatSecurityTierLabel(tier)}</strong>
          </div>
          {status.pending_upgrade_to && (
            <div className="po-tariff-current" data-testid="po-security-pending-upgrade">
              Upgrading to {formatSecurityTierLabel(status.pending_upgrade_to)} —{' '}
              {fmtCountdown(status.upgrade_completes_at, now)}
            </div>
          )}
          {status.pending_downgrade && (
            <div className="po-tariff-current" data-testid="po-security-pending-downgrade">
              Downgrade in progress — {fmtCountdown(status.downgrade_completes_at, now)}
            </div>
          )}
          {typeof status.upkeep_collected === 'number' && status.upkeep_collected > 0 && (
            <div className="po-tariff-current">
              Upkeep collected: {formatCredits(status.upkeep_collected)}
            </div>
          )}

          <div className="po-defense-grid">
            <button
              type="button"
              className="action-button primary"
              data-testid="po-security-upgrade"
              disabled={Boolean(busy) || pending || !upgradeTarget}
              title={
                !upgradeTarget
                  ? 'Already at maximum tier'
                  : upgradeCost != null
                    ? `Upgrade to ${formatSecurityTierLabel(upgradeTarget)} (${formatCredits(upgradeCost)})`
                    : undefined
              }
              onClick={() => void runTierChange('upgrade')}
            >
              {busy === 'upgrade' ? 'Initiating…' : 'Upgrade tier'}
            </button>
            <button
              type="button"
              className="action-button"
              data-testid="po-security-downgrade"
              disabled={Boolean(busy) || pending || !downgradeTarget}
              title={!downgradeTarget ? 'No tier to downgrade from' : 'Free; completes after canon window'}
              onClick={() => void runTierChange('downgrade')}
            >
              {busy === 'downgrade' ? 'Initiating…' : 'Downgrade tier'}
            </button>
          </div>
        </>
      )}

      {actionMsg && (
        <div
          className={actionMsg.ok ? 'genesis-success-message' : 'genesis-error-message'}
          role="status"
          data-testid="po-security-action-msg"
        >
          {actionMsg.text}
        </div>
      )}
    </div>
  );
};

export default StationSecurityMonitoringPane;
