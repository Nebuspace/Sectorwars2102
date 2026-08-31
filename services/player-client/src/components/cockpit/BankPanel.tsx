import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { centralBankAPI } from '../../services/api';
import { useResourceCatalog } from '../../hooks/useResourceCatalog';
import { formatCredits } from '../../utils/formatters';
import './bank-panel.css';

export function isStarportPrimeStation(
  station: { is_starport_prime?: boolean; name?: string } | null | undefined,
): boolean {
  if (!station) return false;
  if (station.is_starport_prime === true) return true;
  return /starport\s*prime/i.test(station.name || '');
}

/** Free cargo holds on the active ship (handles {used,contents} and flat maps). */
export function shipCargoFree(
  ship: { cargo?: unknown; cargo_capacity?: number } | null | undefined,
): number {
  if (!ship) return 0;
  const cargo = (ship.cargo && typeof ship.cargo === 'object') ? ship.cargo as Record<string, unknown> : {};
  let used = 0;
  if (typeof cargo.used === 'number' && Number.isFinite(cargo.used)) {
    used = cargo.used;
  } else if (cargo.contents && typeof cargo.contents === 'object') {
    used = Object.values(cargo.contents as Record<string, unknown>).reduce<number>(
      (sum, v) => sum + (Number(v) || 0),
      0,
    );
  } else {
    const skip = new Set(['capacity', 'used', 'contents']);
    used = Object.entries(cargo)
      .filter(([key, val]) => !skip.has(key) && typeof val === 'number')
      .reduce((sum, [, val]) => sum + (val as number), 0);
  }
  const capacity = Number(ship.cargo_capacity)
    || (typeof cargo.capacity === 'number' ? cargo.capacity : 0);
  return Math.max(0, capacity - used);
}

const clampAmount = (n: number, max: number): number => {
  if (!Number.isFinite(n) || max < 1) return 0;
  return Math.max(0, Math.min(Math.floor(n), max));
};

const commodityTurnCost = (qty: number): number => Math.ceil(Math.max(0, qty) / 100);

/** Exported for TypeError densify tests — balance load + withdraw catch paths use this. */
export function bankErrorMessage(err: unknown): string {
  if (err instanceof TypeError) return 'Bank request failed';
  const e = err as { message?: string; data?: { detail?: unknown } };
  const detail = e?.data?.detail;
  let extra = '';
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const rec = detail as Record<string, unknown>;
    const avail = rec.available_at_this_port ?? rec.available;
    if (typeof avail === 'number' && Number.isFinite(avail)) {
      extra = ` (available here: ${avail.toLocaleString()})`;
    }
  }
  return `${e?.message || 'Bank request failed'}${extra}`;
}

export interface BankPanelProps {
  isDocked: boolean;
  isStarportPrime: boolean;
  playerCredits: number;
  playerTurns: number;
  cargoFree: number;
  onAfterWithdraw: () => void;
}

