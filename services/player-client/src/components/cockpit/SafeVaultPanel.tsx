import React, { useEffect, useMemo, useState } from 'react';

/** One protected-commodity row: stockpile key → safe key, with display chrome. */
export interface SafeCommodityDef {
  stock: 'fuel' | 'organics' | 'equipment';
  safe: string;
  icon: string;
  name: string;
}

export interface SafeVaultPanelProps {
  /** True when the landed planet is owned by the player (vault gating). */
  isOwned: boolean;
  /** Live citadel telemetry from the landed poll (level / safe balances / flags). */
  citadelInfo: any;
  /** Live planet detail (production rates / allocations) for the Store-disabled hints. */
  landedPlanetDetail: any;
  /** Wallet credits — caps a credit deposit alongside vault headroom. */
  playerCredits: number;

  // ── Cap-bar figures (cr-equivalent: credits + stored goods under one cap) ──
  /** Credits currently in the safe. */
  safeCredits: number;
  /** The L-derived cr-equivalent safe capacity. */
  safeCapacity: number;
  /** Total cr-equivalent value secured (credits + commodities) for the cap bar. */
  safeTotalValue: number;

  // ── Credit deposit / withdraw (revives the GameDashboard dead cluster) ──
  /** Deposit credits into the safe — wraps useGame.depositToSafe. */
  onDepositCredits: (amount: number) => void;
  /** Withdraw credits from the safe — wraps useGame.withdrawFromSafe. */
  onWithdrawCredits: (amount: number) => void;
  /** True while a credit deposit/withdraw is in flight. */
  creditBusy: boolean;

  // ── Per-commodity store / take (reuses moveCommoditySafe) ──
  /** The fuel/organics/equipment ↔ safe mapping rows. */
  commodities: SafeCommodityDef[];
  /** Live projected planet-stockpile amount for a commodity (already clamped). */
  projectedStock: (key: 'fuel' | 'organics' | 'equipment') => number;
  /** Move stockpile ↔ safe. 'take' returns goods to the PLANET STOCKPILE. */
  onMoveCommodity: (dir: 'store' | 'take', safeKey: string, amount: number) => void;
  /** safeKey currently mid-transfer, or null. */
  commodityBusy: string | null;

  // ── Auto-deposit toggle ──
  /** Flip the "sweep production into the safe" flag. */
  onToggleAutoDeposit: (enabled: boolean) => void;
  /** True while the auto-deposit flag is being persisted. */
  autoDepositBusy: boolean;
}

/** Clamp + floor an amount into [1, max] (returns 0 when no room). */
const clampAmount = (n: number, max: number): number => {
  if (!Number.isFinite(n) || max < 1) return 0;
  return Math.max(0, Math.min(Math.floor(n), max));
};

/**
 * SafeVaultPanel — the UNIFIED citadel-safe instrument (Lane D).
 *
 * One vault, one cr-equivalent cap. Folds together what used to be split across
 * two tabs: CREDIT deposit/withdraw (formerly on the Citadel tab) and protected
 * COMMODITY store/take + the auto-deposit sweep (formerly the only thing on the
 * Safe tab). The safe holds credits AND commodities under a single
 * credit-equivalent capacity — there are no per-resource safe caps.
 *
 * Honesty note: "Take" returns goods to the PLANET STOCKPILE, not the ship —
 * the safe→ship-cargo (emergency-evac) path is unbuilt, so the label says so and
 * never implies a ship transfer.
 *
 * Presentational: all state + API calls live in GameDashboard's landed closure;
 * this panel only renders them and owns the small credit-amount input value.
 */
