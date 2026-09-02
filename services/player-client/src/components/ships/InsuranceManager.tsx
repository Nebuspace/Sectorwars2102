import React, { useState, useEffect, useCallback } from 'react';
import { shipAPI } from '../../services/api';
import { formatCredits } from '../../utils/formatters';
import './insurance-manager.css';

// Canon shape (ADR-0081 premiums, ADR-0061 payout): 3 tiers, one-time premium,
// coverage attaches to the hull for life. No claims, no cancellation, no refunds.
interface TierInfo {
  tier: string;
  premium_pct: number;
  premium_full: number;
  net_payout_pct: number;
  payout_amount: number;
  upgrade_cost: number | null;
  purchasable: boolean;
}

interface InsuranceStatus {
  ship_id: string;
  ship_name: string;
  ship_type: string | null;
  insurable: boolean;
  current_tier: string;
  purchase_value: number;
  current_payout_amount: number;
  tiers: TierInfo[];
}

interface InsuranceManagerProps {
  shipId: string;
  playerCredits: number;
  onChanged?: () => void;
  onClose?: () => void;
}

// Shared with SpaceDockInterface's Services venue, which shows the held
// coverage tier inline (coverage attaches to the hull for life, so it's
// surfaced there independent of this station's underwriter availability).
export const TIER_LABEL: Record<string, string> = {
  NONE: 'Uninsured', BASIC: 'Basic', STANDARD: 'Standard', PREMIUM: 'Premium'
};

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

function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

/** True when err.message looks like gameserver detail (not bare API Error: N / TypeError noise). */
function hasInsuranceServerDetail(err: unknown, message: string | undefined): boolean {
  // Network collapse (fetch TypeError / axios transport) is not gameserver copy — use the caller fallback.
  if (err instanceof TypeError) return false;
  if (typeof message === 'string' && isNetworkCollapseMessage(message)) return false;
  return (
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim())
  );
}

/** Preserve gameserver detail on insurance status load refusal. */
export function formatInsuranceLoadError(err: unknown): string {
  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  const hasServerDetail = hasInsuranceServerDetail(err, message);

  if (status === 403) {
    if (hasServerDetail) return message!;
    return 'You do not have permission to view insurance for this ship.';
  }

  if (status === 429) {
    return 'Insurance rate limit exceeded — wait a moment and try again.';
  }

  if (hasServerDetail) return message!;
  return 'Insurance is unavailable right now.';
}

/** Preserve gameserver detail on insurance purchase/upgrade refusal. */
export function formatInsurancePurchaseError(err: unknown): string {
  if (err instanceof TypeError) return 'Purchase failed.';
  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  const hasServerDetail = hasInsuranceServerDetail(err, message);

  if (status === 403) {
    if (hasServerDetail) return message!;
    return 'You do not have permission to purchase insurance for this ship.';
  }

  if (status === 429) {
    return 'Insurance purchase rate limit exceeded — wait a moment and try again.';
  }

  if (hasServerDetail) return message!;
  return 'Purchase failed.';
}

const InsuranceManager: React.FC<InsuranceManagerProps> = ({ shipId, playerCredits, onChanged, onClose }) => {
  const [status, setStatus] = useState<InsuranceStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyTier, setBusyTier] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await shipAPI.getInsurance(shipId) as InsuranceStatus;
      setStatus(data);
      setLoadError(null);
    } catch (e) {
      setStatus(null);
      setLoadError(formatInsuranceLoadError(e));
    } finally {
      setLoading(false);
    }
  }, [shipId]);

  useEffect(() => { void load(); }, [load]);

  const buy = async (tier: string) => {
    setBusyTier(tier);
    setMsg(null);
    try {
      const res = await shipAPI.purchaseInsurance(shipId, tier) as { message?: string };
      setMsg({ kind: 'ok', text: res.message || `Insured at ${tier}.` });
      await load();
      onChanged?.();
    } catch (e) {
      setMsg({
        kind: 'err',
        text: formatInsurancePurchaseError(e),
      });
    } finally {
      setBusyTier(null);
    }
  };

  if (loading) {
    return <div className="insurance-manager"><p className="ins-note">Loading insurance…</p></div>;
  }
  if (!status) {
    return (
      <div className="insurance-manager">
        <div className="ins-header"><h3>Hull Insurance</h3>{onClose && <button className="ins-close" onClick={onClose}>✕</button>}</div>
        <p className="ins-error" role="alert" data-testid="ins-load-error">
          {loadError || 'Insurance is unavailable right now.'}
        </p>
      </div>
    );
  }

  return (
    <div className="insurance-manager">
      <div className="ins-header">
        <h3>Hull Insurance — {status.ship_name}</h3>
        {onClose && <button className="ins-close" onClick={onClose}>✕</button>}
      </div>

      {!status.insurable ? (
        <p className="ins-note">
          {(status.ship_type ?? 'These').replace(/_/g, ' ')} hulls are non-insurable — no policy can be written.
        </p>
      ) : (
        <>
          <div className="ins-current">
            <span>Current coverage:</span>
            <strong>{TIER_LABEL[status.current_tier] ?? status.current_tier}</strong>
            {status.current_tier !== 'NONE' && (
              <span className="ins-payout">pays out {formatCredits(status.current_payout_amount)}</span>
            )}
          </div>
          <p className="ins-note">
            Ship value {formatCredits(status.purchase_value)} · premium paid once, coverage lasts the hull's
            lifetime · no refunds, no claims, no cancellation.
          </p>

          {msg && (
            <div className={msg.kind === 'ok' ? 'ins-ok' : 'ins-error'} role="alert">{msg.text}</div>
          )}

          <div className="ins-tiers">
            {status.tiers.map(t => {
              const isCurrent = t.tier === status.current_tier;
              const afford = t.upgrade_cost !== null && t.upgrade_cost <= playerCredits;
              return (
                <div key={t.tier} className={`ins-tier-card ${isCurrent ? 'current' : ''}`}>
                  <div className="ins-tier-name">{TIER_LABEL[t.tier]}</div>
                  <div className="ins-tier-stat">
                    <span>Pays out</span>
                    <strong>{formatCredits(t.payout_amount)}</strong>
                    <em>({Math.round(t.net_payout_pct * 100)}%)</em>
                  </div>
                  <div className="ins-tier-stat">
                    <span>Premium</span>
                    <strong>{formatCredits(t.premium_full)}</strong>
                    <em>({Math.round(t.premium_pct * 100)}%)</em>
                  </div>
                  {isCurrent ? (
                    <div className="ins-current-badge">✓ Current</div>
                  ) : t.purchasable ? (
                    <button
                      className="ins-buy"
                      disabled={!afford || busyTier === t.tier}
                      onClick={() => buy(t.tier)}
                    >
                      {busyTier === t.tier
                        ? '…'
                        : status.current_tier === 'NONE'
                          ? `Insure · ${formatCredits(t.upgrade_cost!)}`
                          : `Upgrade · ${formatCredits(t.upgrade_cost!)}`}
                    </button>
                  ) : (
                    <div className="ins-owned-badge">Included</div>
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

export default InsuranceManager;
