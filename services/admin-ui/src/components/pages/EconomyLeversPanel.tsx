import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../../utils/auth';
import { useToast } from '../../contexts/ToastContext';

interface RegionLever {
  id: string;
  name: string;
  display_name: string | null;
  tax_rate: number;
  starting_credits: number;
  status: string;
}

interface ShipSpecLever {
  type: string;
  base_cost: number;
  is_npc_only: boolean;
}

interface UpgradeLever {
  type: string;
  base_cost: number;
  cost_multiplier: number;
  description: string;
}

interface LeversSnapshot {
  regions: RegionLever[];
  ship_specs: ShipSpecLever[];
  upgrades: UpgradeLever[];
}

/**
 * Unified Economy Levers panel (lifecycle.md § Balancing levers).
 * WO-BUILD-ADMIN-UI-ECONOMY-LEVERS-PANEL — consolidates region tax/starting
 * credits, ship base costs, and upgrade cost defs that previously required
 * scattered DB edits.
 */
const EconomyLeversPanel: React.FC = () => {
  const toast = useToast();
  const [data, setData] = useState<LeversSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [regionDrafts, setRegionDrafts] = useState<Record<string, { tax_pct: string; starting_credits: string }>>({});
  const [shipDrafts, setShipDrafts] = useState<Record<string, string>>({});
  const [upgradeDrafts, setUpgradeDrafts] = useState<Record<string, { base_cost: string; cost_multiplier: string }>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get<LeversSnapshot>('/api/v1/admin/economy/levers');
      setData(res.data);
      const rDrafts: Record<string, { tax_pct: string; starting_credits: string }> = {};
      for (const r of res.data.regions) {
        rDrafts[r.id] = {
          tax_pct: (r.tax_rate * 100).toFixed(1),
          starting_credits: String(r.starting_credits),
        };
      }
      setRegionDrafts(rDrafts);
      const sDrafts: Record<string, string> = {};
      for (const s of res.data.ship_specs) {
        sDrafts[s.type] = String(s.base_cost);
      }
      setShipDrafts(sDrafts);
      const uDrafts: Record<string, { base_cost: string; cost_multiplier: string }> = {};
      for (const u of res.data.upgrades) {
        uDrafts[u.type] = {
          base_cost: String(u.base_cost),
          cost_multiplier: String(u.cost_multiplier),
        };
      }
      setUpgradeDrafts(uDrafts);
    } catch (err) {
      console.error(err);
      toast.error('Failed to load economy levers');
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void load();
  }, [load]);

  const saveRegion = async (regionId: string) => {
    const draft = regionDrafts[regionId];
    if (!draft) return;
    const tax_rate = parseFloat(draft.tax_pct) / 100;
    const starting_credits = parseInt(draft.starting_credits, 10);
    if (Number.isNaN(tax_rate) || tax_rate < 0.05 || tax_rate > 0.25) {
      toast.error('Tax rate must be 5–25%');
      return;
    }
    if (Number.isNaN(starting_credits) || starting_credits < 100 || starting_credits > 10000) {
      toast.error('Starting credits must be 100–10000');
      return;
    }
    setSaving(`region-${regionId}`);
    try {
      await api.patch(`/api/v1/admin/economy/levers/regions/${regionId}`, {
        tax_rate,
        starting_credits,
      });
      toast.success('Region levers saved');
      await load();
    } catch (err) {
      console.error(err);
      toast.error('Failed to save region levers');
    } finally {
      setSaving(null);
    }
  };

  const saveShip = async (shipType: string) => {
    const raw = shipDrafts[shipType];
    const base_cost = parseInt(raw, 10);
    if (Number.isNaN(base_cost) || base_cost < 1) {
      toast.error('Base cost must be a positive integer');
      return;
    }
    setSaving(`ship-${shipType}`);
    try {
      await api.patch(`/api/v1/admin/economy/levers/ship-specs/${shipType}`, { base_cost });
      toast.success(`${shipType} base cost saved`);
      await load();
    } catch (err) {
      console.error(err);
      toast.error('Failed to save ship cost');
    } finally {
      setSaving(null);
    }
  };

  const saveUpgrade = async (upgradeType: string) => {
    const draft = upgradeDrafts[upgradeType];
    if (!draft) return;
    const base_cost = parseInt(draft.base_cost, 10);
    const cost_multiplier = parseFloat(draft.cost_multiplier);
    if (Number.isNaN(base_cost) || base_cost < 1) {
      toast.error('Upgrade base cost must be positive');
      return;
    }
    if (Number.isNaN(cost_multiplier) || cost_multiplier < 1) {
      toast.error('Cost multiplier must be ≥ 1');
      return;
    }
    setSaving(`upgrade-${upgradeType}`);
    try {
      await api.patch(`/api/v1/admin/economy/levers/upgrades/${upgradeType}`, {
        base_cost,
        cost_multiplier,
      });
      toast.success(`${upgradeType} upgrade costs saved (in-process until restart)`);
      await load();
    } catch (err) {
      console.error(err);
      toast.error('Failed to save upgrade costs');
    } finally {
      setSaving(null);
    }
  };

  if (loading && !data) {
    return (
      <div className="levers-panel" data-testid="economy-levers-panel">
        <h3>Economy Levers</h3>
        <p className="health-empty">Loading levers…</p>
      </div>
    );
  }

  if (!data) {
    return null;
  }

  return (
    <div className="levers-panel" data-testid="economy-levers-panel">
      <div className="levers-header">
        <h3>Economy Levers</h3>
        <button type="button" className="refresh-btn" onClick={() => void load()} disabled={loading}>
          ↻ Refresh
        </button>
      </div>
      <p className="levers-blurb">
        Operator balancing controls from lifecycle.md — region tax / starting credits, ship purchase
        costs, and upgrade cost ladders. No code deploy required for region &amp; ship edits.
      </p>

      <h4>Regions</h4>
      <div className="levers-table-wrap">
        <table className="levers-table">
          <thead>
            <tr>
              <th>Region</th>
              <th>Tax % (5–25)</th>
              <th>Starting credits</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.regions.map((r) => (
              <tr key={r.id}>
                <td>
                  <strong>{r.display_name || r.name}</strong>
                  <div className="levers-meta">{r.status}</div>
                </td>
                <td>
                  <input
                    aria-label={`Tax rate for ${r.name}`}
                    type="number"
                    step="0.1"
                    min={5}
                    max={25}
                    value={regionDrafts[r.id]?.tax_pct ?? ''}
                    onChange={(e) =>
                      setRegionDrafts((prev) => ({
                        ...prev,
                        [r.id]: { ...prev[r.id], tax_pct: e.target.value },
                      }))
                    }
                  />
                </td>
                <td>
                  <input
                    aria-label={`Starting credits for ${r.name}`}
                    type="number"
                    min={100}
                    max={10000}
                    value={regionDrafts[r.id]?.starting_credits ?? ''}
                    onChange={(e) =>
                      setRegionDrafts((prev) => ({
                        ...prev,
                        [r.id]: { ...prev[r.id], starting_credits: e.target.value },
                      }))
                    }
                  />
                </td>
                <td>
                  <button
                    type="button"
                    className="action-btn inject"
                    disabled={saving === `region-${r.id}`}
                    onClick={() => void saveRegion(r.id)}
                  >
                    Save
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h4>Ship base costs</h4>
      <div className="levers-table-wrap">
        <table className="levers-table">
          <thead>
            <tr>
              <th>Type</th>
              <th>Base cost (cr)</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.ship_specs
              .filter((s) => !s.is_npc_only)
              .map((s) => (
                <tr key={s.type}>
                  <td>{s.type}</td>
                  <td>
                    <input
                      aria-label={`Base cost for ${s.type}`}
                      type="number"
                      min={1}
                      value={shipDrafts[s.type] ?? ''}
                      onChange={(e) =>
                        setShipDrafts((prev) => ({ ...prev, [s.type]: e.target.value }))
                      }
                    />
                  </td>
                  <td>
                    <button
                      type="button"
                      className="action-btn inject"
                      disabled={saving === `ship-${s.type}`}
                      onClick={() => void saveShip(s.type)}
                    >
                      Save
                    </button>
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>

      <h4>Upgrade definitions</h4>
      <p className="levers-meta">
        In-process overrides — revert on gameserver restart. Prefer a follow-up persistence WO for
        durable overrides.
      </p>
      <div className="levers-table-wrap">
        <table className="levers-table">
          <thead>
            <tr>
              <th>Upgrade</th>
              <th>Base cost</th>
              <th>Multiplier</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.upgrades.map((u) => (
              <tr key={u.type}>
                <td>
                  <strong>{u.type}</strong>
                  <div className="levers-meta">{u.description}</div>
                </td>
                <td>
                  <input
                    aria-label={`Base cost for upgrade ${u.type}`}
                    type="number"
                    min={1}
                    value={upgradeDrafts[u.type]?.base_cost ?? ''}
                    onChange={(e) =>
                      setUpgradeDrafts((prev) => ({
                        ...prev,
                        [u.type]: { ...prev[u.type], base_cost: e.target.value },
                      }))
                    }
                  />
                </td>
                <td>
                  <input
                    aria-label={`Cost multiplier for upgrade ${u.type}`}
                    type="number"
                    step="0.1"
                    min={1}
                    value={upgradeDrafts[u.type]?.cost_multiplier ?? ''}
                    onChange={(e) =>
                      setUpgradeDrafts((prev) => ({
                        ...prev,
                        [u.type]: { ...prev[u.type], cost_multiplier: e.target.value },
                      }))
                    }
                  />
                </td>
                <td>
                  <button
                    type="button"
                    className="action-btn inject"
                    disabled={saving === `upgrade-${u.type}`}
                    onClick={() => void saveUpgrade(u.type)}
                  >
                    Save
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default EconomyLeversPanel;
