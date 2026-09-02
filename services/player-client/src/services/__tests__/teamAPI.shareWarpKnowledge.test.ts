/**
 * teamAPI.shareWarpKnowledge — LEG-4118.
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
import { teamAPI } from '../api';

const post = apiClient.post as ReturnType<typeof vi.fn>;

const jsonHeaders = expect.objectContaining({
  headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
});

describe('teamAPI.shareWarpKnowledge (LEG-4118)', () => {
  beforeEach(() => {
    post.mockReset();
  });

  it('POSTs /api/v1/teams/{id}/share-warp-knowledge and returns DTO', async () => {
    post.mockResolvedValue({
      data: { shared_warp_count: 4, recipient_count: 2, rows_created: 7 },
    });
    await expect(teamAPI.shareWarpKnowledge('team-1')).resolves.toEqual({
      shared_warp_count: 4,
      recipient_count: 2,
      rows_created: 7,
    });
    expect(post).toHaveBeenCalledWith(
      '/api/v1/teams/team-1/share-warp-knowledge',
      undefined,
      jsonHeaders,
    );
  });
});
