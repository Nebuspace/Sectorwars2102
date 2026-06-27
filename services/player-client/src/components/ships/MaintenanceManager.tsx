import React, { useState, useEffect, useCallback } from 'react';
import { shipAPI } from '../../services/api';
import { formatCredits } from '../../utils/formatters';
import './maintenance-manager.css';

// Canon shape (ships.md): condition 0-100 decays per real day by hull class and
// drives a performance band. v1 applies the combat band; speed/fuel/failure are
// surfaced honestly as not-yet-active.
interface RepairOption {
  tier: string;
  cost_pct_per_10: number;
  cost_to_full: number;
  available: boolean;
}
interface MaintenanceStatus {
  ship_id: string;
  ship_name: string;
  condition: number;
  decay_pct_per_day: number;
  band: {
    tier: string;
    speed_pct: number;
    combat_pct: number;
    fuel_pct: number;
    failure_pct: number;
    failure_tier: string | null;
  };
  applied_effects: string[];
  repair_options: RepairOption[];
}

interface MaintenanceManagerProps {
  shipId: string;
  playerCredits: number;
  onChanged?: () => void;
  onClose?: () => void;
}

const TIER_LABEL: Record<string, string> = { basic: 'Basic', emergency: 'Emergency', premium: 'Premium' };
const TIER_NOTE: Record<string, string> = {
  basic: 'Any shipyard · 6h',
  emergency: 'Any shipyard, even mid-failure · 2h',
  premium: 'SpaceDock only · 1h · +2% temp buff',
};

const fmtPct = (n: number) => `${n > 0 ? '+' : ''}${n}%`;

const MaintenanceManager: React.FC<MaintenanceManagerProps> = ({ shipId, playerCredits, onChanged, onClose }) => {
  const [status, setStatus] = useState<MaintenanceStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyTier, setBusyTier] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await shipAPI.getMaintenanceStatus(shipId) as MaintenanceStatus;
      setStatus(data);
    } catch (e) {
      setMsg({ kind: 'err', text: e instanceof Error ? e.message : 'Failed to load maintenance.' });
    } finally {
      setLoading(false);
    }
  }, [shipId]);

  useEffect(() => { void load(); }, [load]);

  const repair = async (tier: string) => {
    setBusyTier(tier);
    setMsg(null);
    try {
      const res = await shipAPI.repairMaintenance(shipId, tier) as { message?: string };
      setMsg({ kind: 'ok', text: res.message || 'Ship serviced.' });
      await load();
      onChanged?.();
    } catch (e) {
      setMsg({ kind: 'err', text: e instanceof Error ? e.message : 'Servicing failed.' });
    } finally {
      setBusyTier(null);
    }
  };

  if (loading) return <div className="maintenance-manager"><p className="mnt-note">Loading maintenance…</p></div>;
  if (!status) {
    return (
      <div className="maintenance-manager">
        <div className="mnt-header"><h3>Ship Maintenance</h3>{onClose && <button className="mnt-close" onClick={onClose}>✕</button>}</div>
        <p className="mnt-error">Maintenance data is unavailable.</p>
      </div>
    );
  }

  const c = status.condition;
  const barClass = c >= 75 ? 'good' : c >= 50 ? 'worn' : c >= 25 ? 'degraded' : 'critical';

  return (
    <div className="maintenance-manager">
      <div className="mnt-header">
        <h3>Ship Maintenance — {status.ship_name}</h3>
        {onClose && <button className="mnt-close" onClick={onClose}>✕</button>}
      </div>

      <div className="mnt-condition">
        <div className="mnt-cond-top">
          <span>Condition</span>
          <strong>{c.toFixed(1)}%</strong>
          <span className={`mnt-tier ${barClass}`}>{status.band.tier}</span>
        </div>
        <div className="mnt-bar-track">
          <div className={`mnt-bar-fill ${barClass}`} style={{ width: `${Math.max(0, Math.min(100, c))}%` }} />
        </div>
        <p className="mnt-note">Decays {status.decay_pct_per_day}%/day for this hull class. Service it to restore condition.</p>
      </div>

      <div className="mnt-effects">
        <div className={`mnt-effect ${status.applied_effects.includes('combat') ? 'active' : ''}`}>
          <span>Combat effectiveness</span>
          <strong>{fmtPct(status.band.combat_pct)}</strong>
          <em>{status.applied_effects.includes('combat') ? 'active' : 'not yet active'}</em>
        </div>
        <div className="mnt-effect">
          <span>Speed</span><strong>{fmtPct(status.band.speed_pct)}</strong><em>not yet active</em>
        </div>
        <div className="mnt-effect">
          <span>Fuel use</span><strong>{fmtPct(status.band.fuel_pct)}</strong><em>not yet active</em>
        </div>
        <div className="mnt-effect">
          <span>Jump failure risk</span>
          <strong>{status.band.failure_pct}%</strong>
          <em>{status.band.failure_tier ? `${status.band.failure_tier.toLowerCase()} · not yet active` : 'none'}</em>
        </div>
      </div>

      {msg && <div className={msg.kind === 'ok' ? 'mnt-ok' : 'mnt-error'} role="alert">{msg.text}</div>}

      <div className="mnt-repair">
        <h4>Service (restore to 100%)</h4>
        <div className="mnt-tiers">
          {status.repair_options.map(opt => {
            const afford = opt.cost_to_full <= playerCredits;
            const disabled = !opt.available || opt.cost_to_full <= 0 || !afford || busyTier === opt.tier || c >= 99.95;
            return (
              <div key={opt.tier} className={`mnt-tier-card ${!opt.available ? 'unavailable' : ''}`}>
                <div className="mnt-tier-name">{TIER_LABEL[opt.tier] ?? opt.tier}</div>
                <div className="mnt-tier-note">{TIER_NOTE[opt.tier]}</div>
                <div className="mnt-tier-cost">{formatCredits(opt.cost_to_full)}</div>
                {opt.available ? (
                  <button className="mnt-buy" disabled={disabled} onClick={() => repair(opt.tier)}>
                    {busyTier === opt.tier ? '…' : c >= 99.95 ? 'Pristine' : afford ? 'Service' : 'Too costly'}
                  </button>
                ) : (
                  <div className="mnt-unavail">SpaceDock only</div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

export default MaintenanceManager;
