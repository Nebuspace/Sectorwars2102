// @vitest-environment jsdom
/**
 * WarpGateAttackControl — LEG-4116 warp-gate / beacon attack.
 */
import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const listSector = vi.fn();
const attackWarpGate = vi.fn();
const refreshPlayerState = vi.fn();
const getAvailableMoves = vi.fn();

let mockPlayerState: {
  id?: string;
  is_docked?: boolean;
  is_landed?: boolean;
} | null = { id: 'player-1', is_docked: false, is_landed: false };

let mockCurrentSector: { id: string; sector_id: number } | null = {
  id: '11111111-1111-1111-1111-111111111111',
  sector_id: 42,
};

vi.mock('../../../services/api', () => ({
  combatAPI: {
    attackWarpGate: (...args: unknown[]) => attackWarpGate(...args),
  },
  warpGatesAPI: {
    listSector: (...args: unknown[]) => listSector(...args),
  },
}));

vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => ({
    currentSector: mockCurrentSector,
    playerState: mockPlayerState,
    refreshPlayerState,
    getAvailableMoves,
  }),
}));

import WarpGateAttackControl, {
  formatWarpGateAttackError,
} from '../WarpGateAttackControl';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const GATE_ID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
const BEACON_ID = 'bbbbbbbb-cccc-dddd-eeee-ffffffffffff';

const apiRequestError = (status: number, message?: string) => {
  const err = new Error(message ?? `API Error: ${status}`);
  (err as { status?: number }).status = status;
  return err;
};

describe('formatWarpGateAttackError (LEG-4116)', () => {
  const fallback = 'Warp gate attack failed';

  it('densifies TypeError network collapse', () => {
    expect(formatWarpGateAttackError(new TypeError('Failed to fetch'), fallback)).toBe(fallback);
    expect(formatWarpGateAttackError(new TypeError('Failed to fetch'), fallback)).not.toMatch(
      /TypeError/i,
    );
  });

  it('surfaces 403 permission path without raw status codes', () => {
    expect(formatWarpGateAttackError(apiRequestError(403), fallback)).toMatch(/permission/i);
    expect(formatWarpGateAttackError(apiRequestError(403), fallback)).not.toMatch(/\b403\b/);
    expect(
      formatWarpGateAttackError(
        apiRequestError(403, 'Player must be in the gate sector to attack'),
        fallback,
      ),
    ).toMatch(/gate sector/i);
  });

  it('surfaces 429 rate-limit path without raw status codes', () => {
    expect(formatWarpGateAttackError(apiRequestError(429), fallback)).toMatch(/rate limit/i);
    expect(formatWarpGateAttackError(apiRequestError(429), fallback)).not.toMatch(/\b429\b/);
    expect(formatWarpGateAttackError(apiRequestError(429), fallback)).not.toMatch(/TypeError/i);
  });
});

describe('formatWarpGateAttackError typeErrorHonesty (LEG-4116)', () => {
  const fallback = 'Warp gate attack failed';

  it('collapses TypeError / Network Error / Failed to fetch without transport strings', () => {
    expect(formatWarpGateAttackError(new TypeError('Failed to fetch'), fallback)).toBe(fallback);
    expect(formatWarpGateAttackError(new Error('Network Error'), fallback)).toBe(fallback);
    expect(formatWarpGateAttackError(new Error('Failed to fetch'), fallback)).toBe(fallback);
    expect(formatWarpGateAttackError(new Error('Network Error'), fallback)).not.toMatch(
      /Network Error/i,
    );
  });

  it('surfaces 403/429 without raw status codes or TypeError leakage', () => {
    expect(formatWarpGateAttackError(apiRequestError(403), fallback)).toMatch(/permission/i);
    expect(formatWarpGateAttackError(apiRequestError(403, 'attack_denied'), fallback)).toBe(
      'attack_denied',
    );
    expect(formatWarpGateAttackError(apiRequestError(429), fallback)).toMatch(/rate limit/i);
    expect(formatWarpGateAttackError(apiRequestError(429), fallback)).not.toMatch(/\b429\b/);
    expect(formatWarpGateAttackError(apiRequestError(403), fallback)).not.toMatch(/TypeError/i);
  });
});

