import React, { useEffect, useRef, useState } from 'react';
import CockpitPanel from './CockpitPanel';
import CoupledColonistSliders, { type RoleAllocation, type ProdRole, type PerColonistRates } from './CoupledColonistSliders';

export interface ProductionLine {
  key: 'fuel' | 'organics' | 'equipment';
  icon: string;
  name: string;
  /** Live projected stockpile (already ticking + clamped by the caller's clock). */
  stock: number;
  /** Production rate per real day (86400s). */
  rate: number;
  /** Storage fill ratio 0..1 (0 when uncapped). */
  ratio: number;
  capped: boolean;
  nearCap: boolean;
  atCap: boolean;
  /** Per-resource storage cap (0 when uncapped). */
  cap: number;
  /** How much of this resource can be stored to the safe right now (0 = none). */
  canStore: number;
  /** True while a store-to-safe call is in flight for this resource. */
  storeBusy: boolean;
  /** Reason the Store button is disabled (for the title), when canStore < 1. */
  storeDisabledTitle: string;
  /** How much of this resource can be loaded to ship cargo right now (0 = none). */
  canWithdraw?: number;
  /** True while a stockpile→cargo call is in flight for this resource. */
  withdrawBusy?: boolean;
  /** Reason the Cargo button is disabled (for the title), when canWithdraw < 1. */
  withdrawDisabledTitle?: string;
  /** Days until storage cap at current rate; null when uncapped / atCap / no rate. */
  daysUntilFull?: number | null;
  /** Production-building level (mine/farm/factory) for inline upgrade affordance. */
  buildingLevel?: number;
  /** Server building level cap — upgrade hidden/disabled at cap. */
  maxBuildingLevel?: number;
  /** Server-authoritative credits for the next level (cost preview). */
  nextUpgradeCredits?: number | null;
  /** True while this row's upgrade call is in flight. */
  upgradeBusy?: boolean;
  /** True when the server reports an upgrade already in progress. */
  buildingUpgrading?: boolean;
  /** Tooltip when the upgrade control is disabled. */
  upgradeDisabledTitle?: string;
  /** Fired when the player clicks Upgrade on this row. */
  onUpgrade?: () => void;
}

/** Existing GameDashboard helper — display only; do not invent a new formula. */
export const fmtDaysUntilFull = (d: number | null | undefined): string => {
  if (d == null) return '';
  if (d < 1) {
    const hrs = Math.max(1, Math.round(d * 24));
    return `~${hrs}h to cap`;
  }
  return `~${Math.round(d)}d to cap`;
};


export interface ProductionPanelProps {
  /** Live production lines, projected off the realtime poll (landedPlanetDetail). */
  lines: ProductionLine[];
  /** Commodities the server flagged as overflowing at the last tick. */
  overflowResources: string[];
  /** Last-tick food deficit from the planet DTO; absent when the colony is fed. */
  starvationWarning?: {
    food_deficit?: number;
    colonists_lost?: number;
    at?: string;
  } | null;
  /** Open the Colony Specialization modal. */
  onOpenSpecialization: () => void;
  /** Current per-role colonist head-counts (optimistic). */
  allocations: RoleAllocation;
  /** Server-confirmed per-day production rates per role. */
  productionRates: Partial<Record<ProdRole, number>> | null | undefined;
  /** Per-colonist baseline yield per role (for the honest, drag-tracking preview). */
  perColonistRates?: PerColonistRates;
  /** Workforce budget — citadel cap clamped to colonists. */
  allocBudget: number;
  /** Total colonists on the planet (may exceed budget). */
  totalColonists: number;
  /** Persist a full allocation via the revived inline persister. */
  onSetAllocations: (next: RoleAllocation) => void;
  /** True while an allocation persist is in flight. */
  allocSyncing?: boolean;
  /** Verbatim server error from the last failed allocation persist. */
  allocError?: string | null;
  /** Store the given resource's storable amount into the citadel safe. */
  onStoreToSafe: (key: 'fuel' | 'organics' | 'equipment', amount: number) => void;
  /** Load the given resource's stockpile into docked ship cargo (GS stockpile/withdraw). */
  onWithdrawToCargo: (key: 'fuel' | 'organics' | 'equipment', amount: number) => void;
  /** Verbatim error from the last failed inline building upgrade (any row). */
  buildingUpgradeError?: string | null;
}

