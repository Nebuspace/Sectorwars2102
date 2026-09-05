import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../../utils/auth';
import { useToast } from '../../contexts/ToastContext';
import { formatAdminApiError } from '../../utils/adminApiError';

const ECONOMY_LEVERS_SCOPE_HINT =
  'economy levers require the admin economy manage scope (ECONOMY_MANAGE)';

const economyLeversApiError = (err: unknown, fallback: string): string =>
  formatAdminApiError(err, {
    fallback,
    scopeHint: ECONOMY_LEVERS_SCOPE_HINT,
  });

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

interface StationCommodityLever {
  station_id: string;
  station_name: string;
  commodity: string;
  base_price: number;
  production_rate: number;
}

interface LeversSnapshot {
  regions: RegionLever[];
  ship_specs: ShipSpecLever[];
  upgrades: UpgradeLever[];
  bounty_payout_ratio: number;
  insurance_premium_pct: Record<string, number>;
  insurance_net_payout_pct: Record<string, number>;
  station_commodities: StationCommodityLever[];
}

const INSURANCE_TIERS = ['BASIC', 'STANDARD', 'PREMIUM'] as const;

/**
 * Unified Economy Levers panel (lifecycle.md § Balancing levers).
 * LEG-30 — adds bounty payout ratio, insurance premium/net-payout tiers,
 * and per-station commodity base_price / production_rate (backend already shipped).
 * Pause-production-tick intentionally omitted (design-only scheduler hook).
 */
