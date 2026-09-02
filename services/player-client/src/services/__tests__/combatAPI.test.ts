/**
 * combatAPI wrappers — LEG-3968 attack-sector-drones.
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
