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
    expect(container.querySelector('[data-testid="mnt-load-error"]')?.textContent).toBe(
      'yard closed',
    );
    expect(container.textContent).toMatch(/yard closed/);
  });

  it('formatMaintenanceLoadError falls back on bare API Error status', async () => {
    const { formatMaintenanceLoadError } = await import('../MaintenanceManager');
    const err = Object.assign(new Error('API Error: 500'), { status: 500 });
    expect(formatMaintenanceLoadError(err)).toBe('Maintenance data is unavailable.');
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

  it('renders a distinct catastrophic warning below 10% condition', async () => {
    getMaintenanceStatus.mockResolvedValue({
      ...STATUS,
      condition: 8.2,
      band: {
        ...STATUS.band,
        tier: 'failing',
        failure_pct: 40,
        failure_tier: 'catastrophic',
      },
    });
    await act(async () => {
      root.render(<MaintenanceManager shipId="ship-1" playerCredits={20000} />);
      await flush();
    });
    const warn = container.querySelector('[data-testid="mnt-catastrophic-warn"]');
    expect(warn).toBeTruthy();
    expect(warn!.textContent).toMatch(/Catastrophic hull failure risk/);
    expect(warn!.textContent).toMatch(/catastrophic/i);
    const fill = container.querySelector('.mnt-bar-fill.catastrophic');
    expect(fill).toBeTruthy();
    expect(container.querySelector('.mnt-tier.catastrophic')).toBeTruthy();
  });

  it('keeps 10–24% in critical without catastrophic banner', async () => {
    getMaintenanceStatus.mockResolvedValue({
      ...STATUS,
      condition: 18,
      band: {
        ...STATUS.band,
        tier: 'critical',
        failure_pct: 15,
        failure_tier: 'critical',
      },
    });
    await act(async () => {
      root.render(<MaintenanceManager shipId="ship-1" playerCredits={20000} />);
      await flush();
    });
    expect(container.querySelector('[data-testid="mnt-catastrophic-warn"]')).toBeNull();
    expect(container.querySelector('.mnt-bar-fill.critical')).toBeTruthy();
    expect(container.querySelector('.mnt-bar-fill.catastrophic')).toBeNull();
  });
});

