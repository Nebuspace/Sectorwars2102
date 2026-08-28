// @vitest-environment jsdom
/**
 * ModuleGridInterface — load / empty-slot install path (WO-TESTCOV-PLAYER-MODULE-GRID).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const getModules = vi.fn();
const installModule = vi.fn();
const removeModule = vi.fn();
const previewModule = vi.fn();
const setCosmetic = vi.fn();
const getCosmetics = vi.fn();

vi.mock('../../../services/api', () => ({
  shipUpgradeAPI: {
    getModules: (...a: unknown[]) => getModules(...a),
    installModule: (...a: unknown[]) => installModule(...a),
    removeModule: (...a: unknown[]) => removeModule(...a),
    previewModule: (...a: unknown[]) => previewModule(...a),
    setCosmetic: (...a: unknown[]) => setCosmetic(...a),
    getCosmetics: (...a: unknown[]) => getCosmetics(...a),
  },
}));

import ModuleGridInterface from '../ModuleGridInterface';

const flush = () => new Promise((r) => setTimeout(r, 0));

const EMPTY_MODULES = {
  ship_id: 'ship-1',
  ship_name: 'Scout One',
  ship_type: 'SCOUT_SHIP',
  module_slots: {
    v: 1,
    cols: 2,
    rows: 1,
    slots: [
      { i: 0, x: 0, y: 0, super: false, class: null, requires: null },
      { i: 1, x: 1, y: 0, super: false, class: null, requires: null },
    ],
  },
  installed: {},
  cosmetics: {},
  is_galactic_citizen: true,
};

/** Mirrors server CITIZEN_COSMETICS shape from GET /ships/{id}/cosmetics. */
const SERVER_COSMETICS = {
  success: true,
  catalog: {
    frame: {
      label: 'Citizen Hull Frame',
      values: ['citizen_aurora', 'citizen_obsidian'],
    },
    slot_glow: {
      label: 'Aurora Slot-Glow',
      values: ['citizen_hue'],
    },
    crest: {
      label: 'Citizen Crest',
      values: ['citizen_sigil'],
    },
  },
  applied: {},
  is_galactic_citizen: true,
};

