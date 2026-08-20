import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import CentralNexusManager from './CentralNexusManager';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: { get: vi.fn() },
}));

describe('CentralNexusManager (LEG-212 shared api)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/nexus/status')) {
        return {
          data: {
            exists: true,
            status: 'active',
            total_sectors: 10,
            total_ports: 2,
            total_planets: 3,
          },
        };
      }
      if (String(url).includes('/nexus/clusters')) {
        return { data: [] };
      }
      if (String(url).includes('/nexus/stats')) {
        return {
          data: {
            total_sectors: 10,
            total_ports: 2,
            total_planets: 3,
            total_warp_gates: 1,
            active_players: null,
            daily_traffic: null,
            clusters: [],
          },
        };
      }
      return { data: {} };
    });
  });

  it('loads nexus surfaces via shared api', async () => {
    render(<CentralNexusManager />);
    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith('/api/v1/nexus/status');
    });
    const urls = vi.mocked(api.get).mock.calls.map(([u]) => String(u));
    expect(urls).toEqual(
      expect.arrayContaining([
        '/api/v1/nexus/status',
        '/api/v1/nexus/clusters',
        '/api/v1/nexus/stats',
      ])
    );
    expect(screen.getByRole('heading', { name: 'Central Nexus Management' })).toBeTruthy();
  });
});
