import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { shipUpgradeAPI } from '../../services/api';
import { formatCredits } from '../../utils/formatters';
import './module-grid-interface.css';

// ============================================================================
// SHIP-MODS slot-grid UI (WO-SM-5, Phase-A — NO adjacency highlight yet).
//
// Renders the ship's module_slots lattice as a cols×rows grid, lets the player
// fit/strip modules against the 3 live SHIP-MODS routes:
//   GET  /ships/{id}/modules          → lattice + installed (+ applied cosmetics)
//   POST /ships/{id}/modules/install  → fit (slot_index, module_class, tier)
//   POST /ships/{id}/modules/remove   → strip (slot_index, ~25% salvage refund)
//   GET  /ships/{id}/cosmetics        → Citizen cosmetic catalog SSOT (WO-WIRE-GET-COSMETICS)
//   POST /ships/{id}/cosmetics        → apply/clear a cosmetic slot
//
// There is NO public catalog route for module FAMILIES (the /ships/catalog
// route serves the legacy UpgradeType hull catalog, not these modules), so the
// installable catalog is encoded CLIENT-SIDE below, mirroring the gameserver's
// ship_upgrade_service._MODULE_FAMILIES 1:1 (classes, base costs, hull gates,
// the deferred mining family; lander/tractor are live). Cost per tier is computed with the same
// curve the server uses (base × MODULE_TIER_COST_MULT^(tier-1)) so the price
// shown == the price the server charges; the server remains the source of truth
// and rejects anything the client mis-derives. Cosmetics are NOT mirrored —
// their catalog comes from GET /cosmetics.
// ============================================================================

// --- server contract types (GET /ships/{id}/modules) ---
interface ModuleSlot {
  i: number;
  x: number;
  y: number;
  super: boolean;
  class: string | null; // class-lock (e.g. "combat" / "fleet"); null = open
  requires: string | null; // Citizen/faction seam; null = open
}

interface ModuleSlots {
  v: number;
  cols: number;
  rows: number;
  slots: ModuleSlot[];
}

interface InstalledModule {
  class: string;
  tier: number;
  super_at_install: boolean;
  installed_at: string;
}

interface ModulesResponse {
  ship_id: string;
  ship_name: string;
  ship_type: string | null;
  module_slots: ModuleSlots | null;
  installed: Record<string, InstalledModule>;
  // WO-GC-B: the Citizen cosmetic overlay (applied values, outside `installed`)
  // + live membership status (greys the skin + label when lapsed).
  cosmetics?: Record<string, string>;
  is_galactic_citizen?: boolean;
}

// --- Galactic-Citizen L1 cosmetic catalog (from GET /ships/{id}/cosmetics) ---
// Zero-stat overlays; catalog + membership gate live on the server.
interface CosmeticCatalogEntry {
  slot: string;
  label: string;
  values: string[];
}

type CosmeticsCatalogResponse = {
  success?: boolean;
  catalog?: Record<string, { label?: string; values?: string[] }>;
  applied?: Record<string, string>;
  is_galactic_citizen?: boolean;
};

/** Map server CITIZEN_COSMETICS-shaped catalog → picker rows. */
const mapCosmeticsCatalog = (
  catalog: CosmeticsCatalogResponse['catalog'],
): CosmeticCatalogEntry[] => {
  if (!catalog || typeof catalog !== 'object') return [];
  return Object.entries(catalog)
    .map(([slot, entry]) => ({
      slot,
      label: (entry && typeof entry.label === 'string' && entry.label) || slot,
      values: Array.isArray(entry?.values) ? entry.values.filter((v) => typeof v === 'string') : [],
    }))
    .filter((row) => row.values.length > 0);
};

// --- client-side module catalog (mirrors ship_upgrade_service._MODULE_FAMILIES) ---
interface ModuleFamily {
  cls: string;
  name: string;
  description: string;
  icon: string;
  baseCost: number;
  // hull gate: null = open to all hulls; otherwise the ShipType .value strings
  compatibleShips: string[] | null;
  // which class-locked slot accepts this module (null = only fits open slots)
  slotClass: string | null;
  // deferred equipment family: catalog-listed but install is server-rejected
  // (consumer_inert) until its runtime consumer is wired — shown "coming soon".
  deferred?: boolean;
}

