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
 * not its child), and apiClient is stubbed since is_warp_jumper=true fires
 * the minimap fetch on mount.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../services/apiClient', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: { origin_sector_id: 1, spacing: 1, complete_radius_spacings: 25, sectors: [] } }) },
}));

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

let mockPlayerState: any = basePlayerState;
let mockQuantumStatus: any = baseQuantumStatus;

vi.mock('../../contexts/GameContext', () => ({
  useGame: () => ({
    playerState: mockPlayerState,
    currentSector: { type: 'STANDARD' },
    quantumStatus: mockQuantumStatus,
    quantumScan: vi.fn(),
    quantumJump: vi.fn(),
    refineQuantumCharge: vi.fn(),
    harvestNebula: vi.fn(),
    quantumScanResult: null,
    setQuantumScanResult: vi.fn(),
  }),
}));

import QuantumDriveConsole from './QuantumDriveConsole';

describe('QuantumDriveConsole — server-surfaced turn costs (WO-API-PHASE2)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockPlayerState = { ...basePlayerState };
    mockQuantumStatus = { ...baseQuantumStatus };
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
