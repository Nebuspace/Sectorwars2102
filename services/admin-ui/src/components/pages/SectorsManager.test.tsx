import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import SectorsManager from './SectorsManager';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

vi.mock('../../contexts/AdminContext', () => ({
  useAdmin: () => ({
    galaxyState: { id: 'g1', name: 'Test Galaxy' },
    regions: [],
    clusters: [],
    loadGalaxyInfo: vi.fn(),
    loadRegions: vi.fn(),
    loadClusters: vi.fn(),
    isLoading: false,
    error: null,
  }),
}));

vi.mock('../universe/SectorEditModal', () => ({
  default: () => null,
}));

const axiosError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status },
  });

describe('SectorsManager (LEG-399)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('reports a 404 as routing/auth/proxy fault, never as unimplemented', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(404));

    render(<SectorsManager />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toContain('404');
    expect(alert).toMatch(/auth\/scope|base URL|proxy/i);
    expect(alert).toMatch(/ships on the gameserver/i);
    expect(alert).not.toMatch(/not implemented|unimplemented/i);
    expect(api.get).toHaveBeenCalledWith(
      '/api/v1/admin/sectors',
      expect.objectContaining({ params: expect.any(Object) })
    );
  });

  it('loads sectors on success without dishonest not-implemented copy', async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: {
        sectors: [
          {
            id: 's1',
            sector_id: 1,
            name: 'Alpha',
            type: 'normal',
            cluster_id: 'c1',
            x_coord: 0,
            y_coord: 0,
            z_coord: 0,
            hazard_level: 0,
            is_discovered: true,
            has_port: false,
            has_planet: false,
            has_warp_tunnel: false,
            player_count: 0,
            controlling_faction: null,
          },
        ],
        total: 1,
      },
    });

    render(<SectorsManager />);

    await waitFor(() => {
      expect(screen.getByText('Alpha')).toBeTruthy();
    });

    expect(screen.queryByText(/not implemented/i)).toBeNull();
  });
});