const SafeVaultPanel: React.FC<SafeVaultPanelProps> = ({
  isOwned,
  citadelInfo,
  landedPlanetDetail,
  playerCredits,
  safeCredits,
  safeCapacity,
  safeTotalValue,
  onDepositCredits,
  onWithdrawCredits,
  creditBusy,
  commodities,
  projectedStock,
  onMoveCommodity,
  commodityBusy,
  onToggleAutoDeposit,
  autoDepositBusy,
}) => {
  const hasCitadel = !!(citadelInfo && citadelInfo.citadel_level >= 1);

  // Credit form: which direction is active + the entered amount. Revives the
  // (formerly dead) deposit/withdraw flow by actually wiring it to a control.
  const [creditAction, setCreditAction] = useState<'deposit' | 'withdraw'>('deposit');
  const [creditAmount, setCreditAmount] = useState<number>(0);

  // The max a credit deposit/withdraw can move right now:
  //  - deposit: min(wallet, remaining cr-equiv headroom)  (server rejects beyond-cap)
  //  - withdraw: whatever credits are banked in the safe
  const creditMax = useMemo(() => {
    if (creditAction === 'deposit') {
      return Math.max(0, Math.min(playerCredits, safeCapacity - safeTotalValue));
    }
    return Math.max(0, safeCredits);
  }, [creditAction, playerCredits, safeCapacity, safeTotalValue, safeCredits]);

  // Keep the entered amount inside the live max as balances/wallet shift.
  useEffect(() => {
    setCreditAmount((amt) => clampAmount(amt, creditMax));
  }, [creditMax]);

  const setPreset = (fraction: number) => {
    setCreditAmount(clampAmount(creditMax * fraction, creditMax));
  };

  const submitCredits = () => {
    const amt = clampAmount(creditAmount, creditMax);
    if (amt < 1 || creditBusy) return;
    if (creditAction === 'deposit') onDepositCredits(amt);
    else onWithdrawCredits(amt);
  };

  // ── Not owned / no citadel: the gated empty state (preserves prior copy) ──
  if (!hasCitadel) {
    return (
      <div className="planet-section storage full-width safe-vault-panel">
        <div className="safe-header">
          <h4>🔐 Citadel Safe</h4>
        </div>
        <div className="safe-credits">
          <span className="credits-text">
            {!isOwned
              ? 'Vault access requires planetary ownership'
              : citadelInfo
                ? 'No citadel safe — establish an Outpost (Citadel Level 1) to unlock the vault'
                : 'Vault telemetry unavailable'}
          </span>
        </div>
      </div>
    );
  }

  const capPct = safeCapacity > 0 ? Math.min(100, (safeTotalValue / safeCapacity) * 100) : 0;
  const room = Math.max(0, safeCapacity - safeTotalValue);

  return (
    <div className="planet-section storage full-width safe-vault-panel">
      {/* ── Header + the single cr-equivalent cap bar ── */}
      <div className="safe-header">
        <h4>🔐 Citadel Safe</h4>
        <div className="safe-credits" title="Credits banked in the protected vault">
          <span className="credits-label">💰</span>
          <span className="credits-value">{safeCredits.toLocaleString()}</span>
          <span className="credits-text">credits</span>
        </div>
        <span className="safe-cap">{safeTotalValue.toLocaleString()} / {safeCapacity.toLocaleString()} cr-equiv</span>
      </div>
      <div
        className="vault-bar"
        title={`${safeTotalValue.toLocaleString()} / ${safeCapacity.toLocaleString()} cr-equivalent secured (credits + stored goods, one shared cap)`}
      >
        <div className="vault-bar-fill" style={{ width: `${capPct}%` }} />
      </div>

      {/* ── Credit deposit / withdraw (relocated here from the Citadel tab) ── */}
      <div className="safe-credit-io">
        <div className="sc-head">
          💰 Credits <span className="sc-hint">(deposited credits are raid-protected)</span>
        </div>
        <div className="safe-credit-tabs" role="tablist" aria-label="Credit transfer direction">
          <button
            type="button"
            role="tab"
            aria-selected={creditAction === 'deposit'}
            className={`safe-btn deposit${creditAction === 'deposit' ? ' active' : ''}`}
            onClick={() => setCreditAction('deposit')}
          >
            ▲ Deposit
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={creditAction === 'withdraw'}
            className={`safe-btn withdraw${creditAction === 'withdraw' ? ' active' : ''}`}
            onClick={() => setCreditAction('withdraw')}
          >
            ▼ Withdraw
          </button>
        </div>

        <div className="safe-credit-row">
          <input
            type="number"
            className="safe-amount-input"
            min={creditMax > 0 ? 1 : 0}
            max={creditMax}
            value={creditAmount > 0 ? creditAmount : ''}
            placeholder="Amount"
            disabled={creditMax < 1 || creditBusy}
            onChange={(e) => setCreditAmount(clampAmount(parseInt(e.target.value, 10), creditMax))}
            title={
              creditMax < 1
                ? creditAction === 'deposit'
                  ? safeTotalValue >= safeCapacity
                    ? 'Vault full — no headroom to deposit'
                    : 'No credits available to deposit'
                  : 'No credits in the safe to withdraw'
                : `Credits to ${creditAction} (max ${creditMax.toLocaleString()})`
            }
          />
          <div className="safe-presets" role="group" aria-label="Quick-fill amount">
            {([['25%', 0.25], ['50%', 0.5], ['75%', 0.75], ['Max', 1]] as const).map(([label, frac]) => (
              <button
                key={label}
                type="button"
                className="safe-btn preset"
                disabled={creditMax < 1 || creditBusy}
                onClick={() => setPreset(frac)}
                title={`${label} of the ${creditAction}able amount (${clampAmount(creditMax * frac, creditMax).toLocaleString()})`}
              >
                {label}
              </button>
            ))}
          </div>
          <button
            type="button"
            className={`safe-btn ${creditAction} confirm`}
            disabled={creditBusy || clampAmount(creditAmount, creditMax) < 1}
            onClick={submitCredits}
            title={
              creditMax < 1
                ? `Nothing to ${creditAction}`
                : `${creditAction === 'deposit' ? 'Deposit' : 'Withdraw'} ${clampAmount(creditAmount, creditMax).toLocaleString()} credits`
            }
          >
            {creditBusy ? '…' : creditAction === 'deposit' ? 'Deposit' : 'Withdraw'}
          </button>
        </div>
      </div>

      {/* ── Protected commodity store / take + auto-deposit ── */}
      <div className="safe-commodities">
        <div className="sc-head">
          📦 Stored Goods <span className="sc-hint">(protected from raiders)</span>
          <label
            className="sc-hint sc-autodeposit"
            style={{ cursor: autoDepositBusy ? 'wait' : 'pointer' }}
            title="Sweeps production into the protected vault up to the cap."
          >
            <input
              type="checkbox"
              checked={!!citadelInfo?.auto_deposit}
              disabled={autoDepositBusy}
              onChange={(e) => onToggleAutoDeposit(e.target.checked)}
              style={{ accentColor: '#7dd3fc', cursor: autoDepositBusy ? 'wait' : 'pointer' }}
            />
            Auto-deposit production
          </label>
        </div>
        <div className="sc-hint" style={{ marginBottom: '4px' }}>
          Auto-deposit sweeps production into the protected vault up to the cap.
          Otherwise, production fills the planet stockpile (right number) — Store it
          here to protect it from raiders. <strong>Take returns goods to the planet
          stockpile</strong> (not your ship).
        </div>
        {commodities.map(({ stock, safe, icon, name }) => {
          const inSafe = Number(citadelInfo?.safe_commodities?.[safe] ?? 0);
          const onPlanet = Math.floor(projectedStock(stock));
          const unitVal = Number(citadelInfo?.commodity_values?.[safe] ?? 0);
          const canStore = unitVal > 0 ? Math.min(onPlanet, Math.floor(room / unitVal)) : 0;
          const busy = commodityBusy === safe;
          // Why the Store button is greyed (distinguishes a full safe, a planet
          // type that yields none, an unstaffed line, and sub-1-unit accrual).
          const rate = Number(landedPlanetDetail?.productionRates?.[stock] ?? 0); // per day
          const allocation = Number(landedPlanetDetail?.allocations?.[stock] ?? 0);
          const storeDisabledTitle =
            onPlanet >= 1 && room < unitVal
              ? 'Safe full (cr-equivalent cap reached)'
              : allocation > 0 && rate <= 0
                ? `This world produces no ${name}`
                : rate <= 0
                  ? `No production — assign workforce to ${name}`
                  : `Producing ${Math.round(rate)}/day — under 1 unit stored so far`;
          return (
            <div className="sc-row" key={safe}>
              <span className="sc-name">{icon} {name}</span>
              <span className="sc-qty" title="In safe / on planet stockpile">
                {inSafe.toLocaleString()} <em>/ {onPlanet.toLocaleString()}</em>
              </span>
              <button
                className="safe-btn sc-btn"
                disabled={busy || canStore < 1}
                title={canStore < 1 ? storeDisabledTitle : `Store ${canStore.toLocaleString()} (${unitVal} cr/unit) into the vault`}
                onClick={() => onMoveCommodity('store', safe, canStore)}
              >
                {busy ? '…' : '▲ Store'}
              </button>
              <button
                className="safe-btn sc-btn"
                disabled={busy || inSafe < 1}
                title={inSafe < 1 ? `Safe holds no ${name}` : `Take all ${inSafe.toLocaleString()} → planet stockpile`}
                onClick={() => onMoveCommodity('take', safe, inSafe)}
              >
                {busy ? '…' : '▼ Take → planet'}
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default SafeVaultPanel;
