// @vitest-environment jsdom
/**
 * MaintenanceManager — load / service / affordability (WO-TESTCOV-PLAYER-MODULE-GRID).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const getMaintenanceStatus = vi.fn();
const repairMaintenance = vi.fn();

vi.mock('../../../services/api', () => ({
  shipAPI: {
    getMaintenanceStatus: (...a: unknown[]) => getMaintenanceStatus(...a),
    repairMaintenance: (...a: unknown[]) => repairMaintenance(...a),
  },
}));

import MaintenanceManager from '../MaintenanceManager';

const flush = () => new Promise((r) => setTimeout(r, 0));

const STATUS = {
  ship_id: 'ship-1',
  ship_name: 'Worn Scout',
  condition: 62.5,
  decay_pct_per_day: 1.5,
  band: {
    tier: 'worn',
    speed_pct: -5,
    combat_pct: -10,
    fuel_pct: 5,
    failure_pct: 2,
    failure_tier: null,
  },
  applied_effects: ['combat'],
  repair_options: [
    {
      tier: 'basic',
      cost_pct_per_10: 1,
      cost_to_full: 5000,
      available: true,
    },
    {
      tier: 'premium',
      cost_pct_per_10: 2,
      cost_to_full: 12000,
      available: false,
    },
  ],
};

describe('MaintenanceManager', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let onChanged: () => void;

  beforeEach(() => {
    getMaintenanceStatus.mockReset();
    repairMaintenance.mockReset();
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

  it('renders condition and services with basic tier', async () => {
    getMaintenanceStatus
      .mockResolvedValueOnce(STATUS)
      .mockResolvedValueOnce({ ...STATUS, condition: 100, band: { ...STATUS.band, tier: 'pristine' } });
    repairMaintenance.mockResolvedValue({ message: 'Serviced.' });

    await act(async () => {
      root.render(
        <MaintenanceManager shipId="ship-1" playerCredits={20000} onChanged={onChanged} />,
      );
      await flush();
    });

    expect(container.textContent).toMatch(/Ship Maintenance — Worn Scout/);
    expect(container.textContent).toMatch(/62\.5%/);
    expect(container.textContent).toMatch(/Combat effectiveness/);
    expect(container.textContent).toMatch(/active/);
    expect(container.textContent).toMatch(/SpaceDock only/);

    const service = Array.from(container.querySelectorAll('button.mnt-buy')).find(
      (b) => b.textContent === 'Service',
    );
    expect(service).toBeTruthy();
    await act(async () => {
      service!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await flush();
      await flush();
    });

    expect(repairMaintenance).toHaveBeenCalledWith('ship-1', 'basic');
    expect(onChanged).toHaveBeenCalled();
    expect(container.textContent).toMatch(/Serviced/);
  });

  it('shows unavailable when load fails', async () => {
    getMaintenanceStatus.mockRejectedValue(new Error('yard closed'));
    await act(async () => {
      root.render(<MaintenanceManager shipId="ship-1" playerCredits={0} />);
      await flush();
    });
    expect(container.textContent).toMatch(/Maintenance data is unavailable/);
  });

  it('labels unaffordable service as Too costly', async () => {
    getMaintenanceStatus.mockResolvedValue(STATUS);
    await act(async () => {
      root.render(<MaintenanceManager shipId="ship-1" playerCredits={10} />);
      await flush();
    });
    const btn = Array.from(container.querySelectorAll('button.mnt-buy')).find(
      (b) => b.textContent === 'Too costly',
    );
    expect(btn).toBeTruthy();
    expect((btn as HTMLButtonElement).disabled).toBe(true);
  });
});
