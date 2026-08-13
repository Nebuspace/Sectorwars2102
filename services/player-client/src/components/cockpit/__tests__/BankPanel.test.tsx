// @vitest-environment jsdom
/**
 * BankPanel — Starport Prime gate, credit/commodity withdraw, error surface.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const getBalance = vi.fn();
const withdrawCredits = vi.fn();
const withdrawCommodity = vi.fn();

vi.mock('../../../services/api', () => ({
  centralBankAPI: {
    getBalance: (...args: unknown[]) => getBalance(...args),
    withdrawCredits: (...args: unknown[]) => withdrawCredits(...args),
    withdrawCommodity: (...args: unknown[]) => withdrawCommodity(...args),
  },
}));

vi.mock('../../../hooks/useResourceCatalog', () => ({
  useResourceCatalog: () => ({
    catalog: [],
    loading: false,
    getLabel: (n: string) => n,
    getIcon: () => '📦',
    getColor: () => '#fff',
  }),
}));

import BankPanel, { isStarportPrimeStation, shipCargoFree } from '../BankPanel';

const flush = () => act(async () => { await Promise.resolve(); });

function buttonByText(container: HTMLElement, text: string): HTMLButtonElement | undefined {
  return Array.from(container.querySelectorAll('button')).find((b) => b.textContent === text) as HTMLButtonElement | undefined;
}

describe('isStarportPrimeStation / shipCargoFree', () => {
  it('detects the flag or a Starport Prime name', () => {
    expect(isStarportPrimeStation({ is_starport_prime: true, name: 'Hub' })).toBe(true);
    expect(isStarportPrimeStation({ name: 'Central Nexus Starport Prime' })).toBe(true);
    expect(isStarportPrimeStation({ name: 'Frontier Hub Kepler' })).toBe(false);
  });

  it('computes free cargo from used/contents shapes', () => {
    expect(shipCargoFree({ cargo_capacity: 100, cargo: { used: 40, contents: { fuel: 40 } } })).toBe(60);
    expect(shipCargoFree({ cargo_capacity: 50, cargo: { contents: { ore: 12, fuel: 8 } } })).toBe(30);
  });
});

describe('BankPanel', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let onAfterWithdraw: ReturnType<typeof vi.fn<() => void>>;

  const renderPanel = async (overrides: Partial<React.ComponentProps<typeof BankPanel>> = {}) => {
    await act(async () => {
      root.render(
        <BankPanel
          isDocked
          isStarportPrime={false}
          playerCredits={1000}
          playerTurns={20}
          cargoFree={80}
          onAfterWithdraw={onAfterWithdraw}
          {...overrides}
        />,
      );
    });
    await flush();
  };

  beforeEach(() => {
    vi.clearAllMocks();
    getBalance.mockResolvedValue({ credits: 400, commodities: { fuel: 50 } });
    withdrawCredits.mockResolvedValue({ withdrawn: 400, bank_credits_remaining: 0, wallet_credits: 1400 });
    withdrawCommodity.mockResolvedValue({ commodity: 'fuel', quantity: 50, turn_cost: 1, bank_commodities_remaining: {} });
    onAfterWithdraw = vi.fn<() => void>();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
  });

  it('gates withdraw when not docked', async () => {
    await renderPanel({ isDocked: false });
    expect(container.textContent).toContain('Dock at a station to use the Central Nexus Bank');
    expect(buttonByText(container, 'Withdraw credits')).toBeUndefined();
    expect(getBalance).toHaveBeenCalled();
  });

  it('shows loading then bank balance', async () => {
    let resolveBalance: (v: unknown) => void = () => {};
    getBalance.mockReturnValue(new Promise((resolve) => { resolveBalance = resolve; }));
    await act(async () => {
      root.render(
        <BankPanel
          isDocked
          isStarportPrime
          playerCredits={1000}
          playerTurns={20}
          cargoFree={80}
          onAfterWithdraw={onAfterWithdraw}
        />,
      );
    });
    expect(container.textContent).toContain('Loading account');
    await act(async () => { resolveBalance({ credits: 250, commodities: {} }); });
    await flush();
    expect(container.textContent).toContain('₡250');
  });

  it('docked non-Prime: credit withdraw enabled, commodity locked', async () => {
    await renderPanel({ isDocked: true, isStarportPrime: false });
    expect(container.textContent).toContain('Full withdrawals require docking at Starport Prime');
    expect(container.textContent).toContain('Commodity withdrawals require docking at Starport Prime');

    const commodityBtn = buttonByText(container, 'Withdraw');
    expect(commodityBtn?.disabled).toBe(true);

    await act(async () => { buttonByText(container, 'Max')!.click(); });
    await act(async () => { buttonByText(container, 'Withdraw credits')!.click(); });
    await flush();
    expect(withdrawCredits).toHaveBeenCalledWith(400);
    expect(onAfterWithdraw).toHaveBeenCalled();
  });

  it('docked Prime: commodity withdraw calls the route with qty and shows turn cost', async () => {
    await renderPanel({ isDocked: true, isStarportPrime: true });
    const commodityMax = Array.from(container.querySelectorAll('.bank-commodity-row button.preset'))
      .find((b) => b.textContent === 'Max') as HTMLButtonElement;
    await act(async () => { commodityMax.click(); });
    expect(container.textContent).toMatch(/1 turn/);
    const commodityBtn = buttonByText(container, 'Withdraw')!;
    expect(commodityBtn.disabled).toBe(false);
    await act(async () => { commodityBtn.click(); });
    await flush();
    expect(withdrawCommodity).toHaveBeenCalledWith('fuel', 50);
    expect(onAfterWithdraw).toHaveBeenCalled();
  });

  it('surfaces a withdraw error message', async () => {
    withdrawCredits.mockRejectedValue(new Error('Withdrawal exceeds access-override balance'));
    await renderPanel({ isDocked: true, isStarportPrime: false });
    await act(async () => { buttonByText(container, 'Max')!.click(); });
    await act(async () => { buttonByText(container, 'Withdraw credits')!.click(); });
    await flush();
    expect(container.textContent).toContain('Withdrawal exceeds access-override balance');
    expect(onAfterWithdraw).not.toHaveBeenCalled();
  });
});
