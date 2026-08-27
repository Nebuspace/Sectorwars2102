// @vitest-environment jsdom
/**
 * ProductionPanel — stockpile rows, Store→Safe, overflow alert, specialization.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../CoupledColonistSliders', () => ({
  default: () => <div data-testid="colonist-sliders" />,
}));

import ProductionPanel, { type ProductionLine } from '../ProductionPanel';

const baseLine = (over: Partial<ProductionLine> = {}): ProductionLine => ({
  key: 'fuel',
  icon: '⛽',
  name: 'Fuel',
  stock: 120,
  rate: 40,
  ratio: 0.5,
  capped: true,
  nearCap: false,
  atCap: false,
  cap: 240,
  canStore: 50,
  storeBusy: false,
  storeDisabledTitle: 'nothing to store',
  ...over,
});

const defaultProps = {
  lines: [baseLine()],
  overflowResources: [] as string[],
  onOpenSpecialization: vi.fn(),
  allocations: { fuel: 1, organics: 0, equipment: 0, fighters: 0 },
  productionRates: { fuel: 40 },
  allocBudget: 10,
  totalColonists: 10,
  onSetAllocations: vi.fn(),
  onStoreToSafe: vi.fn(),
  onWithdrawToCargo: vi.fn(),
};

describe('ProductionPanel', () => {
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

  it('shows LIVE readout, stockpile chrome, and colonist sliders', async () => {
    await act(async () => {
      root.render(<ProductionPanel {...defaultProps} />);
    });

    expect(container.querySelector('.cp-title')?.textContent).toBe('Production');
    expect(container.textContent).toContain('LIVE');
    expect(container.textContent).toContain('UNPROTECTED');
    expect(container.textContent).toContain('Fuel');
    expect(container.querySelector('[data-testid="colonist-sliders"]')).toBeTruthy();
  });

  it('shows empty ledger copy when there are no production lines', async () => {
    await act(async () => {
      root.render(<ProductionPanel {...defaultProps} lines={[]} />);
    });
    expect(container.textContent).toContain('Colony ledger unavailable');
  });

  it('stores to safe when Store is enabled', async () => {
    const onStoreToSafe = vi.fn();
    await act(async () => {
      root.render(
        <ProductionPanel
          {...defaultProps}
          onStoreToSafe={onStoreToSafe}
          lines={[baseLine({ canStore: 50 })]}
        />,
      );
    });

    const storeBtn = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Store'),
    ) as HTMLButtonElement;
    expect(storeBtn.disabled).toBe(false);

    await act(async () => {
      storeBtn.click();
    });
    expect(onStoreToSafe).toHaveBeenCalledWith('fuel', 50);
  });

  it('loads stockpile to cargo without calling Store→Safe', async () => {
    const onStoreToSafe = vi.fn();
    const onWithdrawToCargo = vi.fn();
    await act(async () => {
      root.render(
        <ProductionPanel
          {...defaultProps}
          onStoreToSafe={onStoreToSafe}
          onWithdrawToCargo={onWithdrawToCargo}
          lines={[baseLine({ stock: 120, canWithdraw: 120 })]}
        />,
      );
    });

    const cargoBtn = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Cargo'),
    ) as HTMLButtonElement;
    expect(cargoBtn.disabled).toBe(false);

    await act(async () => {
      cargoBtn.click();
    });
    expect(onWithdrawToCargo).toHaveBeenCalledWith('fuel', 120);
    expect(onStoreToSafe).not.toHaveBeenCalled();
  });

  it('disables Cargo when canWithdraw < 1', async () => {
    await act(async () => {
      root.render(
        <ProductionPanel
          {...defaultProps}
          lines={[baseLine({ canWithdraw: 0, withdrawDisabledTitle: 'empty' })]}
        />,
      );
    });
    const cargoBtn = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Cargo'),
    ) as HTMLButtonElement;
    expect(cargoBtn.disabled).toBe(true);
  });

  it('disables Store when canStore < 1 or busy', async () => {
    await act(async () => {
      root.render(
        <ProductionPanel
          {...defaultProps}
          lines={[baseLine({ canStore: 0, storeBusy: false })]}
        />,
      );
    });
    let storeBtn = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Store'),
    ) as HTMLButtonElement;
    expect(storeBtn.disabled).toBe(true);

    await act(async () => {
      root.render(
        <ProductionPanel
          {...defaultProps}
          lines={[baseLine({ canStore: 10, storeBusy: true })]}
        />,
      );
    });
    storeBtn = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('…'),
    ) as HTMLButtonElement;
    expect(storeBtn.disabled).toBe(true);
  });

  it('surfaces overflow alert and wires Specialization', async () => {
    const onOpenSpecialization = vi.fn();
    await act(async () => {
      root.render(
        <ProductionPanel
          {...defaultProps}
          overflowResources={['fuel', 'organics']}
          onOpenSpecialization={onOpenSpecialization}
        />,
      );
    });

    const alert = container.querySelector('.cp-prod-overflow');
    expect(alert?.getAttribute('role')).toBe('alert');
    expect(alert?.textContent).toContain('fuel');
    expect(alert?.textContent).toContain('organics');

    const spec = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Specialization'),
    ) as HTMLButtonElement;
    await act(async () => {
      spec.click();
    });
    expect(onOpenSpecialization).toHaveBeenCalledTimes(1);
  });

  it('shows days-until-full when capped with a positive rate estimate', async () => {
    await act(async () => {
      root.render(
        <ProductionPanel
          {...defaultProps}
          lines={[baseLine({ capped: true, atCap: false, rate: 40, daysUntilFull: 2.4 })]}
        />,
      );
    });
    const eta = container.querySelector('[data-testid="days-until-full-fuel"]');
    expect(eta?.textContent).toBe('~2d to cap');
  });

  it('shows hours-to-cap when the estimate is under one day', async () => {
    await act(async () => {
      root.render(
        <ProductionPanel
          {...defaultProps}
          lines={[baseLine({ capped: true, atCap: false, daysUntilFull: 0.2 })]}
        />,
      );
    });
    expect(container.querySelector('[data-testid="days-until-full-fuel"]')?.textContent).toBe(
      '~5h to cap',
    );
  });

  it('hides days-until-full when atCap or rate is zero (null estimate)', async () => {
    await act(async () => {
      root.render(
        <ProductionPanel
          {...defaultProps}
          lines={[baseLine({ capped: true, atCap: true, rate: 0, daysUntilFull: null })]}
        />,
      );
    });
    expect(container.querySelector('[data-testid="days-until-full-fuel"]')).toBeNull();
    expect(container.textContent).not.toContain('to cap');
  });
});
