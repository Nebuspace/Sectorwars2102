import React, { useMemo } from 'react';
import { resourceIcon, resourceColor } from '../../services/resourceCatalog';

export type ProdRole = 'fuel' | 'organics' | 'equipment';

export interface RoleAllocation {
  fuel: number;
  organics: number;
  equipment: number;
}

/**
 * Per-colonist daily yield per role, derived from a STABLE server-confirmed
 * baseline pair (the last persisted allocation + the productionRates that pair
 * with it) in GameDashboard. A role whose confirmed allocation is 0 has no
 * measured per-colonist signal and is omitted (null) — never a fabricated /
 * Infinity / NaN number. The honest preview is simply this rate × the LIVE
 * slider head-count, so it tracks the dragged hypothetical linearly instead of
 * collapsing to the stale current rate.
 */
export type PerColonistRates = Partial<Record<ProdRole, number | null>> | null | undefined;

export interface CoupledColonistSlidersProps {
  /** Current per-role colonist head-counts (optimistic, server-confirmed on persist). */
  allocations: RoleAllocation;
  /** Server-confirmed per-day production rate per role (used for the preset weighting). */
  productionRates: Partial<Record<ProdRole, number>> | null | undefined;
  /**
   * Per-colonist daily yield per role from the STABLE server-confirmed baseline
   * (confirmedAllocation + the productionRates that pair with it), computed once
   * in GameDashboard. The honest preview multiplies this by the LIVE slider value
   * so it tracks the drag linearly. A role with a 0 / unknown baseline is null →
   * the UI shows "—". When absent, the slider falls back to the legacy preview.
   */
  perColonistRates?: PerColonistRates;
  /** Workforce budget — the citadel cap (maxColonists), already clamped to colonists. */
  budget: number;
  /** Total colonists on the planet (may exceed budget → surplus is idle). */
  totalColonists: number;
  /**
   * Persist a complete allocation. This is wired to the revived inline persister
   * (optimistic + debounced + revert-on-fail) in GameDashboard — NOT a new loop.
   * Every coupled/preset change funnels through here as one atomic set of three.
   */
  onSetAll: (next: RoleAllocation) => void;
  /** True while a persist is in flight (shows a subtle syncing hint). */
  syncing?: boolean;
  /** Verbatim server error from the last failed persist (revert already happened). */
  error?: string | null;
}

const ROLES: { key: ProdRole; icon: string; label: string; color: string }[] = [
  { key: 'fuel', icon: resourceIcon('fuel'), label: 'Fuel', color: resourceColor('fuel') },
  { key: 'organics', icon: resourceIcon('organics'), label: 'Organics', color: resourceColor('organics') },
  { key: 'equipment', icon: resourceIcon('equipment'), label: 'Equipment', color: resourceColor('equipment') },
];

const sumAlloc = (a: RoleAllocation): number => a.fuel + a.organics + a.equipment;

const fmt = (n: number) => Math.round(n).toLocaleString();

/**
 * Zero-sum proportional coupling.
 *
 * Allocation is colonist HEAD-COUNT. `budget` (the citadel workforce cap) is the
 * hard ceiling on the SUM of the three roles; idle = budget − Σ. Dragging one
 * slider first consumes the free idle pool, and only when that is exhausted does
 * it pull the OTHER TWO down — proportionally to their current sizes so their
 * ratio is preserved. The budget is conserved exactly: the donors give up
 * precisely `overflow = requested − idle` head-count between them, distributed by
 * a largest-remainder split so no head-count is created or lost to rounding.
 */
