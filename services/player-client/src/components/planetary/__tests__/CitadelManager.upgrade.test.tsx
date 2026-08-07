// @vitest-environment jsdom
/**
 * CitadelManager — upgrade money path (WO-TESTCOV-PLAYER-CITADEL-UPGRADE).
 * Upgrade button → citadelAPI.upgrade(planetId) → POST .../citadel/upgrade.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { upgrade, getInfo, CITADEL_INFO } = vi.hoisted(() => {
  const info = {
    success: true,
    planet_id: 'planet-1',
    planet_name: 'Test World',
    citadel_level: 1,
    citadel_name: 'Outpost',
    max_population: 1000,
    safe_storage: 100000,
    safe_credits: 0,
    drone_capacity: 10,
    is_upgrading: false,
    next_level: {
      level: 2,
      name: 'Settlement',
      upgrade_cost: 5000,
      upgrade_hours: 4,
      resource_cost: { fuel_ore: 1500 },
      max_population: 5000,
      safe_storage: 500000,
      drone_capacity: 25,
    },
  };
  return {
    CITADEL_INFO: info,
    upgrade: vi.fn(async () => ({ success: true })),
    getInfo: vi.fn(async () => info),
  };
});

vi.mock('../../../services/api', () => ({
  citadelAPI: {
    getInfo,
    upgrade,
  },
  resourceAPI: { list: vi.fn(() => new Promise(() => {})) },
}));

import CitadelManager from '../CitadelManager';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('CitadelManager — upgrade money path', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    upgrade.mockClear();
    getInfo.mockClear();
    getInfo.mockResolvedValue(CITADEL_INFO);
    upgrade.mockResolvedValue({ success: true });
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

  it('calls citadelAPI.upgrade when Upgrade is clicked', async () => {
    await act(async () => {
      root.render(<CitadelManager planetId="planet-1" playerCredits={100_000} />);
    });
    await act(async () => {
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      expect(container.querySelector('.citadel-btn.upgrade-btn')).toBeTruthy();
    });

    const btn = container.querySelector('.citadel-btn.upgrade-btn') as HTMLButtonElement;
    expect(btn.textContent).toContain('Upgrade');
    expect(btn.disabled).toBe(false);

    await act(async () => {
      btn.click();
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      expect(upgrade).toHaveBeenCalledWith('planet-1');
    });
  });
});