describe('WarpGateAttackControl', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    listSector.mockReset();
    attackWarpGate.mockReset();
    refreshPlayerState.mockReset();
    getAvailableMoves.mockReset();
    refreshPlayerState.mockResolvedValue(undefined);
    getAvailableMoves.mockResolvedValue(undefined);
    mockPlayerState = { id: 'player-1', is_docked: false, is_landed: false };
    mockCurrentSector = {
      id: '11111111-1111-1111-1111-111111111111',
      sector_id: 42,
    };
    listSector.mockResolvedValue({
      gates: [
        {
          gate_id: GATE_ID,
          destination_sector_id: 99,
          destination_name: 'Rylan',
          owner_name: 'Rival',
        },
      ],
      beacons: [
        {
          beacon_id: BEACON_ID,
          destination_sector_id: 7,
          destination_name: 'Fringe',
        },
      ],
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it('shows attack actions when gates and beacons are present', async () => {
    await act(async () => {
      root.render(<WarpGateAttackControl />);
      await flush();
    });
    expect(listSector).toHaveBeenCalledWith(42);
    expect(container.querySelector(`[data-testid="warp-gate-attack-${GATE_ID}"]`)).toBeTruthy();
    expect(container.querySelector(`[data-testid="warp-beacon-attack-${BEACON_ID}"]`)).toBeTruthy();
    expect(container.textContent).toMatch(/warp structure/i);
  });

  it('calls attackWarpGate with gateId and surfaces destroy/salvage on success', async () => {
    attackWarpGate.mockResolvedValue({
      success: true,
      message: 'Gate collapsed.',
      destroyed: true,
      salvageGranted: { ORE: 500 },
      turnsConsumed: 75,
    });
    listSector
      .mockResolvedValueOnce({
        gates: [{ gate_id: GATE_ID, destination_sector_id: 99 }],
        beacons: [],
      })
      .mockResolvedValueOnce({ gates: [], beacons: [] });

    await act(async () => {
      root.render(<WarpGateAttackControl />);
      await flush();
    });

    const btn = container.querySelector(
      `[data-testid="warp-gate-attack-${GATE_ID}"]`,
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await flush();
    });

    expect(attackWarpGate).toHaveBeenCalledWith({ gateId: GATE_ID });
    expect(refreshPlayerState).toHaveBeenCalled();
    expect(getAvailableMoves).toHaveBeenCalled();
    const text = container.querySelector('[data-testid="warp-gate-attack-msg"]')?.textContent;
    expect(text).toMatch(/Gate collapsed/i);
    expect(text).toMatch(/destroyed/i);
    expect(text).toMatch(/Salvage/i);
  });

  it('calls attackWarpGate with beaconId on beacon attack', async () => {
    attackWarpGate.mockResolvedValue({
      success: true,
      message: 'Beacon destroyed.',
      destroyed: true,
    });
    listSector.mockResolvedValue({
      gates: [],
      beacons: [{ beacon_id: BEACON_ID, destination_sector_id: 7 }],
    });

    await act(async () => {
      root.render(<WarpGateAttackControl />);
      await flush();
    });

    const btn = container.querySelector(
      `[data-testid="warp-beacon-attack-${BEACON_ID}"]`,
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await flush();
    });

    expect(attackWarpGate).toHaveBeenCalledWith({ beaconId: BEACON_ID });
  });

  it('does not claim success when API returns success:false', async () => {
    attackWarpGate.mockResolvedValue({
      success: false,
      message: 'Engagement aborted.',
    });

    await act(async () => {
      root.render(<WarpGateAttackControl />);
      await flush();
    });

    const btn = container.querySelector(
      `[data-testid="warp-gate-attack-${GATE_ID}"]`,
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await flush();
    });

    const msg = container.querySelector('[data-testid="warp-gate-attack-msg"]');
    expect(msg?.className).toMatch(/err/);
    expect(msg?.textContent).toMatch(/Engagement aborted/i);
    expect(refreshPlayerState).not.toHaveBeenCalled();
  });

  it('surfaces 403 attack errors with player-safe copy', async () => {
    attackWarpGate.mockRejectedValue(apiRequestError(403));

    await act(async () => {
      root.render(<WarpGateAttackControl />);
      await flush();
    });

    const btn = container.querySelector(
      `[data-testid="warp-gate-attack-${GATE_ID}"]`,
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await flush();
    });

    const text = container.querySelector('[data-testid="warp-gate-attack-msg"]')?.textContent;
    expect(text).toMatch(/permission/i);
    expect(text).not.toMatch(/\b403\b/);
  });

  it('surfaces 429 attack errors with player-safe copy', async () => {
    attackWarpGate.mockRejectedValue(apiRequestError(429));

    await act(async () => {
      root.render(<WarpGateAttackControl />);
      await flush();
    });

    const btn = container.querySelector(
      `[data-testid="warp-gate-attack-${GATE_ID}"]`,
    ) as HTMLButtonElement;
    await act(async () => {
      btn.click();
      await flush();
    });

    const text = container.querySelector('[data-testid="warp-gate-attack-msg"]')?.textContent;
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/\b429\b/);
  });
});
