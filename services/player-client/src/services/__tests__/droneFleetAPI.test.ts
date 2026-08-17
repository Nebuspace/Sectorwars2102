/**
 * droneFleetAPI route/payload contract (LEG-277).
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
import { droneFleetAPI } from '../api';

const get = apiClient.get as ReturnType<typeof vi.fn>;
const post = apiClient.post as ReturnType<typeof vi.fn>;

const jsonHeaders = expect.objectContaining({
  headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
});

describe('droneFleetAPI (LEG-277)', () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
  });

  it('getTypes GETs /api/v1/drones/types', async () => {
    get.mockResolvedValue({ data: { types: [] } });
    await droneFleetAPI.getTypes();
    expect(get).toHaveBeenCalledWith('/api/v1/drones/types', jsonHeaders);
  });

  it('create POSTs drone_type (+ optional name)', async () => {
    post.mockResolvedValue({ data: { id: 'd1' } });
    await droneFleetAPI.create({ drone_type: 'mining', name: 'Rig' });
    expect(post).toHaveBeenCalledWith(
      '/api/v1/drones/',
      JSON.stringify({ drone_type: 'mining', name: 'Rig' }),
      jsonHeaders,
    );
  });

  it('repair POSTs repair_amount', async () => {
    post.mockResolvedValue({ data: { id: 'd1', health: 50 } });
    await droneFleetAPI.repair('d1', 15);
    expect(post).toHaveBeenCalledWith(
      '/api/v1/drones/d1/repair',
      JSON.stringify({ repair_amount: 15 }),
      jsonHeaders,
    );
  });

  it('upgrade POSTs with no body', async () => {
    post.mockResolvedValue({ data: { id: 'd1', level: 2 } });
    await droneFleetAPI.upgrade('d1');
    expect(post).toHaveBeenCalledWith(
      '/api/v1/drones/d1/upgrade',
      undefined,
      jsonHeaders,
    );
  });
});