const BankPanel: React.FC<BankPanelProps> = ({
  isDocked,
  isStarportPrime,
  playerCredits,
  playerTurns,
  cargoFree,
  onAfterWithdraw,
}) => {
  const { getLabel, getIcon } = useResourceCatalog();
  const [balance, setBalance] = useState<{ credits: number; commodities: Record<string, number> } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creditAmount, setCreditAmount] = useState(0);
  const [commodityQty, setCommodityQty] = useState<Record<string, number>>({});
  const [creditBusy, setCreditBusy] = useState(false);
  const [commodityBusy, setCommodityBusy] = useState<string | null>(null);

  const loadBalance = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await centralBankAPI.getBalance();
      const commodities = (data?.commodities && typeof data.commodities === 'object')
        ? Object.fromEntries(
            Object.entries(data.commodities as Record<string, unknown>).map(([k, v]) => [k, Number(v) || 0]),
          )
        : {};
      setBalance({
        credits: Number(data?.credits ?? 0) || 0,
        commodities,
      });
    } catch (err) {
      setError(bankErrorMessage(err));
      setBalance(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadBalance();
  }, [loadBalance, isDocked, isStarportPrime]);

  const bankCredits = balance?.credits ?? 0;
  const commodities = useMemo(
    () => Object.entries(balance?.commodities || {})
      .map(([key, qty]) => ({ key, qty: Number(qty) || 0 }))
      .filter((row) => row.qty > 0)
      .sort((a, b) => a.key.localeCompare(b.key)),
    [balance],
  );

  useEffect(() => {
    setCreditAmount((amt) => clampAmount(amt, bankCredits));
  }, [bankCredits]);

  const submitCredits = async () => {
    const amt = clampAmount(creditAmount, bankCredits);
    if (amt < 1 || creditBusy || !isDocked) return;
    setCreditBusy(true);
    setError(null);
    try {
      await centralBankAPI.withdrawCredits(amt);
      await loadBalance();
      onAfterWithdraw();
    } catch (err) {
      setError(bankErrorMessage(err));
    } finally {
      setCreditBusy(false);
    }
  };

  const submitCommodity = async (key: string) => {
    const held = commodities.find((c) => c.key === key)?.qty ?? 0;
    const maxQty = Math.min(held, Math.max(0, cargoFree));
    const qty = clampAmount(commodityQty[key] ?? 0, maxQty);
    const turnCost = commodityTurnCost(qty);
    if (qty < 1 || commodityBusy || !isStarportPrime || playerTurns < turnCost) return;
    setCommodityBusy(key);
    setError(null);
    try {
      await centralBankAPI.withdrawCommodity(key, qty);
      await loadBalance();
      onAfterWithdraw();
    } catch (err) {
      setError(bankErrorMessage(err));
    } finally {
      setCommodityBusy(null);
    }
  };

  return (
    <div className="bank-panel">
      <div className="bank-panel-header">
        <h4>🏦 Central Nexus Bank</h4>
        <span className="bank-panel-wallet" title="Credits in your wallet">
          Wallet {formatCredits(playerCredits)}
        </span>
      </div>
      <p className="bank-panel-explainer">
        Galactic Concord vault — region-independent. Credits withdraw instantly at
        Starport Prime; commodities cost 1 turn per 100 units to ship cargo.
      </p>

      {loading && <div className="bank-panel-status">Loading account…</div>}
      {error && (
        <div className="bank-panel-error" role="alert">{error}</div>
      )}

      {!loading && balance && (
        <div className="bank-panel-balance">
          <div className="bank-credits" title="Credits held in the Central Nexus Bank">
            <span className="bank-credits-label">Bank</span>
            <span className="bank-credits-value">{formatCredits(bankCredits)}</span>
          </div>
          {commodities.length === 0 ? (
            <div className="bank-empty-goods">No commodities on deposit.</div>
          ) : (
            <ul className="bank-goods-summary">
              {commodities.map(({ key, qty }) => (
                <li key={key}>
                  {getIcon(key)} {getLabel(key)}: {qty.toLocaleString()}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {!isDocked && (
        <div className="bank-panel-gate">
          Dock at a station to use the Central Nexus Bank.
        </div>
      )}

      {isDocked && !isStarportPrime && (
        <div className="bank-panel-gate">
          Full withdrawals require docking at Starport Prime. Cascade-compensation
          credits (access override) can be withdrawn at this port; the server will
          reject amounts above the override cap.
        </div>
      )}

      {isDocked && (
        <div className="bank-credit-io">
          <div className="bank-section-head">💰 Withdraw credits</div>
          <div className="bank-credit-row">
            <input
              type="number"
              className="bank-amount-input"
              aria-label="Credit withdrawal amount"
              min={bankCredits > 0 ? 1 : 0}
              max={bankCredits}
              value={creditAmount > 0 ? creditAmount : ''}
              placeholder="Amount"
              disabled={bankCredits < 1 || creditBusy}
              onChange={(e) => setCreditAmount(clampAmount(parseInt(e.target.value, 10), bankCredits))}
            />
            <div className="bank-presets" role="group" aria-label="Quick-fill credit amount">
              {([['25%', 0.25], ['50%', 0.5], ['75%', 0.75], ['Max', 1]] as const).map(([label, frac]) => (
                <button
                  key={label}
                  type="button"
                  className="bank-btn preset"
                  disabled={bankCredits < 1 || creditBusy}
                  onClick={() => setCreditAmount(clampAmount(bankCredits * frac, bankCredits))}
                >
                  {label}
                </button>
              ))}
            </div>
            <button
              type="button"
              className="bank-btn confirm"
              disabled={creditBusy || clampAmount(creditAmount, bankCredits) < 1}
              onClick={() => { void submitCredits(); }}
            >
              {creditBusy ? '…' : 'Withdraw credits'}
            </button>
          </div>
        </div>
      )}

      {isDocked && (
        <div className="bank-commodities">
          <div className="bank-section-head">
            📦 Withdraw commodities
            <span className="bank-hint">1 turn / 100 units · cargo free {cargoFree.toLocaleString()}</span>
          </div>
          {!isStarportPrime && (
            <div className="bank-panel-gate commodity-lock">
              Commodity withdrawals require docking at Starport Prime.
            </div>
          )}
          {commodities.length === 0 && isStarportPrime && (
            <div className="bank-empty-goods">Nothing to withdraw.</div>
          )}
          {commodities.map(({ key, qty }) => {
            const maxQty = Math.min(qty, Math.max(0, cargoFree));
            const entered = clampAmount(commodityQty[key] ?? 0, maxQty);
            const turnCost = commodityTurnCost(entered);
            const busy = commodityBusy === key;
            const locked = !isStarportPrime;
            const disabled = locked || busy || maxQty < 1 || entered < 1 || playerTurns < turnCost;
            return (
              <div className="bank-commodity-row" key={key}>
                <span className="bank-commodity-name">
                  {getIcon(key)} {getLabel(key)}
                  <span className="bank-commodity-held">{qty.toLocaleString()} in bank</span>
                </span>
                <input
                  type="number"
                  className="bank-amount-input"
                  aria-label={`${getLabel(key)} withdrawal quantity`}
                  min={maxQty > 0 ? 1 : 0}
                  max={maxQty}
                  value={entered > 0 ? entered : ''}
                  placeholder="Qty"
                  disabled={locked || maxQty < 1 || busy}
                  onChange={(e) => setCommodityQty((prev) => ({
                    ...prev,
                    [key]: clampAmount(parseInt(e.target.value, 10), maxQty),
                  }))}
                />
                <button
                  type="button"
                  className="bank-btn preset"
                  disabled={locked || maxQty < 1 || busy}
                  onClick={() => setCommodityQty((prev) => ({ ...prev, [key]: maxQty }))}
                >
                  Max
                </button>
                <span className="bank-turn-cost" title="Turns charged for this withdrawal">
                  {entered > 0 ? `${turnCost} turn${turnCost === 1 ? '' : 's'}` : '—'}
                </span>
                <button
                  type="button"
                  className="bank-btn confirm"
                  disabled={disabled}
                  title={
                    locked
                      ? 'Commodity withdrawals require docking at Starport Prime'
                      : playerTurns < turnCost
                        ? `Need ${turnCost} turns`
                        : maxQty < 1
                          ? 'No cargo space'
                          : `Withdraw ${entered.toLocaleString()} ${getLabel(key)}`
                  }
                  onClick={() => { void submitCommodity(key); }}
                >
                  {busy ? '…' : 'Withdraw'}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default BankPanel;