// Server tier curve: cost × 2.2^(tier-1), int-rounded. (Effect ×1.6^(tier-1)
// is server-side; the client only needs the displayed cost to match the charge.)
const MODULE_TIER_COST_MULT = 2.2;
const MODULE_MAX_TIER = 3;
const TIER_LABEL: Record<number, string> = { 1: 'Mk I', 2: 'Mk II', 3: 'Mk III' };
const tierCost = (baseCost: number, tier: number): number =>
  Math.round(baseCost * Math.pow(MODULE_TIER_COST_MULT, tier - 1));

const MODULE_FAMILIES: ModuleFamily[] = [
  { cls: 'engine', name: 'Engine Module', icon: '🚀', baseCost: 5000, compatibleShips: null, slotClass: null,
    description: 'Improves ship speed (and shortens the Warp Jumper post-jump cooldown).' },
  { cls: 'shield', name: 'Shield Module', icon: '🛡️', baseCost: 8000, compatibleShips: null, slotClass: null,
    description: 'Increases max shields.' },
  { cls: 'hull', name: 'Hull Module', icon: '🔧', baseCost: 7000, compatibleShips: null, slotClass: null,
    description: 'Increases hull points.' },
  { cls: 'sensor', name: 'Sensor Module', icon: '📡', baseCost: 6000, compatibleShips: null, slotClass: null,
    description: 'Increases evasion and scanner range.' },
  { cls: 'maintenance', name: 'Maintenance Module', icon: '🔩', baseCost: 6000, compatibleShips: null, slotClass: null,
    description: 'Reduces mechanical failure rate.' },
  { cls: 'genesis', name: 'Genesis Containment Module', icon: '🌍', baseCost: 15000, slotClass: null,
    compatibleShips: ['CARGO_HAULER', 'COLONY_SHIP', 'DEFENDER', 'CARRIER', 'WARP_JUMPER'],
    description: 'Increases genesis-device capacity (genesis-capable hulls only).' },
  { cls: 'cargo', name: 'Cargo Module', icon: '📦', baseCost: 3000, compatibleShips: null, slotClass: null,
    description: 'Increases cargo capacity.' },
  { cls: 'drone', name: 'Drone Bay Module', icon: '🤖', baseCost: 10000, compatibleShips: null, slotClass: null,
    description: 'Increases drone capacity.' },
  // Live after WO residual (2): server get_passive_income reads modules._baked.
  // baseCost lockstep with ship_upgrade_service MODULE_DEFINITIONS harvester (50k).
  { cls: 'harvester', name: 'Quantum Harvester Module', icon: '⚡', baseCost: 50000, slotClass: null,
    compatibleShips: ['SCOUT_SHIP', 'FAST_COURIER', 'DEFENDER', 'WARP_JUMPER'],
    description: 'Harvests quantum particles for passive income.' },
  // Live after WO-BUILD-LANDER-MINING-TRACTOR-CONSUMER-WIRING (server install +
  // effects live). Client deferred flags flipped WO-WIRE-LANDER-TRACTOR-CATALOG-UNLOCK.
  { cls: 'lander', name: 'Planetary Lander Module', icon: '🛬', baseCost: 20000, slotClass: null,
    compatibleShips: ['COLONY_SHIP', 'LIGHT_FREIGHTER', 'CARGO_HAULER'],
    description: 'Improves planet-landing interaction.' },
  // mining STAYS deferred — server still consumer_inert (see ship_upgrade_service).
  { cls: 'mining', name: 'Mining Laser Module', icon: '⛏️', baseCost: 35000, deferred: true, slotClass: null,
    compatibleShips: ['CARGO_HAULER', 'COLONY_SHIP', 'DEFENDER'],
    description: 'Enables direct asteroid mining.' },
  { cls: 'tractor', name: 'Tractor Beam Module', icon: '🧲', baseCost: 40000, slotClass: 'combat',
    compatibleShips: ['CARGO_HAULER', 'DEFENDER', 'CARRIER', 'WARP_JUMPER'],
    description: 'Dual-use tractor: combat escape-denial (no damage) + ship-tow rig.' },
];

const FAMILY_BY_CLASS: Record<string, ModuleFamily> = MODULE_FAMILIES.reduce(
  (acc, f) => { acc[f.cls] = f; return acc; },
  {} as Record<string, ModuleFamily>,
);