export function coupleAllocation(
  prev: RoleAllocation,
  role: ProdRole,
  requested: number,
  budget: number,
): RoleAllocation {
  const cap = Math.max(0, Math.floor(budget));
  // The dragged role can never exceed the whole budget.
  const target = Math.max(0, Math.min(Math.round(requested), cap));

  const otherKeys = (['fuel', 'organics', 'equipment'] as ProdRole[]).filter((k) => k !== role);
  const othersTotal = prev[otherKeys[0]] + prev[otherKeys[1]];

  // idle currently available to absorb growth without touching the donors
  const idle = Math.max(0, cap - prev[role] - othersTotal);
  const delta = target - prev[role];

  const next: RoleAllocation = { ...prev, [role]: target };

  if (delta <= 0) {
    // Shrinking (or unchanged) frees workforce back into idle — donors untouched.
    return next;
  }

  // Growing: idle absorbs first; the remainder must come OUT of the two donors.
  const overflow = delta - idle;
  if (overflow <= 0) {
    return next; // fit entirely in idle, no donor reduction
  }

  if (othersTotal <= 0) {
    // No donors to pull from — clamp the dragged role to what idle could give.
    next[role] = prev[role] + idle;
    return next;
  }

  const need = Math.min(overflow, othersTotal); // can't take more than donors hold
  // Proportional shares (preserve donor ratio), then largest-remainder rounding so
  // the donors give up EXACTLY `need` head-count (no drift / rounding leak).
  const raw = otherKeys.map((k) => (prev[k] / othersTotal) * need);
  const floors = raw.map((r) => Math.floor(r));
  let remainder = need - floors.reduce((s, f) => s + f, 0);
  // hand the leftover units to the largest fractional parts first
  const order = raw
    .map((r, i) => ({ i, frac: r - Math.floor(r) }))
    .sort((a, b) => b.frac - a.frac);
  const take = [...floors];
  for (let j = 0; j < order.length && remainder > 0; j++) {
    take[order[j].i] += 1;
    remainder -= 1;
  }

  otherKeys.forEach((k, idx) => {
    next[k] = Math.max(0, prev[k] - take[idx]);
  });
  // If donors couldn't cover the full overflow (clamped at 0), pull the dragged
  // role back down so the budget is still conserved exactly.
  const finalSum = sumAlloc(next);
  if (finalSum > cap) {
    next[role] = Math.max(0, next[role] - (finalSum - cap));
  }
  return next;
}

