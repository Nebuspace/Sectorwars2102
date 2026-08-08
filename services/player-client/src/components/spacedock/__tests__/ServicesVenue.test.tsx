// @vitest-environment jsdom
/**
 * ServicesVenue — repair money-path UI (WO-TESTCOV-PLAYER-SHIP-REPAIR).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../../ships', () => ({
  InsuranceManager: () => null,
  MaintenanceManager: () => null,
  ModuleGridInterface: () => null,
  TIER_LABEL: { basic: 'Basic' },
}));

import ServicesVenue from '../ServicesVenue';

const DAMAGED = {
  id: 'ship-1',
  name: 'Rusty Nail',
  current_value: 10_000,
  cargo_capacity: 50,
  cargo: { used: 0 },
  combat: { hull: 50, max_hull: 100, shields: 25, max_shields: 50 },
};

describe('ServicesVenue — Full Repair', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let repairShip: () => void;

  const renderVenue = (overrides: Record<string, unknown> = {}) => {
    act(() => {
      root.render(
        <ServicesVenue
          shipData={DAMAGED}
          displayCredits={50_000}
          stationServices={{ ship_repair: true }}
          repairSuccess={null}
          repairError={null}
          repairBusy={false}
          repairShip={repairShip}
          showInsurance={false}
          setShowInsurance={vi.fn()}
          showMaintenance={false}
          setShowMaintenance={vi.fn()}
          showUpgrades={false}
          setShowUpgrades={vi.fn()}
          insuranceTier={null}
          fetchInsuranceStatus={vi.fn()}
          refreshPlayerState={vi.fn()}
          fetchShipData={vi.fn()}
          onBack={vi.fn<() => void>()}
          blackMarketButton={null}
          {...overrides}
        />,
      );
    });
  };

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    repairShip = vi.fn<() => void>();
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it('enables Full Repair when damaged and funded, and clicks repairShip', async () => {
    renderVenue();
    const btn = Array.from(container.querySelectorAll('button.service-btn')).find((b) =>
      b.textContent?.includes('Full Repair'),
    ) as HTMLButtonElement;
    expect(btn).toBeTruthy();
    expect(btn.disabled).toBe(false);
    await act(async () => {
      btn.click();
    });
    expect(repairShip).toHaveBeenCalled();
  });

  it('disables Full Repair when at full condition', () => {
    renderVenue({
      shipData: {
        ...DAMAGED,
        combat: { hull: 100, max_hull: 100, shields: 50, max_shields: 50 },
      },
    });
    const btn = Array.from(container.querySelectorAll('button.service-btn')).find((b) =>
      b.textContent?.includes('Full Repair'),
    ) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.title).toBe('Ship is at full condition');
  });

  it('disables Full Repair when station lacks ship_repair', () => {
    renderVenue({ stationServices: {} });
    const btn = Array.from(container.querySelectorAll('button.service-btn')).find((b) =>
      b.textContent?.includes('Full Repair'),
    ) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.title).toBe('This station does not offer hull repair');
  });
});
