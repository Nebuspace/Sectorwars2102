// @vitest-environment jsdom
/**
 * SafeVaultPanel — gated empty states, credit IO, commodity store/take, auto-deposit.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SafeVaultPanel, { type SafeCommodityDef } from '../SafeVaultPanel';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const commodities: SafeCommodityDef[] = [
  { stock: 'fuel', safe: 'fuel', icon: '⛽', name: 'Fuel' },
];

const ownedVaultProps = {
  isOwned: true,
  citadelInfo: {
    citadel_level: 2,
    auto_deposit: false,
    safe_commodities: { fuel: 10 },
    commodity_values: { fuel: 5 },
  },
  landedPlanetDetail: { productionRates: { fuel: 20 }, allocations: { fuel: 2 } },
  playerCredits: 1000,
  safeCredits: 200,
  safeCapacity: 5000,
  safeTotalValue: 250,
  onDepositCredits: vi.fn(),
  onWithdrawCredits: vi.fn(),
  creditBusy: false,
  commodities,
  projectedStock: (key: 'fuel' | 'organics' | 'equipment') => (key === 'fuel' ? 40 : 0),
  onMoveCommodity: vi.fn(),
  commodityBusy: null as string | null,
  onToggleAutoDeposit: vi.fn(),
  autoDepositBusy: false,
};

describe('SafeVaultPanel', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    vi.clearAllMocks();
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

  it('gates vault access when the planet is not owned', async () => {
    await act(async () => {
      root.render(
        <SafeVaultPanel
          {...ownedVaultProps}
          isOwned={false}
          citadelInfo={null}
        />,
      );
    });
    expect(container.textContent).toContain('Vault access requires planetary ownership');
  });

  it('prompts for Outpost when owned but no citadel safe', async () => {
    await act(async () => {
      root.render(
        <SafeVaultPanel
          {...ownedVaultProps}
          citadelInfo={{ citadel_level: 0 }}
        />,
      );
    });
    expect(container.textContent).toContain('No citadel safe');
    expect(container.textContent).toContain('Outpost');
  });

  it('shows capacity chrome and deposits credits via Max preset', async () => {
    const onDepositCredits = vi.fn();
    await act(async () => {
      root.render(
        <SafeVaultPanel {...ownedVaultProps} onDepositCredits={onDepositCredits} />,
      );
    });

    expect(container.textContent).toContain('Citadel Safe');
    expect(container.textContent).toContain('cr-equiv');

    const maxBtn = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent === 'Max',
    ) as HTMLButtonElement;
    await act(async () => {
      maxBtn.click();
    });

    const confirm = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent === 'Deposit',
    ) as HTMLButtonElement;
    await act(async () => {
      confirm.click();
    });
    // deposit max = min(wallet 1000, capacity-total 4750) = 1000
    expect(onDepositCredits).toHaveBeenCalledWith(1000);
  });

  it('withdraws credits after switching to Withdraw tab', async () => {
    const onWithdrawCredits = vi.fn();
    await act(async () => {
      root.render(
        <SafeVaultPanel {...ownedVaultProps} onWithdrawCredits={onWithdrawCredits} />,
      );
    });

    const withdrawTab = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent?.includes('Withdraw') && b.getAttribute('role') === 'tab',
    ) as HTMLButtonElement;
    await act(async () => {
      withdrawTab.click();
    });

    const maxBtn = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent === 'Max',
    ) as HTMLButtonElement;
    await act(async () => {
      maxBtn.click();
    });

    const confirm = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent === 'Withdraw' && b.classList.contains('confirm'),
    ) as HTMLButtonElement;
    await act(async () => {
      confirm.click();
    });
    expect(onWithdrawCredits).toHaveBeenCalledWith(200);
  });

  it('stores and takes commodities; toggles auto-deposit', async () => {
    const onMoveCommodity = vi.fn();
    const onToggleAutoDeposit = vi.fn();
    await act(async () => {
      root.render(
        <SafeVaultPanel
          {...ownedVaultProps}
          onMoveCommodity={onMoveCommodity}
          onToggleAutoDeposit={onToggleAutoDeposit}
        />,
      );
    });

    const store = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Store'),
    ) as HTMLButtonElement;
    await act(async () => {
      store.click();
    });
    // room=4750, unitVal=5 → canStore = min(40, floor(4750/5)) = 40
    expect(onMoveCommodity).toHaveBeenCalledWith('store', 'fuel', 40);

    const take = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Take'),
    ) as HTMLButtonElement;
    await act(async () => {
      take.click();
    });
    expect(onMoveCommodity).toHaveBeenCalledWith('take', 'fuel', 10);

    const checkbox = container.querySelector(
      'input[type="checkbox"]',
    ) as HTMLInputElement;
    await act(async () => {
      checkbox.click();
    });
    expect(onToggleAutoDeposit).toHaveBeenCalledWith(true);
  });
});
