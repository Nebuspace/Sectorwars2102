// @vitest-environment jsdom
/**
 * GenesisVenue — capacity gates, price degrade, purchase disable reasons.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import GenesisVenue from '../GenesisVenue';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const baseProps = {
  shipName: 'Hauler One',
  shipType: 'Cargo Hauler',
  currentGenesisDevices: 0,
  maxGenesisDevices: 2,
  genesisWeeklyRemaining: 3,
  genesisWeeklyLimit: 5,
  genesisRepGate: null as { required: number; current: number; met: boolean } | null,
  genesisSuccess: null as string | null,
  genesisError: null as string | null,
  genesisPurchasing: false,
  displayCredits: 100_000,
  genesisDevicePrice: 5000,
  purchaseGenesisDevice: vi.fn(),
  onBack: vi.fn(),
};

describe('GenesisVenue', () => {
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

  it('shows capacity orbs and acquires when purchase is enabled', async () => {
    const purchaseGenesisDevice = vi.fn();
    await act(async () => {
      root.render(
        <GenesisVenue {...baseProps} purchaseGenesisDevice={purchaseGenesisDevice} />,
      );
    });

    expect(container.textContent).toContain('Genesis Store');
    expect(container.textContent).toContain('0 / 2');
    expect(container.querySelectorAll('.genesis-orb')).toHaveLength(2);

    const btn = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Acquire Device'),
    ) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    await act(async () => {
      btn.click();
    });
    expect(purchaseGenesisDevice).toHaveBeenCalledTimes(1);
  });

  it('disables purchase and shows Price Unavailable when price is null', async () => {
    await act(async () => {
      root.render(<GenesisVenue {...baseProps} genesisDevicePrice={null} />);
    });
    expect(container.textContent).toContain('—');
    const btn = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Price Unavailable'),
    ) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it('warns when the ship cannot hold genesis devices', async () => {
    await act(async () => {
      root.render(<GenesisVenue {...baseProps} maxGenesisDevices={0} />);
    });
    expect(container.textContent).toContain('cannot carry Genesis Devices');
    const btn = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Ship Incompatible'),
    ) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it('surfaces rep-gate copy and disables Acquire', async () => {
    await act(async () => {
      root.render(
        <GenesisVenue
          {...baseProps}
          genesisRepGate={{ required: 50, current: 10, met: false }}
        />,
      );
    });
    expect(container.textContent).toContain('Heroic Federation');
    expect(container.textContent).toContain("you're at 10");
    const btn = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Reputation Too Low'),
    ) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.getAttribute('aria-describedby')).toBe('genesis-rep-gate-note');
  });

  it('wires Back to Hub', async () => {
    const onBack = vi.fn();
    await act(async () => {
      root.render(<GenesisVenue {...baseProps} onBack={onBack} />);
    });
    const back = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Back to Hub'),
    ) as HTMLButtonElement;
    await act(async () => {
      back.click();
    });
    expect(onBack).toHaveBeenCalledTimes(1);
  });
});
