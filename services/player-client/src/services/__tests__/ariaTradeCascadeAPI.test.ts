/**
 * ariaTradeCascadeAPI — POST /api/v1/ai/trade-cascade wiring (LEG-725).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../apiClient', () => ({
  default: {
    post: vi.fn(),
  },
}));

import apiClient from '../apiClient';
import { ariaTradeCascadeAPI } from '../api';

const post = apiClient.post as ReturnType<typeof vi.fn>;

describe('ariaTradeCascadeAPI', () => {
  beforeEach(() => {
    post.mockReset();
  });

  it('posts start_sector_id, target_profit, and max_jumps to /api/v1/ai/trade-cascade', async () => {
    post.mockResolvedValueOnce({
      data: {
        cascade_id: 'cascade-1',
        player_id: 'p1',
        total_profit: 1200,
        total_jumps: 2,
        profit_per_jump: 600,
        confidence: 0.8,
        steps: [],
      },
    });

    const result = await ariaTradeCascadeAPI.planTradeCascade({
      start_sector_id: 'sector-a',
      target_profit: 1000,
      max_jumps: 4,
    });

    expect(post).toHaveBeenCalledWith(
      '/api/v1/ai/trade-cascade',
      JSON.stringify({
        start_sector_id: 'sector-a',
        target_profit: 1000,
        max_jumps: 4,
      }),
      expect.objectContaining({
        headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
      }),
    );
    expect(result).toMatchObject({ cascade_id: 'cascade-1', total_profit: 1200 });
  });

  it('defaults max_jumps to 5 when omitted', async () => {
    post.mockResolvedValueOnce({
      data: { error: 'no_exploration_map', message: 'Explore more sectors to plan trade routes' },
    });

    await ariaTradeCascadeAPI.planTradeCascade({
      start_sector_id: 'sector-b',
      target_profit: 500,
    });

    expect(post).toHaveBeenCalledWith(
      '/api/v1/ai/trade-cascade',
      JSON.stringify({
        start_sector_id: 'sector-b',
        target_profit: 500,
        max_jumps: 5,
      }),
      expect.any(Object),
    );
  });
});
