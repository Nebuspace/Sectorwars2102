// @vitest-environment jsdom
/**
 * QuantumDriveConsole — WO-API-PHASE2 Lane B5: server-surfaced turn costs.
 *
 * Two things under test:
 *  1. Graceful degrade — an older server response (no scan_turn_cost /
 *     jump_turn_cost / jump_tow_surcharge) must render the same hardcoded
 *     fallbacks (5 / 50) it always has, never NaN/undefined text.
 *  2. BUG-1 fix, client side — once the server surfaces jump_tow_surcharge,
 *     the JUMP COMMIT button's own "INSUFFICIENT TURNS" turn-check (and its
 *     cost tag) must account for base + surcharge, not the flat base, so a
 *     towing pilot with turns in [base, base+surcharge) sees the accurate
 *     reason instead of the generic can_jump-derived "DRIVE NOT READY".
 *
 * Mirrors ShipSelector.test.tsx's seam: jsdom + react-dom/client createRoot
 * + act(), no RTL in this project. QuantumBearingViewport is stubbed to a
 * no-op (its own canvas/ResizeObserver/rAF machinery is irrelevant here —
 * the cost-tag and block-reason text under test live in this component,
 * not its child), and quantumAPI.getMinimap is stubbed since is_warp_jumper=true fires
 * the minimap fetch on mount.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { getModules, installModule } = vi.hoisted(() => ({
  getModules: vi.fn(),
  installModule: vi.fn(),
}));

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>();
  return {
    ...actual,
    quantumAPI: {
      ...actual.quantumAPI,
      getMinimap: vi.fn().mockResolvedValue({
        origin_sector_id: 1,
        spacing: 1,
        complete_radius_spacings: 25,
        sectors: [],
      }),
    },
    shipUpgradeAPI: {
      ...actual.shipUpgradeAPI,
      getModules: (...a: unknown[]) => getModules(...a),
      installModule: (...a: unknown[]) => installModule(...a),
    },
  };
});

vi.mock('./QuantumBearingViewport', () => ({
  default: () => null,
}));

vi.mock('./CrystalRefiningPanel', () => ({
  default: () => null,
}));

const EMPTY_MODULES = {
  ship_id: 'ship-1',
  ship_name: 'Jumper One',
  ship_type: 'WARP_JUMPER',
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

const FITTED_HARVESTER_MODULES = {
  ...EMPTY_MODULES,
  installed: {
    '0': { class: 'harvester', tier: 1, super_at_install: false, installed_at: '2026-08-27T00:00:00Z' },
  },
};

const basePlayerState = {
  id: 'player-1',
  current_sector_id: 1,
  turns: 100,
  is_docked: false,
  is_landed: false,
};

const baseQuantumStatus = {
  quantum_shards: 0,
  quantum_crystals: 0,
  quantum_charges: 1,
  jump_cooldown_until: null,
  scan_cooldown_until: null,
  can_jump: true,
  is_warp_jumper: true,
  sensor_level: 0,
};

let mockPlayerState: any = basePlayerState;
let mockQuantumStatus: any = baseQuantumStatus;
const updatePlayerCredits = vi.fn();
const refreshQuantumStatus = vi.fn();
const harvestNebula = vi.fn();

vi.mock('../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: mockPlayerState,
    currentSector: { type: 'STANDARD' },
    quantumStatus: mockQuantumStatus,
    quantumScan: vi.fn(),
    quantumJump: vi.fn(),
    refineQuantumCharge: vi.fn(),
    harvestNebula,
    refreshQuantumStatus,
    updatePlayerCredits,
    quantumScanResult: null,
    setQuantumScanResult: vi.fn(),
  }),
}));

import QuantumDriveConsole from './QuantumDriveConsole';

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('QuantumDriveConsole — server-surfaced turn costs (WO-API-PHASE2)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockPlayerState = { ...basePlayerState };
    mockQuantumStatus = { ...baseQuantumStatus };
    getModules.mockReset();
    installModule.mockReset();
    updatePlayerCredits.mockReset();
    refreshQuantumStatus.mockReset();
    harvestNebula.mockReset();
    getModules.mockResolvedValue(EMPTY_MODULES);
    installModule.mockResolvedValue({ success: true, remaining_credits: 150000 });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  const mount = async () => {
    await act(async () => {
      root.render(<QuantumDriveConsole />);
    });
    // Let the minimap fetch's resolved promise settle without an act() warning.
    await act(async () => {
      await Promise.resolve();
    });
  };

  it('falls back to the hardcoded 5/50 costs when the server omits the new fields', async () => {
    // baseQuantumStatus deliberately has no scan_turn_cost/jump_turn_cost/
    // jump_tow_surcharge — simulates an older server build.
    await mount();

    const scanBtn = container.querySelector('.qd-scan-btn') as HTMLButtonElement;
    const jumpBtn = container.querySelector('.qd-jump-btn') as HTMLButtonElement;
    expect(scanBtn.textContent).toContain('5');
    expect(jumpBtn.textContent).toContain('50');
    expect(container.textContent).not.toMatch(/NaN|undefined/);
  });

  it('uses the server-surfaced costs once present, honestly showing the tow surcharge', async () => {
    mockQuantumStatus = {
      ...baseQuantumStatus,
      scan_turn_cost: 5,
      jump_turn_cost: 50,
      jump_tow_surcharge: 5,
    };
    await mount();

    const jumpBtn = container.querySelector('.qd-jump-btn') as HTMLButtonElement;
    expect(jumpBtn.textContent).toContain('50');
    expect(jumpBtn.textContent).toContain('+5 TOW');
  });

  it('BUG-1: a towing pilot short of base+surcharge sees INSUFFICIENT TURNS, not a generic DRIVE NOT READY', async () => {
    // Server already fixed BUG-1 server-side: can_jump=false because
    // 50 turns < base(50)+surcharge(5)=55. The client's OWN turn-check must
    // reach the same conclusion via the same total, not the flat 50.
    mockPlayerState = { ...basePlayerState, turns: 50 };
    mockQuantumStatus = {
      ...baseQuantumStatus,
      can_jump: false,
      jump_turn_cost: 50,
      jump_tow_surcharge: 5,
    };
    await mount();

    const jumpBtn = container.querySelector('.qd-jump-btn') as HTMLButtonElement;
    expect(jumpBtn.textContent).toContain('INSUFFICIENT TURNS');
    expect(jumpBtn.disabled).toBe(true);
  });

  it('a non-towing pilot at exactly the base cost is still unaffected (no false negative)', async () => {
    mockPlayerState = { ...basePlayerState, turns: 50 };
    mockQuantumStatus = {
      ...baseQuantumStatus,
      can_jump: true,
      jump_turn_cost: 50,
      jump_tow_surcharge: 0,
    };
    await mount();

    const jumpBtn = container.querySelector('.qd-jump-btn') as HTMLButtonElement;
    expect(jumpBtn.textContent).not.toContain('INSUFFICIENT TURNS');
    expect(jumpBtn.disabled).toBe(false);
  });
});

describe('QuantumDriveConsole — Install Quantum Field Harvester CTA (LEG-2484)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockPlayerState = { ...basePlayerState, current_ship_id: 'ship-1' };
    mockQuantumStatus = { ...baseQuantumStatus };
    getModules.mockReset();
    installModule.mockReset();
    updatePlayerCredits.mockReset();
    refreshQuantumStatus.mockReset();
    harvestNebula.mockReset();
    getModules.mockResolvedValue(EMPTY_MODULES);
    installModule.mockResolvedValue({ success: true, remaining_credits: 150000 });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  const mount = async () => {
    await act(async () => {
      root.render(<QuantumDriveConsole />);
    });
    await act(async () => {
      await flush();
    });
  };

  it('renders the Install Quantum Field Harvester CTA with 50,000 cr catalog honesty when unequipped', async () => {
    await mount();
    expect(getModules).toHaveBeenCalledWith('ship-1');
    const cta = container.querySelector('.qd-install-harvester-btn') as HTMLButtonElement;
    expect(cta).toBeTruthy();
    expect(cta.textContent).toContain('Install Quantum Field Harvester');
    expect(cta.textContent).toContain('50,000');
    expect(cta.getAttribute('aria-label')).toMatch(/Install Quantum Field Harvester/);
    const actionButtons = container.querySelectorAll('.qd-scan-btn');
    expect(actionButtons).toHaveLength(2);
    expect(actionButtons[0].textContent).toContain('ECHO SCAN');
    expect(actionButtons[1].textContent).toContain('NO NEBULA HERE');
  });

  it('does not render the CTA when a harvester module is already fitted', async () => {
    getModules.mockResolvedValue(FITTED_HARVESTER_MODULES);
    await mount();
    expect(container.querySelector('.qd-install-harvester-btn')).toBeNull();
    const actionButtons = container.querySelectorAll('.qd-scan-btn');
    expect(actionButtons).toHaveLength(2);
    expect(actionButtons[1].textContent).toContain('NO NEBULA HERE');
  });

  it('calls installModule with harvester class on the first empty open slot', async () => {
    await mount();
    const cta = container.querySelector('.qd-install-harvester-btn') as HTMLButtonElement;
    await act(async () => {
      cta.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await flush();
    });
    expect(installModule).toHaveBeenCalledWith('ship-1', 0, 'harvester', 1);
    expect(updatePlayerCredits).toHaveBeenCalledWith(150000);
    expect(container.querySelector('.qd-install-harvester-btn')).toBeNull();
  });

  it('surfaces an honest GS venue/shipyard denial on the console', async () => {
    installModule.mockRejectedValue(new Error('You must be docked at a shipyard to fit modules'));
    await mount();
    const cta = container.querySelector('.qd-install-harvester-btn') as HTMLButtonElement;
    await act(async () => {
      cta.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      await flush();
    });
    expect(container.textContent).toContain('You must be docked at a shipyard to fit modules');
    expect(container.querySelector('.qd-install-harvester-btn')).toBeTruthy();
  });
});
