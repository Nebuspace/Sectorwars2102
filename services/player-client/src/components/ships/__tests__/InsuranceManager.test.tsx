// @vitest-environment jsdom
/**
 * InsuranceManager — load / buy / error (WO-TESTCOV-PLAYER-MODULE-GRID).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const getInsurance = vi.fn();
const purchaseInsurance = vi.fn();

vi.mock('../../../services/api', () => ({
  shipAPI: {
    getInsurance: (...a: unknown[]) => getInsurance(...a),
    purchaseInsurance: (...a: unknown[]) => purchaseInsurance(...a),
  },
}));

import InsuranceManager from '../InsuranceManager';

const flush = () => new Promise((r) => setTimeout(r, 0));

const STATUS = {
  ship_id: 'ship-1',
  ship_name: 'Hauler',
  ship_type: 'CARGO_HAULER',
  insurable: true,
  current_tier: 'NONE',
  purchase_value: 100000,
  current_payout_amount: 0,
  tiers: [
    {
      tier: 'BASIC',
      premium_pct: 0.1,
      premium_full: 10000,
      net_payout_pct: 0.5,
      payout_amount: 50000,
      upgrade_cost: 10000,
      purchasable: true,
    },
    {
      tier: 'STANDARD',
      premium_pct: 0.2,
      premium_full: 20000,
      net_payout_pct: 0.7,
      payout_amount: 70000,
      upgrade_cost: 20000,
      purchasable: true,
    },
  ],
};

describe('InsuranceManager', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let onChanged: () => void;

  beforeEach(() => {
    getInsurance.mockReset();
    purchaseInsurance.mockReset();
    onChanged = vi.fn<() => void>();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('renders tiers after load and buys BASIC', async () => {
    purchaseInsurance.mockResolvedValue({ message: 'Policy written.' });
    getInsurance
      .mockResolvedValueOnce(STATUS)
      .mockResolvedValueOnce({ ...STATUS, current_tier: 'BASIC', current_payout_amount: 50000 });

    await act(async () => {
      root.render(
        <InsuranceManager shipId="ship-1" playerCredits={50000} onChanged={onChanged} />,
      );
      await flush();
    });

    expect(container.textContent).toMatch(/Hull Insurance — Hauler/);
    expect(container.textContent).toMatch(/Uninsured/);

    const buy = Array.from(container.querySelectorAll('button.ins-buy')).find((b) =>
      (b.textContent || '').includes('Insure'),
    );
    expect(buy).toBeTruthy();
    await act(async () => {
      buy!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await flush();
      await flush();
    });

    expect(purchaseInsurance).toHaveBeenCalledWith('ship-1', 'BASIC');
    expect(onChanged).toHaveBeenCalled();
    expect(container.textContent).toMatch(/Policy written/);
  });

  it('shows unavailable when load fails', async () => {
    getInsurance.mockRejectedValue(new Error('underwriter down'));
    await act(async () => {
      root.render(<InsuranceManager shipId="ship-1" playerCredits={0} />);
      await flush();
    });
    expect(container.textContent).toMatch(/Insurance is unavailable/);
  });

  it('disables buy when player cannot afford the premium', async () => {
    getInsurance.mockResolvedValue(STATUS);
    await act(async () => {
      root.render(<InsuranceManager shipId="ship-1" playerCredits={100} />);
      await flush();
    });
    const buys = Array.from(container.querySelectorAll('button.ins-buy'));
    expect(buys.length).toBeGreaterThan(0);
    expect(buys.every((b) => (b as HTMLButtonElement).disabled)).toBe(true);
  });
});