describe('ModuleGridInterface', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    getModules.mockReset();
    installModule.mockReset();
    removeModule.mockReset();
    previewModule.mockReset();
    setCosmetic.mockReset();
    getCosmetics.mockReset();
    getCosmetics.mockResolvedValue(SERVER_COSMETICS);
    previewModule.mockResolvedValue({
      current: { speed_bonus: 1, cargo_bonus_percent: 10 },
      projected: { speed_bonus: 2, cargo_bonus_percent: 10 },
      delta: { speed_bonus: 1, cargo_bonus_percent: 0 },
      candidate: { name: 'Engine Module', tier: 1, class: 'engine' },
      replacing: null,
    });
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

  it('shows loading then the Module Bay with ship name', async () => {
    let resolve!: (v: unknown) => void;
    getModules.mockReturnValue(new Promise((r) => { resolve = r; }));

    await act(async () => {
      root.render(<ModuleGridInterface ship={{ id: 'ship-1' }} playerCredits={100000} />);
    });
    expect(container.textContent).toMatch(/Reading the slot lattice/);

    await act(async () => {
      resolve(EMPTY_MODULES);
      await flush();
    });
    expect(container.textContent).toMatch(/Module Bay/);
    expect(container.textContent).toMatch(/Scout One/);
    expect(getModules).toHaveBeenCalledWith('ship-1');
    expect(getCosmetics).toHaveBeenCalledWith('ship-1');
  });

  it('renders cosmetic catalog labels from GET /cosmetics (not a client mirror)', async () => {
    getModules.mockResolvedValue(EMPTY_MODULES);
    await act(async () => {
      root.render(<ModuleGridInterface ship={{ id: 'ship-1' }} playerCredits={100000} />);
      await flush();
    });
    expect(container.textContent).toMatch(/Citizen Hull Frame/);
    expect(container.textContent).toMatch(/Aurora Slot-Glow/);
    expect(container.textContent).toMatch(/Citizen Crest/);
    expect(container.textContent).toMatch(/aurora/);
  });

  it('surfaces load errors with a Retry control', async () => {
    getModules.mockRejectedValue(new Error('lattice offline'));
    await act(async () => {
      root.render(<ModuleGridInterface ship={{ id: 'ship-1' }} />);
      await flush();
    });
    expect(container.textContent).toMatch(/lattice offline/);
    expect(container.querySelector('.mgi-retry-btn')).toBeTruthy();
  });

  it('surfaces getModules 403 permission detail in load error UI', async () => {
    const err = new Error('You do not have permission to view this ship\'s modules');
    (err as { status?: number }).status = 403;
    getModules.mockRejectedValue(err);
    await act(async () => {
      root.render(<ModuleGridInterface ship={{ id: 'ship-1' }} />);
      await flush();
    });
    expect(container.querySelector('.mgi-error')?.textContent).toContain(
      'You do not have permission to view this ship\'s modules',
    );
    expect(container.querySelector('.mgi-retry-btn')).toBeTruthy();
  });

  it('surfaces getModules 404 not-found detail in load error UI', async () => {
    const err = Object.assign(new Error('Ship not found'), { status: 404 });
    getModules.mockRejectedValue(err);
    await act(async () => {
      root.render(<ModuleGridInterface ship={{ id: 'ship-1' }} />);
      await flush();
    });
    expect(container.querySelector('.mgi-error')?.textContent).toContain('Ship not found');
    expect(container.querySelector('.mgi-retry-btn')).toBeTruthy();
  });

  it('opens an empty slot and installs a catalog module', async () => {
    getModules.mockResolvedValue(EMPTY_MODULES);
    installModule.mockResolvedValue({
      success: true,
      message: 'Engine fitted',
      remaining_credits: 95000,
    });

    await act(async () => {
      root.render(<ModuleGridInterface ship={{ id: 'ship-1' }} playerCredits={100000} />);
      await flush();
    });

    const fitBtn = container.querySelector('button.mgi-slot-add');
    expect(fitBtn).toBeTruthy();
    await act(async () => {
      fitBtn!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await flush();
    });

    expect(container.textContent).toMatch(/Engine Module/);

    const tierBtn = container.querySelector('button.mgi-tier-btn');
    expect(tierBtn).toBeTruthy();
    await act(async () => {
      tierBtn!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await flush();
    });

    expect(installModule).toHaveBeenCalledWith('ship-1', 0, 'engine', 1);
    expect(container.textContent).toMatch(/Engine fitted/);
  });

  it('surfaces installModule 429 rate-limit detail in action message', async () => {
    getModules.mockResolvedValue(EMPTY_MODULES);
    const err = new Error('Too many module install requests — try again shortly');
    (err as { status?: number }).status = 429;
    installModule.mockRejectedValue(err);

    await act(async () => {
      root.render(<ModuleGridInterface ship={{ id: 'ship-1' }} playerCredits={100000} />);
      await flush();
    });

    const fitBtn = container.querySelector('button.mgi-slot-add');
    await act(async () => {
      fitBtn!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await flush();
    });

    const tierBtn = container.querySelector('button.mgi-tier-btn') as HTMLButtonElement | null;
    await act(async () => {
      tierBtn!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await flush();
      await flush();
    });

    expect(installModule).toHaveBeenCalledWith('ship-1', 0, 'engine', 1);
    expect(container.querySelector('.mgi-action-message')?.textContent).toBe(
      'Too many module install requests — try again shortly',
    );
  });

  it('shows GS before/after preview deltas on tier hover (no client bake math)', async () => {
    getModules.mockResolvedValue(EMPTY_MODULES);

    await act(async () => {
      root.render(<ModuleGridInterface ship={{ id: 'ship-1' }} playerCredits={100000} />);
      await flush();
    });

    const fitBtn = container.querySelector('button.mgi-slot-add');
    expect(fitBtn).toBeTruthy();
    await act(async () => {
      fitBtn!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await flush();
    });

    const tierBtn = container.querySelector('button.mgi-tier-btn') as HTMLButtonElement | null;
    expect(tierBtn).toBeTruthy();
    await act(async () => {
      // React maps onMouseEnter from mouseover (jsdom mouseenter is a no-op for React).
      tierBtn!.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
      await flush();
      await flush();
    });

    expect(previewModule).toHaveBeenCalledWith('ship-1', 0, 'engine', 1);
    const preview = container.querySelector('[data-testid="mgi-stat-preview"]');
    expect(preview).toBeTruthy();
    expect(preview!.textContent).toMatch(/Speed/);
    expect(preview!.textContent).toMatch(/\+1/);
    expect(installModule).not.toHaveBeenCalled();
  });

  it('surfaces preview API errors without inventing stats', async () => {
    getModules.mockResolvedValue(EMPTY_MODULES);
    previewModule.mockRejectedValue(new Error('preview offline'));

    await act(async () => {
      root.render(<ModuleGridInterface ship={{ id: 'ship-1' }} playerCredits={100000} />);
      await flush();
    });

    const fitBtn = container.querySelector('button.mgi-slot-add');
    await act(async () => {
      fitBtn!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await flush();
    });

    const tierBtn = container.querySelector('button.mgi-tier-btn') as HTMLButtonElement | null;
    await act(async () => {
      tierBtn!.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
      await flush();
      await flush();
    });

    expect(container.textContent).toMatch(/preview offline/);
    expect(container.querySelector('.mgi-preview-table')).toBeNull();
  });
});