const familyName = (cls: string): string => FAMILY_BY_CLASS[cls]?.name ?? cls;
const familyIcon = (cls: string): string => FAMILY_BY_CLASS[cls]?.icon ?? '🔩';

interface ModuleGridInterfaceProps {
  ship: { id: string };
  playerCredits?: number;
  onChanged?: () => void;
}

// A pending action on a specific slot (drives the catalog drawer / remove modal).
type SlotAction =
  | { kind: 'install'; slot: ModuleSlot }
  | { kind: 'remove'; slotIndex: number; installed: InstalledModule };

/** GS `_MODULE_PREVIEW_EFFECT_KEYS` labels — display only; values come from preview API. */
const PREVIEW_EFFECT_LABELS: Record<string, string> = {
  speed_bonus: 'Speed',
  cargo_bonus_percent: 'Cargo %',
  shield_bonus: 'Shield',
  hull_bonus: 'Hull',
  evasion_bonus_percent: 'Evasion %',
  scanner_range_bonus: 'Scanner range',
  drone_capacity_bonus: 'Drone capacity',
  genesis_capacity_bonus: 'Genesis capacity',
  failure_rate_reduction: 'Failure rate ↓',
  landing_bonus: 'Landing',
  passive_income: 'Passive income',
  mining_efficiency: 'Mining efficiency',
};

type ModulePreviewPayload = {
  success?: boolean;
  candidate?: { class?: string; tier?: number; name?: string };
  current?: Record<string, number>;
  projected?: Record<string, number>;
  delta?: Record<string, number>;
  message?: string;
};

