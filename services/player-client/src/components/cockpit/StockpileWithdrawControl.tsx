import React, { useState } from 'react';

export type StockpileWithdrawCommodity = 'fuel_ore' | 'organics' | 'equipment';

export interface StockpileWithdrawControlProps {
  /** POST stockpile/withdraw with GS commodity keys. Parent surfaces GS detail. */
  onWithdraw: (commodity: StockpileWithdrawCommodity, amount: number) => void;
  busy?: boolean;
}

/**
 * Sibling to ProductionPanel Cargo for landed non-owners (teammates).
 * GET /planets/{id} is owner-only — this control does not invent stockpile numbers.
 */
const StockpileWithdrawControl: React.FC<StockpileWithdrawControlProps> = ({
  onWithdraw,
  busy = false,
}) => {
  const [commodity, setCommodity] = useState<StockpileWithdrawCommodity>('fuel_ore');
  const [amount, setAmount] = useState(1);
  const canSubmit = !busy && Number.isInteger(amount) && amount > 0;

  return (
    <span className="pvs-stockpile-withdraw" data-testid="stockpile-withdraw-control">
      <span className="pvs-transfer-label">Stockpile → cargo</span>
      <label>
        Commodity
        <select
          aria-label="Stockpile commodity"
          value={commodity}
          disabled={busy}
          onChange={(e) => setCommodity(e.target.value as StockpileWithdrawCommodity)}
        >
          <option value="fuel_ore">Fuel ore</option>
          <option value="organics">Organics</option>
          <option value="equipment">Equipment</option>
        </select>
      </label>
      <label>
        Amount
        <input
          aria-label="Stockpile amount"
          type="number"
          min={1}
          step={1}
          value={amount}
          disabled={busy}
          onChange={(e) => setAmount(Math.floor(Number(e.target.value)))}
        />
      </label>
      <button
        type="button"
        className="pvs-btn"
        disabled={!canSubmit}
        title="Move planet stockpile into ship cargo. Team tax skim is applied by the server."
        onClick={() => onWithdraw(commodity, amount)}
      >
        {busy ? '…' : '📦 To cargo'}
      </button>
    </span>
  );
};

export default StockpileWithdrawControl;
