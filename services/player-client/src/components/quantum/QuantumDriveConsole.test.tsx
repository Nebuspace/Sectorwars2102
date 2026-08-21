// @vitest-environment jsdom
/**
 * QuantumDriveConsole — WO-API-PHASE2 Lane B5 + LEG-115 harvester install.
 *
 * Covered:
 *  1. Graceful degrade — older server response omits turn-cost fields → 5/50.
 *  2. BUG-1 — jump_tow_surcharge folds into INSUFFICIENT TURNS check.
 *  3. LEG-115 — Install Quantum Field Harvester posts /equipment/install with
 *     quantum_harvester; harvest CTA stays gated until upgrades report fitted.
 *
 * Mirrors ShipSelector.test.tsx's seam: jsdom + react-dom/client createRoot
 * + act(), no RTL. QuantumBearingViewport stubbed; quantumAPI.getMinimap stubbed.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const getUpgradesMock = vi.fn();
const installEquipmentMock = vi.fn();
const harvestNebulaMock = vi.fn();

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
      getUpgrades: (...args: unknown[]) => getUpgradesMock(...args),
      installEquipment: (...args: unknown[]) => installEquipmentMock(...args),
    },
  };
});

vi.mock('./QuantumBearingViewport', () => ({
  default: () => null,
}));

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

const baseShip = { id: 'ship-1', type: 'WARP_JUMPER', name: 'Jumper' };

let mockPlayerState: any = basePlayerState;
let mockQuantumStatus: any = baseQuantumStatus;
let mockCurrentShip: any = baseShip;
let mockCurrentSector: any = { type: 'STANDARD' };

vi.mock('../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: mockPlayerState,
    currentShip: mockCurrentShip,
    currentSector: mockCurrentSector,
    quantumStatus: mockQuantumStatus,
    quantumScan: vi.fn(),
    quantumJump: vi.fn(),
    refineQuantumCharge: vi.fn(),
    harvestNebula: harvestNebulaMock,
    quantumScanResult: null,
    setQuantumScanResult: vi.fn(),
    refreshPlayerState: vi.fn().mockResolvedValue(undefined),
    updatePlayerCredits: vi.fn(),
  }),
}));

import QuantumDriveConsole from './QuantumDriveConsole';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('QuantumDriveConsole — server-surfaced turn costs (WO-API-PHASE2)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockPlayerState = { ...basePlayerState };
    mockQuantumStatus = { ...baseQuantumStatus };
    mockCurrentShip = { ...baseShip };
    mockCurrentSector = { type: 'STANDARD' };
    getUpgradesMock.mockReset();
    installEquipmentMock.mockReset();
    harvestNebulaMock.mockReset();
    // Default: harvester already fitted so legacy cost tests keep the harvest button.
    getUpgradesMock.mockResolvedValue({
      success: true,
      equipment: {
        quantum_harvester: { installed: true, cost: 50_000, name: 'Quantum Harvester' },
      },
      equipped: {
        quantum_harvester: { installed_at: '2026-01-01T00:00:00Z', effects: {} },
      },
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
    vi.clearAllMocks();
  });

  const mount = async () => {
    await act(async () => {
      root.render(<QuantumDriveConsole />);
    });
    // Let minimap + getUpgrades settle.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  it('falls back to the hardcoded 5/50 costs when the server omits the new fields', async () => {
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

describe('QuantumDriveConsole — Quantum Field Harvester install (LEG-115)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockPlayerState = { ...basePlayerState, is_docked: true };
    mockQuantumStatus = { ...baseQuantumStatus };
    mockCurrentShip = { ...baseShip };
    mockCurrentSector = { type: 'NEBULA' };
    getUpgradesMock.mockReset();
    installEquipmentMock.mockReset();
    harvestNebulaMock.mockReset();
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
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  it('shows Install CTA and posts /equipment/install with quantum_harvester when none fitted', async () => {
    getUpgradesMock.mockResolvedValue({
      success: true,
      equipment: {
        quantum_harvester: {
          installed: false,
          cost: 50_000,
          name: 'Quantum Harvester',
          compatible: true,
        },
      },
      equipped: {},
    });
    installEquipmentMock.mockResolvedValue({
      success: true,
      message: 'Quantum Harvester install started — ready in 24h',
      equipment: 'quantum_harvester',
      cost_paid: 50_000,
      remaining_credits: 100_000,
      pending: true,
      ready_at: '2099-01-01T00:00:00Z',
    });

    await mount();

    expect(getUpgradesMock).toHaveBeenCalledWith('ship-1');

    const installBtn = container.querySelector(
      '[data-testid="qd-install-harvester"]',
    ) as HTMLButtonElement;
    expect(installBtn).toBeTruthy();
    expect(installBtn.textContent).toMatch(/INSTALL QUANTUM FIELD HARVESTER/);
    expect(installBtn.textContent).toContain('50,000');
    expect(container.querySelector('[data-testid="qd-harvest-nebula"]')).toBeNull();

    await act(async () => {
      installBtn.click();
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(installEquipmentMock).toHaveBeenCalledWith('ship-1', 'quantum_harvester');
    expect(harvestNebulaMock).not.toHaveBeenCalled();
  });

  it('keeps harvest CTA gated until harvester is installed', async () => {
    getUpgradesMock.mockResolvedValue({
      success: true,
      equipment: {
        quantum_harvester: { installed: false, cost: 50_000, compatible: true },
      },
      equipped: {},
    });

    await mount();

    expect(container.querySelector('[data-testid="qd-harvest-nebula"]')).toBeNull();
    const installBtn = container.querySelector(
      '[data-testid="qd-install-harvester"]',
    ) as HTMLButtonElement;
    expect(installBtn).toBeTruthy();
    expect(installBtn.disabled).toBe(false);
  });

  it('shows harvest (not install) when upgrades report a ready harvester', async () => {
    getUpgradesMock.mockResolvedValue({
      success: true,
      equipment: {
        quantum_harvester: { installed: true, cost: 50_000 },
      },
      equipped: {
        quantum_harvester: { installed_at: '2026-01-01T00:00:00Z', effects: {} },
      },
    });

    await mount();

    expect(container.querySelector('[data-testid="qd-install-harvester"]')).toBeNull();
    const harvestBtn = container.querySelector(
      '[data-testid="qd-harvest-nebula"]',
    ) as HTMLButtonElement;
    expect(harvestBtn).toBeTruthy();
    expect(harvestBtn.disabled).toBe(false);
    expect(harvestBtn.textContent).toContain('HARVEST NEBULA');
  });
});
