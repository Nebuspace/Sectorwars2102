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
const setCosmetic = vi.fn();

vi.mock('../../../services/api', () => ({
  shipUpgradeAPI: {
    getModules: (...a: unknown[]) => getModules(...a),
    installModule: (...a: unknown[]) => installModule(...a),
    removeModule: (...a: unknown[]) => removeModule(...a),
    setCosmetic: (...a: unknown[]) => setCosmetic(...a),
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
};

describe('ModuleGridInterface', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    getModules.mockReset();
    installModule.mockReset();
    removeModule.mockReset();
    setCosmetic.mockReset();
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
});
