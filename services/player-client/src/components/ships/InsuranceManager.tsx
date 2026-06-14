import React, { useState, useEffect, useCallback } from 'react';
import { shipAPI } from '../../services/api';
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

const TIER_LABEL: Record<string, string> = {
  NONE: 'Uninsured', BASIC: 'Basic', STANDARD: 'Standard', PREMIUM: 'Premium'
};

const InsuranceManager: React.FC<InsuranceManagerProps> = ({ shipId, playerCredits, onChanged, onClose }) => {
  const [status, setStatus] = useState<InsuranceStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyTier, setBusyTier] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await shipAPI.getInsurance(shipId) as InsuranceStatus;
      setStatus(data);
    } catch (e) {
      setMsg({ kind: 'err', text: e instanceof Error ? e.message : 'Failed to load insurance.' });
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
      setMsg({ kind: 'err', text: e instanceof Error ? e.message : 'Purchase failed.' });
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
        <p className="ins-error">Insurance is unavailable right now.</p>
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
              <span className="ins-payout">pays out {status.current_payout_amount.toLocaleString()} cr</span>
            )}
          </div>
          <p className="ins-note">
            Ship value {status.purchase_value.toLocaleString()} cr · premium paid once, coverage lasts the hull's
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
                    <strong>{t.payout_amount.toLocaleString()} cr</strong>
                    <em>({Math.round(t.net_payout_pct * 100)}%)</em>
                  </div>
                  <div className="ins-tier-stat">
                    <span>Premium</span>
                    <strong>{t.premium_full.toLocaleString()} cr</strong>
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
                          ? `Insure · ${t.upgrade_cost!.toLocaleString()} cr`
                          : `Upgrade · ${t.upgrade_cost!.toLocaleString()} cr`}
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