const fmt = (n: number) => Math.floor(n).toLocaleString();

/**
 * RollingStock — renders a number whose digits visibly ROLL toward the live
 * target. Eases the displayed value toward `value` on each animation frame so
 * the stockpile climbs smoothly on screen between the ~1s projection ticks. No
 * extra fetch — purely a visual smoothing of the value the caller already feeds.
 */
const RollingStock: React.FC<{ value: number }> = ({ value }) => {
  const [shown, setShown] = useState(value);
  const shownRef = useRef(value);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    const target = value;
    const step = () => {
      const cur = shownRef.current;
      const diff = target - cur;
      if (Math.abs(diff) < 0.5) {
        shownRef.current = target;
        setShown(target);
        rafRef.current = null;
        return;
      }
      // ease ~18% of the gap per frame → a visible roll that settles quickly
      const nextVal = cur + diff * 0.18;
      shownRef.current = nextVal;
      setShown(nextVal);
      rafRef.current = window.requestAnimationFrame(step);
    };
    if (rafRef.current === null) {
      rafRef.current = window.requestAnimationFrame(step);
    }
    return () => {
      if (rafRef.current !== null) {
        window.cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [value]);

  return <span className="cp-roll" aria-live="off">{fmt(shown)}</span>;
};

/**
 * ProductionPanel — the PRODUCTION HUD instrument. Ticks LIVE off
 * landedPlanetDetail (the 15s realtime poll + the caller's per-second clamped
 * projection) — no extra fetch. Surfaces the coupled colonist sliders + presets
 * + idle meter, the rolling stockpile readouts (clearly labelled UNPROTECTED with
 * a Store→Safe affordance and a distinct stockpile→ship-cargo control),
 * storage fill, and the Specialization modal.
 */
const ProductionPanel: React.FC<ProductionPanelProps> = ({
  lines,
  overflowResources,
  starvationWarning = null,
  onOpenSpecialization,
  allocations,
  productionRates,
  perColonistRates,
  allocBudget,
  totalColonists,
  onSetAllocations,
  allocSyncing,
  allocError,
  onStoreToSafe,
  onWithdrawToCargo,
  buildingUpgradeError,
}) => (
  <CockpitPanel title="Production" accent="#7dd3fc" readout={<span className="cp-live-tag">┄ LIVE ┄</span>}>
    <CoupledColonistSliders
      allocations={allocations}
      productionRates={productionRates}
      perColonistRates={perColonistRates}
      budget={allocBudget}
      totalColonists={totalColonists}
      onSetAll={onSetAllocations}
      syncing={allocSyncing}
      error={allocError}
    />

    <div className="cp-stockpile-section">
      <div className="cp-stockpile-head">
        <span className="cp-sp-title">Planet Stockpile</span>
        <span className="cp-sp-warn" title="The planet stockpile is raidable. Production flows here, not into the safe. Store goods to the citadel safe to protect them.">
          UNPROTECTED
        </span>
      </div>

      {lines.length === 0 ? (
        <div className="cp-empty">Colony ledger unavailable</div>
      ) : (
        <div className="cp-production-lines">
          {lines.map((l) => {
            const showUpgrade = l.buildingLevel !== undefined && l.maxBuildingLevel !== undefined;
            const atBuildingCap = showUpgrade && l.buildingLevel! >= l.maxBuildingLevel!;
            const upgradeDisabled =
              !l.onUpgrade ||
              l.upgradeBusy ||
              l.buildingUpgrading ||
              atBuildingCap;
            const upgradeTitle = atBuildingCap
              ? `Building at maximum level (${l.maxBuildingLevel})`
              : l.buildingUpgrading
                ? 'Upgrade already in progress'
                : l.upgradeDisabledTitle
                  ?? (l.nextUpgradeCredits != null
                    ? `Upgrade to level ${l.buildingLevel! + 1} (${l.nextUpgradeCredits.toLocaleString()} credits)`
                    : `Upgrade to level ${l.buildingLevel! + 1}`);
            return (
            <div className="cp-prod-row" key={l.key}>
              <span className="cp-prod-icon">{l.icon}</span>
              <span className="cp-prod-name">{l.name}</span>
              <span className="cp-prod-stock">
                📦 <RollingStock value={l.stock} />
                {l.capped ? `/${l.cap.toLocaleString()}` : ''}
                <span className="cp-prod-rate"> +{Math.round(l.rate).toLocaleString()}/day</span>
                {showUpgrade && (
                  <span className="cp-prod-bldg-lvl" data-testid={`building-level-${l.key}`}>
                    {' '}· Lv {l.buildingLevel}/{l.maxBuildingLevel}
                  </span>
                )}
              </span>
              {showUpgrade && (
                <button
                  type="button"
                  className="cp-store-btn cp-upgrade-btn"
                  data-testid={`upgrade-${l.key}`}
                  disabled={upgradeDisabled}
                  title={upgradeTitle}
                  onClick={() => l.onUpgrade?.()}
                >
                  {l.upgradeBusy ? '…' : '⬆ Upgrade'}
                </button>
              )}
              <button
                type="button"
                className="cp-store-btn"
                disabled={l.storeBusy || l.canStore < 1}
                title={l.canStore < 1 ? l.storeDisabledTitle : `Store ${l.canStore.toLocaleString()} to the citadel safe (raid-proof)`}
                onClick={() => onStoreToSafe(l.key, l.canStore)}
              >
                {l.storeBusy ? '…' : '🔐 Store'}
              </button>
              {(() => {
                const canWithdraw = l.canWithdraw ?? Math.floor(l.stock);
                const withdrawBusy = !!l.withdrawBusy;
                const withdrawTitle = canWithdraw < 1
                  ? (l.withdrawDisabledTitle || 'No stockpile to load to cargo')
                  : `Move ${canWithdraw.toLocaleString()} to ship cargo`;
                return (
                  <button
                    type="button"
                    className="cp-store-btn cp-cargo-btn"
                    disabled={withdrawBusy || canWithdraw < 1}
                    title={withdrawTitle}
                    onClick={() => onWithdrawToCargo(l.key, canWithdraw)}
                  >
                    {withdrawBusy ? '…' : '📦 Cargo'}
                  </button>
                );
              })()}
              {l.capped && (
                <div className="cp-prod-bar">
                  <div
                    className={`cp-prod-bar-fill${l.atCap ? ' at-cap' : l.nearCap ? ' near-cap' : ''}`}
                    style={{ width: `${Math.round(l.ratio * 100)}%` }}
                  />
                </div>
              )}
              {l.capped && fmtDaysUntilFull(l.daysUntilFull) !== '' && (
                <span
                  className="cp-prod-eta"
                  data-testid={`days-until-full-${l.key}`}
                >
                  {fmtDaysUntilFull(l.daysUntilFull)}
                </span>
              )}
            </div>
            );
          })}
        </div>
      )}
      {buildingUpgradeError && (
        <div className="cp-prod-upgrade-error" role="alert" data-testid="building-upgrade-error">
          {buildingUpgradeError}
        </div>
      )}
      {overflowResources.length > 0 && (
        <div className="cp-prod-overflow" role="alert">
          ⚠️ Storage full: {overflowResources.join(', ')} — output above the cap is wasted.
        </div>
      )}
      {starvationWarning &&
        typeof starvationWarning === 'object' &&
        (Number(starvationWarning.colonists_lost ?? 0) > 0 ||
          Number(starvationWarning.food_deficit ?? 0) > 0) && (
        <div className="cp-prod-starvation" role="alert" data-testid="starvation-warning">
          ⚠️ Food deficit: {Number(starvationWarning.food_deficit ?? 0).toLocaleString()} organics
          short, {Number(starvationWarning.colonists_lost ?? 0).toLocaleString()} colonists lost.
        </div>
      )}
    </div>

    <div className="cp-actions">
      <button type="button" className="cp-action-btn" onClick={onOpenSpecialization} title="Choose a colony specialization">
        🎯 Specialization
      </button>
    </div>
  </CockpitPanel>
);

export default ProductionPanel;