const CoupledColonistSliders: React.FC<CoupledColonistSlidersProps> = ({
  allocations,
  productionRates,
  perColonistRates,
  budget,
  totalColonists,
  onSetAll,
  syncing,
  error,
}) => {
  const assigned = sumAlloc(allocations);
  const idle = Math.max(0, budget - assigned);
  // Colonists above the workforce cap can't work — shown separately, not as idle.
  const surplus = Math.max(0, totalColonists - budget);

  const presets = useMemo(() => {
    const rates = {
      fuel: Number(productionRates?.fuel ?? 0),
      organics: Number(productionRates?.organics ?? 0),
      equipment: Number(productionRates?.equipment ?? 0),
    };
    const split = (f: number, o: number, e: number): RoleAllocation => ({
      fuel: Math.floor(budget * f),
      organics: Math.floor(budget * o),
      equipment: Math.floor(budget * e),
    });
    // Optimal: weight the workforce by each role's SERVER-DERIVED per-colonist
    // efficiency (rate / colonists-in-role). This uses the same planet-type
    // efficiency the server already baked into the live rates as the best proxy,
    // so it stays honest even on worlds that produce 0 of a given good. Falls
    // back to Balanced when no role has any measured per-colonist rate yet
    // (e.g. nothing allocated, so no signal exists client-side).
    const perCol = {
      fuel: allocations.fuel > 0 ? rates.fuel / allocations.fuel : 0,
      organics: allocations.organics > 0 ? rates.organics / allocations.organics : 0,
      equipment: allocations.equipment > 0 ? rates.equipment / allocations.equipment : 0,
    };
    const weightTotal = perCol.fuel + perCol.organics + perCol.equipment;
    const optimal: RoleAllocation =
      weightTotal > 0
        ? {
            fuel: Math.floor(budget * (perCol.fuel / weightTotal)),
            organics: Math.floor(budget * (perCol.organics / weightTotal)),
            equipment: Math.floor(budget * (perCol.equipment / weightTotal)),
          }
        : split(0.34, 0.33, 0.33);

    return [
      { key: 'balanced', label: '⚖️ Balanced', allocs: split(0.34, 0.33, 0.33) },
      { key: 'fuel', label: '⛽ Heavy-Fuel', allocs: split(0.7, 0.15, 0.15) },
      { key: 'organics', label: '🌿 Heavy-Organics', allocs: split(0.15, 0.7, 0.15) },
      { key: 'equipment', label: '⚙️ Heavy-Equipment', allocs: split(0.15, 0.15, 0.7) },
      { key: 'optimal', label: '✨ Optimal', allocs: optimal, title: 'Weighted by each role’s measured per-colonist yield on this world' },
    ];
  }, [allocations, productionRates, budget]);

  /**
   * Honest preview of a slider's output at the LIVE head-count `newValue`.
   *
   * The per-colonist rate is taken from a STABLE, server-confirmed baseline pair
   * (`perColonistRates` = confirmedProductionRate / confirmedAllocation, computed
   * once in GameDashboard) and multiplied by the live slider value. Because the
   * baseline denominator is the CONFIRMED allocation — not the optimistic one —
   * the preview is linear in the dragged value and tracks the hypothetical output
   * during a drag instead of algebraically collapsing to the stale current rate.
   *
   * Single source of truth — never the old ColonistAllocator math, which omitted
   * building / citadel / specialization / gourmet multipliers and lied on
   * 0-yield worlds.
   *
   * Divide-by-zero / no-signal guard: a role with no measured baseline yield
   * (confirmed allocation was 0, or the value is non-finite) is null here, so the
   * UI shows "—" and we never fabricate an Infinity / NaN number.
   *
   * Legacy fallback: if the baseline map is absent entirely (older caller), fall
   * back to the previous (rate / current-allocation × newValue) form so the
   * component degrades gracefully rather than blanking.
   */
  const previewRate = (role: ProdRole, newValue: number): number | null => {
    if (perColonistRates !== null && perColonistRates !== undefined) {
      const perCol = perColonistRates[role];
      if (perCol === null || perCol === undefined || !Number.isFinite(perCol)) {
        return null; // no measured baseline yield — don't fabricate a number
      }
      return perCol * newValue;
    }
    // Legacy fallback (no baseline map supplied).
    const rate = Number(productionRates?.[role] ?? 0);
    const cur = allocations[role];
    if (cur <= 0) return null; // no signal — don't fabricate a number
    return (rate / cur) * newValue;
  };

  /**
   * Magnetic detent at the "free-pool-exhausted" boundary.
   *
   * For the dragged role, `headroom = value + idle` is the head-count at which it
   * has consumed the entire unallocated idle pool but has NOT yet started stealing
   * from the other two roles — the natural "free growth" landing point. We make
   * that point momentarily STICKY: if the raw requested value lands within a small
   * threshold (~2% of the slider range) of it, snap exactly to it so it's easy to
   * stop right where free growth ends. Momentary, not a wall: a value clearly past
   * the threshold passes straight through (the user can always drag into the
   * steal-from-donors zone). Only the dragged role's coupling math runs — the snap
   * just adjusts which value we feed into the unchanged `coupleAllocation`.
   */
  const onSlide = (role: ProdRole, requested: number) => {
    const headroom = Math.min(budget, allocations[role] + idle);
    // Snap window: ~2% of the full range, min 1 colonist so tiny budgets still snap.
    const snapWindow = Math.max(1, Math.round(budget * 0.02));
    const snapped =
      idle > 0 && Math.abs(requested - headroom) <= snapWindow ? headroom : requested;
    onSetAll(coupleAllocation(allocations, role, snapped, budget));
  };

  const disabled = budget <= 0;

  return (
    <div className="cp-sliders">
      {/* Idle / workforce meter — first-class readout */}
      <div className="cp-workforce-meter">
        <span className="cp-wm-main">
          {fmt(assigned)}/{fmt(budget)} workforce
        </span>
        <span className="cp-wm-idle" title="Idle colonists within the workforce cap (reallocation is free + instant)">
          💤 {fmt(idle)} idle
        </span>
        {surplus > 0 && (
          <span
            className="cp-wm-surplus"
            title="Colonists above the citadel workforce cap — they settle fine but can't be assigned until habitability / citadel level rises"
          >
            ⚠ {fmt(surplus)} over cap
          </span>
        )}
        {syncing && <span className="cp-wm-sync">syncing…</span>}
      </div>

      <div className="cp-presets" role="group" aria-label="Workforce presets">
        {presets.map((p) => (
          <button
            key={p.key}
            type="button"
            className="cp-preset-btn"
            disabled={disabled}
            title={(p as any).title || `Set workforce: ${p.label}`}
            onClick={() => onSetAll(p.allocs)}
          >
            {p.label}
          </button>
        ))}
      </div>

      <div className="cp-slider-rows">
        {ROLES.map(({ key, icon, label, color }) => {
          const value = allocations[key];
          const pct = budget > 0 ? (value / budget) * 100 : 0;
          // Free-headroom point: the value at which this role would absorb the
          // ENTIRE idle pool but not yet steal from the other two (= value + idle,
          // capped at budget). When the thumb is LEFT of it (idle > 0) we paint a
          // highlighted band from the thumb to this point — the "free" growth the
          // role can take before it starts pulling donors down. The detent (in
          // onSlide) makes landing exactly on this point sticky.
          const headroom = Math.min(budget, value + idle);
          const headroomPct = budget > 0 ? (headroom / budget) * 100 : 0;
          const hasHeadroom = idle > 0 && headroomPct > pct;
          // Highlight band colour: the role colour at low opacity so it reads as
          // "same resource, but free/available" rather than a different channel.
          const headroomColor = `color-mix(in srgb, ${color} 38%, transparent)`;
          const trackBg = hasHeadroom
            ? `linear-gradient(to right,
                ${color} 0%, ${color} ${pct}%,
                ${headroomColor} ${pct}%, ${headroomColor} ${headroomPct}%,
                rgba(255,255,255,0.08) ${headroomPct}%, rgba(255,255,255,0.08) 100%)`
            : `linear-gradient(to right, ${color} 0%, ${color} ${pct}%, rgba(255,255,255,0.08) ${pct}%, rgba(255,255,255,0.08) 100%)`;
          const pv = previewRate(key, value);
          return (
            <div className="cp-slider-row" key={key}>
              <div className="cp-slider-head">
                <span className="cp-slider-label">
                  <span aria-hidden="true">{icon}</span> {label}
                </span>
                <span className="cp-slider-count">{fmt(value)} colonists</span>
              </div>
              <input
                type="range"
                className="cp-slider-input"
                min={0}
                max={Math.max(1, budget)}
                value={value}
                disabled={disabled}
                aria-label={`${label} workforce, ${fmt(value)} of ${fmt(budget)} colonists${hasHeadroom ? `, ${fmt(idle)} free before reallocating others` : ''}`}
                onChange={(e) => onSlide(key, parseInt(e.target.value, 10) || 0)}
                style={{ background: trackBg }}
              />
              <div className="cp-slider-preview">
                {pv === null ? (
                  <span className="cp-pv-none" title="Assign colonists to measure this world's per-colonist yield">
                    output —
                  </span>
                ) : (
                  <span className="cp-pv-val">≈ {fmt(pv)}/day</span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {error && (
        <div className="cp-slider-error" role="alert">
          ⚠️ {error}
        </div>
      )}
    </div>
  );
};

export default CoupledColonistSliders;
