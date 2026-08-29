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

import CitadelManager, {
  formatCitadelLoadError,
  formatCitadelUpgradeError,
} from '../CitadelManager';

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

  it('L4 preflight names Shield Generator L4, not a defense-level line', async () => {
    getInfo.mockResolvedValue({
      ...CITADEL_INFO,
      citadel_level: 3,
      citadel_name: 'Colony',
      next_level: {
        ...CITADEL_INFO.next_level,
        level: 4,
        name: 'Major Colony',
        upgrade_cost: 50_000,
      },
    });

    await act(async () => {
      root.render(<CitadelManager planetId="planet-1" playerCredits={100_000} />);
    });
    await act(async () => {
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      expect(container.querySelector('.upgrade-prereq')).toBeTruthy();
    });

    const prereq = container.querySelector('.upgrade-prereq') as HTMLElement;
    expect(prereq.textContent).toContain('Shield Generator L4');
    expect(prereq.textContent).not.toMatch(/planetary defense level/i);
  });

  it('failed L4 upgrade surfaces GS 400 detail naming Shield Generator L4', async () => {
    getInfo.mockResolvedValue({
      ...CITADEL_INFO,
      citadel_level: 3,
      citadel_name: 'Colony',
      next_level: {
        ...CITADEL_INFO.next_level,
        level: 4,
        name: 'Major Colony',
        upgrade_cost: 50_000,
      },
    });
    upgrade.mockRejectedValue(
      new Error('Upgrade to Major Colony requires Shield Generator L4 (current shield generator: L0).'),
    );

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
    await act(async () => {
      btn.click();
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      const msg = container.querySelector('.citadel-message');
      // GS 400 sentence, not the preflight CITADEL_PREREQS line (which also
      // names Shield Generator L4 after this WO).
      expect(msg?.textContent).toContain(
        'Upgrade to Major Colony requires Shield Generator L4 (current shield generator: L0).',
      );
      expect(msg?.textContent).not.toMatch(/planetary defense level/i);
    });
  });

  it('load 403 non-owner shows server detail in error banner', async () => {
    const err = new Error('You do not own this planet');
    (err as { status?: number }).status = 403;
    getInfo.mockRejectedValue(err);

    await act(async () => {
      root.render(<CitadelManager planetId="planet-1" playerCredits={100_000} />);
    });
    await act(async () => {
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      const errorEl = container.querySelector('.citadel-error');
      expect(errorEl?.textContent).toContain('You do not own this planet');
    });
  });

  it('upgrade 400 already-in-progress shows server detail', async () => {
    const err = new Error('An upgrade is already in progress');
    (err as { status?: number }).status = 400;
    upgrade.mockRejectedValue(err);

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
    await act(async () => {
      btn.click();
      await flush();
      await flush();
    });

    await vi.waitFor(() => {
      const msg = container.querySelector('.citadel-message');
      expect(msg?.textContent).toBe('An upgrade is already in progress');
    });
  });

  it('formatCitadelLoadError falls back on bare 403 without server detail', () => {
    const err = new Error('API Error: 403');
    (err as { status?: number }).status = 403;
    expect(formatCitadelLoadError(err)).toBe('You do not own this planet.');
  });

  it('formatCitadelUpgradeError falls back when message is generic API Error', () => {
    const err = new Error('API Error: 400');
    (err as { status?: number }).status = 400;
    expect(formatCitadelUpgradeError(err)).toBe('Upgrade failed');
  });
});
