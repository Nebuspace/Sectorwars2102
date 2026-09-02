/**
 * combatAPI wrappers — LEG-3968 attack-sector-drones + LEG-4116 attack-warp-gate.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../apiClient', () => ({
  default: {
    request: vi.fn(),
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

import apiClient from '../apiClient';
import { combatAPI } from '../api';

const post = apiClient.post as ReturnType<typeof vi.fn>;

const jsonHeaders = expect.objectContaining({
  headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
});

describe('combatAPI.attackSectorDrones (LEG-3968)', () => {
  beforeEach(() => {
    post.mockReset();
  });

  it('POSTs /api/v1/combat/attack-sector-drones and returns server DTO', async () => {
    post.mockResolvedValue({
      data: {
        success: true,
        message: 'Cleared hostile drones.',
        dronesDestroyed: 3,
        dronesRemaining: 0,
        turnsConsumed: 2,
        turnsRemaining: 18,
        combatLogId: 'log-1',
      },
    });

    await expect(combatAPI.attackSectorDrones()).resolves.toEqual({
      success: true,
      message: 'Cleared hostile drones.',
      dronesDestroyed: 3,
      dronesRemaining: 0,
      turnsConsumed: 2,
      turnsRemaining: 18,
      combatLogId: 'log-1',
    });

    expect(post).toHaveBeenCalledWith(
      '/api/v1/combat/attack-sector-drones',
      undefined,
      jsonHeaders,
    );
  });
});

describe('combatAPI.attackWarpGate (LEG-4116)', () => {
  beforeEach(() => {
    post.mockReset();
  });

  it('POSTs /api/v1/combat/attack-warp-gate with gateId and returns DTO', async () => {
    post.mockResolvedValue({
      data: {
        success: true,
        message: 'Gate collapsed.',
        destroyed: true,
        gateHpRemaining: 0,
        salvageGranted: { ORE: 500 },
        turnsConsumed: 75,
        turnsRemaining: 10,
      },
    });

    await expect(combatAPI.attackWarpGate({ gateId: 'gate-1' })).resolves.toEqual({
      success: true,
      message: 'Gate collapsed.',
      destroyed: true,
      gateHpRemaining: 0,
      salvageGranted: { ORE: 500 },
      turnsConsumed: 75,
      turnsRemaining: 10,
    });

    expect(post).toHaveBeenCalledWith(
      '/api/v1/combat/attack-warp-gate',
      JSON.stringify({ gateId: 'gate-1' }),
      jsonHeaders,
    );
  });

  it('POSTs /api/v1/combat/attack-warp-gate with beaconId and returns DTO', async () => {
    post.mockResolvedValue({
      data: {
        success: true,
        message: 'Beacon destroyed.',
        destroyed: true,
        salvageGranted: { EQUIPMENT: 250 },
        turnsConsumed: 75,
        turnsRemaining: 5,
      },
    });

    await expect(combatAPI.attackWarpGate({ beaconId: 'beacon-1' })).resolves.toEqual({
      success: true,
      message: 'Beacon destroyed.',
      destroyed: true,
      salvageGranted: { EQUIPMENT: 250 },
      turnsConsumed: 75,
      turnsRemaining: 5,
    });

    expect(post).toHaveBeenCalledWith(
      '/api/v1/combat/attack-warp-gate',
      JSON.stringify({ beaconId: 'beacon-1' }),
      jsonHeaders,
    );
  });
});