const EconomyLeversPanel: React.FC = () => {
  const toast = useToast();
  const [data, setData] = useState<LeversSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [regionDrafts, setRegionDrafts] = useState<Record<string, { tax_pct: string; starting_credits: string }>>({});
  const [shipDrafts, setShipDrafts] = useState<Record<string, string>>({});
  const [upgradeDrafts, setUpgradeDrafts] = useState<Record<string, { base_cost: string; cost_multiplier: string }>>({});
  const [bountyRatioDraft, setBountyRatioDraft] = useState('1.0');
  const [premiumDrafts, setPremiumDrafts] = useState<Record<string, string>>({});
  const [payoutDrafts, setPayoutDrafts] = useState<Record<string, string>>({});
  const [commodityDrafts, setCommodityDrafts] = useState<
    Record<string, { base_price: string; production_rate: string }>
  >({});
  const [commodityFilter, setCommodityFilter] = useState('');

  const commodityKey = (row: StationCommodityLever) => `${row.station_id}::${row.commodity}`;

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
      setBountyRatioDraft(String(res.data.bounty_payout_ratio ?? 1));
      const prem: Record<string, string> = {};
      const pay: Record<string, string> = {};
      for (const tier of INSURANCE_TIERS) {
        prem[tier] = ((res.data.insurance_premium_pct?.[tier] ?? 0) * 100).toFixed(1);
        pay[tier] = ((res.data.insurance_net_payout_pct?.[tier] ?? 0) * 100).toFixed(1);
      }
      setPremiumDrafts(prem);
      setPayoutDrafts(pay);
      const cDrafts: Record<string, { base_price: string; production_rate: string }> = {};
      for (const row of res.data.station_commodities || []) {
        cDrafts[commodityKey(row)] = {
          base_price: String(row.base_price),
          production_rate: String(row.production_rate),
        };
      }
      setCommodityDrafts(cDrafts);
    } catch (err) {
      console.error(err);
      toast.error(
        economyLeversApiError(err, 'Gameserver unreachable — network error loading economy levers'),
      );
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void load();
  }, [load]);

  const filteredCommodities = useMemo(() => {
    if (!data?.station_commodities) return [];
    const q = commodityFilter.trim().toLowerCase();
    if (!q) return data.station_commodities;
    return data.station_commodities.filter(
      (row) =>
        row.station_name.toLowerCase().includes(q) ||
        row.commodity.toLowerCase().includes(q) ||
        row.station_id.toLowerCase().includes(q),
    );
  }, [data, commodityFilter]);

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
      toast.error(economyLeversApiError(err, 'Failed to save region levers'));
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
      toast.error(economyLeversApiError(err, 'Failed to save ship cost'));
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
      toast.error(economyLeversApiError(err, 'Failed to save upgrade costs'));
    } finally {
      setSaving(null);
    }
  };

  const saveBountyRatio = async () => {
    const bounty_payout_ratio = parseFloat(bountyRatioDraft);
    if (Number.isNaN(bounty_payout_ratio) || bounty_payout_ratio < 0 || bounty_payout_ratio > 5) {
      toast.error('Bounty payout ratio must be 0.0–5.0');
      return;
    }
    setSaving('bounty-ratio');
    try {
      await api.patch('/api/v1/admin/economy/levers/bounty-payout', { bounty_payout_ratio });
      toast.success('Bounty payout ratio saved (in-process until restart)');
      await load();
    } catch (err) {
      console.error(err);
      toast.error(economyLeversApiError(err, 'Failed to save bounty payout ratio'));
    } finally {
      setSaving(null);
    }
  };

  const saveInsurance = async () => {
    const insurance_premium_pct: Record<string, number> = {};
    const insurance_net_payout_pct: Record<string, number> = {};
    for (const tier of INSURANCE_TIERS) {
      const prem = parseFloat(premiumDrafts[tier] ?? '') / 100;
      const pay = parseFloat(payoutDrafts[tier] ?? '') / 100;
      if (Number.isNaN(prem) || prem < 0 || prem > 1) {
        toast.error(`${tier} premium must be 0–100%`);
        return;
      }
      if (Number.isNaN(pay) || pay < 0 || pay > 1) {
        toast.error(`${tier} net payout must be 0–100%`);
        return;
      }
      insurance_premium_pct[tier] = prem;
      insurance_net_payout_pct[tier] = pay;
    }
    setSaving('insurance');
    try {
      await api.patch('/api/v1/admin/economy/levers/insurance', {
        insurance_premium_pct,
        insurance_net_payout_pct,
      });
      toast.success('Insurance levers saved (in-process until restart)');
      await load();
    } catch (err) {
      console.error(err);
      toast.error(economyLeversApiError(err, 'Failed to save insurance levers'));
    } finally {
      setSaving(null);
    }
  };

  const saveCommodity = async (row: StationCommodityLever) => {
    const key = commodityKey(row);
    const draft = commodityDrafts[key];
    if (!draft) return;
    const base_price = parseInt(draft.base_price, 10);
    const production_rate = parseFloat(draft.production_rate);
    if (Number.isNaN(base_price) || base_price < 0) {
      toast.error('Base price must be ≥ 0');
      return;
    }
    if (Number.isNaN(production_rate) || production_rate < 0) {
      toast.error('Production rate must be ≥ 0');
      return;
    }
    setSaving(`commodity-${key}`);
    try {
      await api.patch(
        `/api/v1/admin/economy/levers/stations/${row.station_id}/commodities/${encodeURIComponent(row.commodity)}`,
        { base_price, production_rate },
      );
      toast.success(`${row.station_name} / ${row.commodity} saved`);
      await load();
    } catch (err) {
      console.error(err);
      toast.error(economyLeversApiError(err, 'Failed to save station commodity levers'));
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
        costs, upgrade ladders, bounty payout ratio, insurance tiers, and per-station commodity
        base price / production rate. Pause-production-tick is not exposed here (scheduler hook
        required).
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

      <h4>Bounty payout ratio</h4>
      <p className="levers-meta">
        Global faucet throttle applied when collecting bounty pots (0.0–5.0). In-process until
        restart.
      </p>
      <div className="levers-table-wrap">
        <table className="levers-table">
          <thead>
            <tr>
              <th>Ratio</th>
              <th />
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>
                <input
                  aria-label="Bounty payout ratio"
                  type="number"
                  step="0.05"
                  min={0}
                  max={5}
                  value={bountyRatioDraft}
                  onChange={(e) => setBountyRatioDraft(e.target.value)}
                />
              </td>
              <td>
                <button
                  type="button"
                  className="action-btn inject"
                  disabled={saving === 'bounty-ratio'}
                  onClick={() => void saveBountyRatio()}
                >
                  Save
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <h4>Insurance premiums &amp; net payout</h4>
      <p className="levers-meta">
        Tier fractions of purchase value (enter as percent). In-process until restart.
      </p>
      <div className="levers-table-wrap">
        <table className="levers-table">
          <thead>
            <tr>
              <th>Tier</th>
              <th>Premium %</th>
              <th>Net payout %</th>
            </tr>
          </thead>
          <tbody>
            {INSURANCE_TIERS.map((tier) => (
              <tr key={tier}>
                <td>{tier}</td>
                <td>
                  <input
                    aria-label={`${tier} insurance premium percent`}
                    type="number"
                    step="0.1"
                    min={0}
                    max={100}
                    value={premiumDrafts[tier] ?? ''}
                    onChange={(e) =>
                      setPremiumDrafts((prev) => ({ ...prev, [tier]: e.target.value }))
                    }
                  />
                </td>
                <td>
                  <input
                    aria-label={`${tier} insurance net payout percent`}
                    type="number"
                    step="0.1"
                    min={0}
                    max={100}
                    value={payoutDrafts[tier] ?? ''}
                    onChange={(e) =>
                      setPayoutDrafts((prev) => ({ ...prev, [tier]: e.target.value }))
                    }
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button
        type="button"
        className="action-btn inject"
        disabled={saving === 'insurance'}
        onClick={() => void saveInsurance()}
        style={{ marginBottom: '1.5rem' }}
      >
        Save insurance levers
      </button>

      <h4>Station commodity levers</h4>
      <p className="levers-meta">
        Persisted on station JSONB — base_price and production_rate per commodity stocked.
      </p>
      <label className="levers-meta" htmlFor="commodity-filter">
        Filter stations / commodities{' '}
        <input
          id="commodity-filter"
          type="search"
          value={commodityFilter}
          onChange={(e) => setCommodityFilter(e.target.value)}
          placeholder="station or commodity…"
          style={{ marginLeft: '0.5rem', minWidth: '16rem' }}
        />
      </label>
      <div className="levers-table-wrap">
        <table className="levers-table">
          <thead>
            <tr>
              <th>Station</th>
              <th>Commodity</th>
              <th>Base price</th>
              <th>Production rate</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {filteredCommodities.length === 0 ? (
              <tr>
                <td colSpan={5} className="levers-meta">
                  {commodityFilter.trim()
                    ? 'No commodity rows match.'
                    : 'No station commodity levers returned.'}
                </td>
              </tr>
            ) : (
              filteredCommodities.slice(0, 200).map((row) => {
                const key = commodityKey(row);
                return (
                  <tr key={key}>
                    <td>
                      <strong>{row.station_name}</strong>
                      <div className="levers-meta">{row.station_id.slice(0, 8)}…</div>
                    </td>
                    <td>{row.commodity}</td>
                    <td>
                      <input
                        aria-label={`Base price for ${row.station_name} ${row.commodity}`}
                        type="number"
                        min={0}
                        value={commodityDrafts[key]?.base_price ?? ''}
                        onChange={(e) =>
                          setCommodityDrafts((prev) => ({
                            ...prev,
                            [key]: { ...prev[key], base_price: e.target.value },
                          }))
                        }
                      />
                    </td>
                    <td>
                      <input
                        aria-label={`Production rate for ${row.station_name} ${row.commodity}`}
                        type="number"
                        step="0.01"
                        min={0}
                        value={commodityDrafts[key]?.production_rate ?? ''}
                        onChange={(e) =>
                          setCommodityDrafts((prev) => ({
                            ...prev,
                            [key]: { ...prev[key], production_rate: e.target.value },
                          }))
                        }
                      />
                    </td>
                    <td>
                      <button
                        type="button"
                        className="action-btn inject"
                        disabled={saving === `commodity-${key}`}
                        onClick={() => void saveCommodity(row)}
                      >
                        Save
                      </button>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      {filteredCommodities.length > 200 && (
        <p className="levers-meta">
          Showing first 200 of {filteredCommodities.length} rows — narrow the filter to edit more.
        </p>
      )}
    </div>
  );
};

export default EconomyLeversPanel;