const ModuleGridInterface: React.FC<ModuleGridInterfaceProps> = ({ ship, playerCredits, onChanged }) => {
  const [data, setData] = useState<ModulesResponse | null>(null);
  const [cosmeticCatalog, setCosmeticCatalog] = useState<CosmeticCatalogEntry[]>([]);
  const [credits, setCredits] = useState<number>(playerCredits ?? 0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [action, setAction] = useState<SlotAction | null>(null);
  const [lastAction, setLastAction] = useState<number>(0);
  const [preview, setPreview] = useState<ModulePreviewPayload | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewKey, setPreviewKey] = useState<string | null>(null);
  const previewReqRef = React.useRef(0);

  const RATE_LIMIT_MS = 1000;
  const canPerformAction = useCallback(() => {
    const now = Date.now();
    if (now - lastAction < RATE_LIMIT_MS) return false;
    setLastAction(now);
    return true;
  }, [lastAction]);

  const fetchModules = useCallback(async () => {
    if (!ship?.id) return;
    try {
      setLoading(true);
      // Modules lattice is required; cosmetics catalog is best-effort SSOT
      // (picker stays empty if the cosmetics GET fails — never invent values).
      const [result, cosmeticsRes] = await Promise.all([
        shipUpgradeAPI.getModules(ship.id) as Promise<ModulesResponse>,
        shipUpgradeAPI.getCosmetics(ship.id).catch(() => null) as Promise<CosmeticsCatalogResponse | null>,
      ]);
      setData(result);
      setCosmeticCatalog(mapCosmeticsCatalog(cosmeticsRes?.catalog));
      setError(null);
    } catch (err: any) {
      setError(err.message || 'Failed to load module data');
    } finally {
      setLoading(false);
    }
  }, [ship?.id]);

  useEffect(() => { fetchModules(); }, [fetchModules]);
  useEffect(() => { if (playerCredits !== undefined) setCredits(playerCredits); }, [playerCredits]);

  const shipType = data?.ship_type ?? null;
  const isWarpJumper = shipType === 'WARP_JUMPER';

  // Modules legally fittable into a given slot for THIS hull, excluding deferred
  // families (those render disabled "coming soon" in the drawer, not as choices):
  //   - hull gate: compatibleShips null (open) OR includes this ship_type
  //   - slot-class: a class-locked slot only accepts its own slot_class; an open
  //     (class:null) slot accepts any module whose slot_class is null
  const candidatesForSlot = useCallback(
    (slot: ModuleSlot): ModuleFamily[] => {
      return MODULE_FAMILIES.filter((fam) => {
        if (slot.class !== null) return fam.slotClass === slot.class; // class-locked slot
        return fam.slotClass === null; // open slot only takes open-class modules
      }).filter((fam) => {
        if (fam.compatibleShips === null) return true;
        return shipType !== null && fam.compatibleShips.includes(shipType);
      });
    },
    [shipType],
  );

  const clearPreview = useCallback(() => {
    previewReqRef.current += 1;
    setPreview(null);
    setPreviewError(null);
    setPreviewLoading(false);
    setPreviewKey(null);
  }, []);

  const closeAction = useCallback(() => {
    clearPreview();
    setAction(null);
  }, [clearPreview]);

  /** LEG-1095: GS dry-run before/after — invent=0 (no client MODULE_DEFINITIONS bake). */
  const requestPreview = useCallback(
    async (slotIndex: number, cls: string, tier: number) => {
      if (!ship?.id) return;
      const key = `${slotIndex}:${cls}:${tier}`;
      const reqId = ++previewReqRef.current;
      setPreviewKey(key);
      setPreviewLoading(true);
      setPreviewError(null);
      try {
        const result = (await shipUpgradeAPI.previewModule(
          ship.id,
          slotIndex,
          cls,
          tier,
        )) as ModulePreviewPayload;
        if (reqId !== previewReqRef.current) return;
        if (result?.success === false) {
          setPreview(null);
          setPreviewError(result.message || 'Preview unavailable');
          return;
        }
        setPreview(result);
      } catch (err: any) {
        if (reqId !== previewReqRef.current) return;
        setPreview(null);
        setPreviewError(err?.message || 'Preview failed');
      } finally {
        if (reqId === previewReqRef.current) setPreviewLoading(false);
      }
    },
    [ship?.id],
  );

  const handleInstall = async (slotIndex: number, cls: string, tier: number) => {
    if (!canPerformAction() || actionLoading || !ship?.id) return;
    try {
      setActionLoading(true);
      setActionMessage(null);
      const result = await shipUpgradeAPI.installModule(ship.id, slotIndex, cls, tier);
      if (result.success) {
        setActionMessage(result.message || `${familyName(cls)} ${TIER_LABEL[tier]} installed.`);
        if (typeof result.remaining_credits === 'number') setCredits(result.remaining_credits);
        clearPreview();
        setAction(null);
        await fetchModules();
        onChanged?.();
      } else {
        // Includes the deferred "coming soon" rejection (consumer_inert) — but the
        // drawer already disables those, so this is a defensive surface.
        setActionMessage(result.message || 'Install failed');
      }
    } catch (err: any) {
      setActionMessage(err.message || 'Install failed');
    } finally {
      setActionLoading(false);
    }
  };

  const handleRemove = async (slotIndex: number) => {
    if (!canPerformAction() || actionLoading || !ship?.id) return;
    try {
      setActionLoading(true);
      setActionMessage(null);
      const result = await shipUpgradeAPI.removeModule(ship.id, slotIndex);
      if (result.success) {
        setActionMessage(result.message || `Module stripped (salvage refund ${formatCredits(result.refund ?? 0)}).`);
        if (typeof result.remaining_credits === 'number') setCredits(result.remaining_credits);
        setAction(null);
        await fetchModules();
        onChanged?.();
      } else {
        setActionMessage(result.message || 'Remove failed');
      }
    } catch (err: any) {
      setActionMessage(err.message || 'Remove failed');
    } finally {
      setActionLoading(false);
    }
  };

  // WO-GC-B: apply/clear a Citizen cosmetic overlay (server gates on membership).
  const handleSetCosmetic = async (slot: string, value: string | null) => {
    if (!canPerformAction() || actionLoading || !ship?.id) return;
    try {
      setActionLoading(true);
      setActionMessage(null);
      const result = await shipUpgradeAPI.setCosmetic(ship.id, slot, value);
      if (result.success) {
        setActionMessage(result.message || 'Cosmetic updated.');
        await fetchModules();
        onChanged?.();
      } else {
        setActionMessage(result.message || 'Cosmetic update failed');
      }
    } catch (err: any) {
      setActionMessage(err?.message || 'Cosmetic update failed');
    } finally {
      setActionLoading(false);
    }
  };

  // CSS grid template from the lattice dimensions (cols×rows).
  const gridStyle = useMemo<React.CSSProperties>(() => {
    const cols = data?.module_slots?.cols ?? 0;
    return cols > 0 ? { gridTemplateColumns: `repeat(${cols}, minmax(120px, 1fr))` } : {};
  }, [data?.module_slots?.cols]);

  if (loading) {
    return (
      <div className="module-grid-interface">
        <div className="mgi-header"><h3>Module Bay</h3></div>
        <div className="mgi-loading">Reading the slot lattice...</div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="module-grid-interface">
        <div className="mgi-header"><h3>Module Bay</h3></div>
        <div className="mgi-error">
          <span>{error || 'Module data unavailable'}</span>
          <button onClick={fetchModules} className="mgi-retry-btn">Retry</button>
        </div>
      </div>
    );
  }

  const slots = data.module_slots?.slots ?? [];

  // WO-GC-B: cosmetic overlay + live membership. Cosmetics render as wrapper
  // classes (CSS-driven skin/glow); a lapsed member's applied skin greys out.
  const cosmetics = data.cosmetics ?? {};
  const isCitizen = !!data.is_galactic_citizen;
  const hasCosmetics = Object.keys(cosmetics).length > 0;
  const wrapperClass = [
    'module-grid-interface',
    cosmetics.frame ? `gc-frame-${cosmetics.frame}` : '',
    cosmetics.slot_glow ? `gc-glow-${cosmetics.slot_glow}` : '',
    cosmetics.crest ? 'gc-has-crest' : '',
    hasCosmetics && !isCitizen ? 'gc-lapsed' : '',
  ].filter(Boolean).join(' ');

  return (
    <div className={wrapperClass} data-gc-citizen={isCitizen ? '1' : '0'}>
      {cosmetics.crest && <div className="mgi-gc-crest" aria-hidden="true" title="Citizen crest" />}
      <div className="mgi-header">
        <h3>Module Bay</h3>
        <div className="mgi-ship-info">
          <span className="mgi-ship-name">{data.ship_name}</span>
          {isCitizen && (
            <span className="mgi-gc-badge" title="Galactic Citizen — cosmetic flair unlocked">
              🌌 Galactic Citizen
            </span>
          )}
          <span className="mgi-credits">{formatCredits(credits)}</span>
        </div>
      </div>

      {isWarpJumper && (
        <div className="mgi-wj-warning" role="alert">
          ⚠ Warp Jumper hull — <strong>non-insurable</strong>. Modules fitted here are at
          risk: if the ship is destroyed there is no payout for the lost modules. Fit at your own risk.
        </div>
      )}

      {actionMessage && <div className="mgi-action-message">{actionMessage}</div>}

      {data.module_slots === null || slots.length === 0 ? (
        <div className="mgi-no-slots">
          This hull has no module slots — it predates the module-bay refit, or is too small to customize.
        </div>
      ) : (
        <div className="mgi-grid" style={gridStyle}>
          {slots.map((slot) => {
            const fitted = data.installed[String(slot.i)];
            const locked = slot.requires !== null; // requires-gated (none in the kernel)
            const classNames = [
              'mgi-slot',
              slot.super ? 'is-super' : '',
              slot.class ? 'is-class-locked' : '',
              fitted ? 'is-filled' : 'is-empty',
              locked ? 'is-locked' : '',
            ].filter(Boolean).join(' ');

            return (
              <div
                key={slot.i}
                className={classNames}
                title={
                  slot.super ? 'Supercharged slot — module effects boosted'
                  : slot.class ? `Class-locked slot: ${slot.class}`
                  : locked ? `Requires: ${slot.requires}`
                  : undefined
                }
              >
                <div className="mgi-slot-badges">
                  <span className="mgi-slot-index">#{slot.i}</span>
                  {slot.super && <span className="mgi-badge mgi-badge-super">SUPER</span>}
                  {slot.class && <span className="mgi-badge mgi-badge-class">{slot.class}</span>}
                  {locked && <span className="mgi-badge mgi-badge-lock">🔒 {slot.requires}</span>}
                </div>

                {fitted ? (
                  <div className="mgi-slot-fitted">
                    <div className="mgi-fitted-icon">{familyIcon(fitted.class)}</div>
                    <div className="mgi-fitted-name">{familyName(fitted.class)}</div>
                    <div className="mgi-fitted-tier">{TIER_LABEL[fitted.tier] ?? `Tier ${fitted.tier}`}</div>
                    {fitted.super_at_install && <div className="mgi-fitted-super">⚡ supercharged</div>}
                    <button
                      className="mgi-remove-btn"
                      disabled={actionLoading}
                      onClick={() => setAction({ kind: 'remove', slotIndex: slot.i, installed: fitted })}
                    >
                      Strip
                    </button>
                  </div>
                ) : locked ? (
                  <div className="mgi-slot-locked-body">
                    <span className="mgi-locked-text">Locked</span>
                  </div>
                ) : (
                  <button
                    className="mgi-slot-add"
                    disabled={actionLoading}
                    onClick={() => setAction({ kind: 'install', slot })}
                  >
                    <span className="mgi-add-plus">+</span>
                    <span className="mgi-add-label">Fit module</span>
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* WO-GC-B: Citizen cosmetic picker — BELOW the grid (secondary expression
          affordance; keeps the primary slot grid above the fold — scroll-law).
          Zero-stat flair, membership-gated; non-citizens see it disabled with a
          tooltip (never a hidden control). */}
      <div className="mgi-cosmetics">
        <div className="mgi-cosmetics-head">
          <span>🎨 Citizen Cosmetics</span>
          {!isCitizen && <span className="mgi-cosmetics-locked">Galactic Citizen members only</span>}
        </div>
        <div className="mgi-cosmetics-rows">
          {cosmeticCatalog.length === 0 ? (
            <div className="mgi-cosmetics-empty">Cosmetic catalog unavailable.</div>
          ) : (
            cosmeticCatalog.map((c) => (
              <div className="mgi-cosmetic-row" key={c.slot}>
                <span className="mgi-cosmetic-label">{c.label}</span>
                <div className="mgi-cosmetic-options">
                  {c.values.map((v) => (
                    <button
                      key={v}
                      className={`mgi-cosmetic-opt ${cosmetics[c.slot] === v ? 'is-selected' : ''}`}
                      disabled={!isCitizen || actionLoading}
                      onClick={() => handleSetCosmetic(c.slot, v)}
                      title={isCitizen ? `Apply ${v.replace(/^citizen_/, '')}` : 'Requires Galactic Citizen membership'}
                    >
                      {v.replace(/^citizen_/, '')}
                    </button>
                  ))}
                  <button
                    className="mgi-cosmetic-opt is-clear"
                    disabled={!isCitizen || actionLoading || !cosmetics[c.slot]}
                    onClick={() => handleSetCosmetic(c.slot, null)}
                    title="Clear this cosmetic"
                  >
                    clear
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* --- catalog drawer (install) --- */}
      {action?.kind === 'install' && (
        <div className="mgi-drawer-overlay" onClick={closeAction}>
          <div className="mgi-drawer" onClick={(e) => e.stopPropagation()}>
            <div className="mgi-drawer-header">
              <h4>
                Fit module — slot #{action.slot.i}
                {action.slot.super && <span className="mgi-badge mgi-badge-super"> SUPER</span>}
                {action.slot.class && <span className="mgi-badge mgi-badge-class"> {action.slot.class}</span>}
              </h4>
              <button className="mgi-drawer-close" onClick={closeAction} aria-label="Close catalog">✕</button>
            </div>
            {action.slot.super && (
              <p className="mgi-drawer-note">⚡ This is a supercharged slot — a fitted module's effects are boosted.</p>
            )}
            <div className="mgi-catalog">
              {candidatesForSlot(action.slot).map((fam) => {
                const deferred = !!fam.deferred;
                return (
                  <div key={fam.cls} className={`mgi-cat-card ${deferred ? 'is-deferred' : ''}`}>
                    <div className="mgi-cat-head">
                      <span className="mgi-cat-icon">{fam.icon}</span>
                      <span className="mgi-cat-name">{fam.name}</span>
                      {deferred && <span className="mgi-badge mgi-badge-soon">COMING SOON</span>}
                    </div>
                    <p className="mgi-cat-desc">{fam.description}</p>
                    {deferred ? (
                      <div className="mgi-cat-deferred-note">
                        Runtime effect pending — not yet installable.
                      </div>
                    ) : (
                      <div className="mgi-cat-tiers">
                        {Array.from({ length: MODULE_MAX_TIER }, (_, k) => k + 1).map((tier) => {
                          const cost = tierCost(fam.baseCost, tier);
                          const tooPoor = credits < cost;
                          const key = `${action.slot.i}:${fam.cls}:${tier}`;
                          const isPreviewing = previewKey === key;
                          return (
                            <button
                              key={tier}
                              type="button"
                              className={`mgi-tier-btn${isPreviewing ? ' is-previewing' : ''}`}
                              disabled={actionLoading || tooPoor}
                              title={tooPoor ? `Need ${formatCredits(cost)}` : undefined}
                              onMouseEnter={() => requestPreview(action.slot.i, fam.cls, tier)}
                              onFocus={() => requestPreview(action.slot.i, fam.cls, tier)}
                              onClick={() => handleInstall(action.slot.i, fam.cls, tier)}
                            >
                              <span className="mgi-tier-label">{TIER_LABEL[tier]}</span>
                              <span className="mgi-tier-cost">{formatCredits(cost)}</span>
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
              {candidatesForSlot(action.slot).length === 0 && (
                <div className="mgi-cat-empty">No modules are compatible with this slot on this hull.</div>
              )}
            </div>
            <div className="mgi-preview" data-testid="mgi-stat-preview" aria-live="polite">
              <h5 className="mgi-preview-title">Before / after preview</h5>
              {previewLoading && <p className="mgi-preview-status">Loading preview…</p>}
              {!previewLoading && previewError && (
                <p className="mgi-preview-status mgi-preview-error" role="alert">{previewError}</p>
              )}
              {!previewLoading && !previewError && !preview && (
                <p className="mgi-preview-status">Hover or focus a tier to preview shipyard stats.</p>
              )}
              {!previewLoading && preview && (
                <>
                  {preview.candidate?.name && (
                    <p className="mgi-preview-candidate">
                      {preview.candidate.name}
                      {preview.candidate.tier != null ? ` Mk${preview.candidate.tier}` : ''}
                    </p>
                  )}
                  <table className="mgi-preview-table">
                    <thead>
                      <tr>
                        <th scope="col">Stat</th>
                        <th scope="col">Now</th>
                        <th scope="col">After</th>
                        <th scope="col">Δ</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Array.from(
                        new Set([
                          ...Object.keys(preview.current || {}),
                          ...Object.keys(preview.projected || {}),
                          ...Object.keys(preview.delta || {}),
                        ]),
                      ).map((statKey) => {
                        const cur = Number(preview.current?.[statKey] ?? 0);
                        const proj = Number(preview.projected?.[statKey] ?? 0);
                        const delta = Number(
                          preview.delta?.[statKey] ?? proj - cur,
                        );
                        if (cur === 0 && proj === 0 && delta === 0) return null;
                        const label = PREVIEW_EFFECT_LABELS[statKey] ?? statKey;
                        const deltaCls =
                          delta > 0 ? 'is-up' : delta < 0 ? 'is-down' : 'is-flat';
                        const deltaSign = delta > 0 ? '+' : '';
                        return (
                          <tr key={statKey}>
                            <td>{label}</td>
                            <td>{cur}</td>
                            <td>{proj}</td>
                            <td className={deltaCls}>
                              {deltaSign}
                              {delta}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {/* --- destructive remove confirm (names the salvage refund) --- */}
      {action?.kind === 'remove' && (() => {
        const fam = FAMILY_BY_CLASS[action.installed.class];
        const fullCost = fam ? tierCost(fam.baseCost, action.installed.tier) : null;
        const estRefund = fullCost !== null ? Math.floor(fullCost * 0.25) : null;
        return (
          <div className="mgi-drawer-overlay" onClick={closeAction}>
            <div className="mgi-confirm" onClick={(e) => e.stopPropagation()}>
              <h4>Strip {familyName(action.installed.class)} {TIER_LABEL[action.installed.tier] ?? ''}?</h4>
              <p className="mgi-confirm-body">
                Pulling a module is a salvage operation — you only get back the salvage
                fraction (~25%) of its price, not the full cost.
                {estRefund !== null && (
                  <> You'll get back roughly <strong>{formatCredits(estRefund)}</strong>.</>
                )}
              </p>
              <div className="mgi-confirm-actions">
                <button className="mgi-cancel-btn" disabled={actionLoading} onClick={closeAction}>
                  Keep module
                </button>
                <button
                  className="mgi-confirm-btn"
                  disabled={actionLoading}
                  onClick={() => handleRemove(action.slotIndex)}
                >
                  {actionLoading ? 'Stripping...' : 'Strip & salvage'}
                </button>
              </div>
            </div>
          </div>
        );
      })()}
    </div>
  );
};

export default ModuleGridInterface;
