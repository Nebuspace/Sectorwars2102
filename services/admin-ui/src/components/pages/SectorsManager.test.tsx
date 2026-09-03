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
    expect(alert).not.toMatch(/not implemented|unimplemented/i);
  });

  it('surfaces scope denial on 403 without generic load failure', async () => {
    vi.mocked(api.get).mockRejectedValue(
      Object.assign(axiosError(403), {
        response: {
          status: 403,
          data: { detail: 'Missing scope admin.universe.view' },
        },
      }),
    );

    render(<SectorsManager />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/admin\.universe\.view|Missing scope/i);
    });
  });

  it('shows rate-limit copy on 429', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<SectorsManager />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
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

  it('surfaces honest fallback on sectors load TypeError/network collapse (LEG-3031)', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<SectorsManager />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Gameserver unreachable — network error fetching sectors/i);
    expect(alert).not.toMatch(/TypeError/i);
    expect(alert).not.toMatch(/Failed to fetch/i);
  });
});

describe('SectorsManager axios Network Error densify (LEG-3392)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios-shaped Network Error on load to honest fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<SectorsManager />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Gameserver unreachable — network error fetching sectors/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
  });

  it('preserves scope detail on 403 load (not collapsed away)', async () => {
    vi.mocked(api.get).mockRejectedValue(
      Object.assign(axiosError(403), {
        response: {
          status: 403,
          data: { detail: 'Missing scope admin.universe.view' },
        },
      }),
    );

    render(<SectorsManager />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(
        /admin\.universe\.view|Missing scope/i,
      );
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toContain('Missing scope admin.universe.view');
  });
});

function listSector(overrides: Record<string, unknown> = {}) {
  return {
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
    has_warp_tunnel: true,
    player_count: 0,
    controlling_faction: null,
    ...overrides,
  };
}

describe('SectorsManager pirate holding badge (LEG-4181)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('shows a Holding badge only when has_pirate_holding is true', async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: { sectors: [listSector({ name: 'Corsair', has_pirate_holding: true })], total: 1 },
    });

    render(<SectorsManager />);

    await waitFor(() => {
      expect(screen.getByText('Corsair')).toBeTruthy();
    });
    expect(screen.getByTitle('Pirate Holding')).toHaveTextContent('Holding');
    expect(vi.mocked(api.get).mock.calls.every(([url]) => !String(url).includes('pirate-holdings'))).toBe(
      true,
    );
  });

  it('does not show a Holding badge when has_pirate_holding is false', async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: { sectors: [listSector({ name: 'Beta', has_pirate_holding: false })], total: 1 },
    });

    render(<SectorsManager />);

    await waitFor(() => {
      expect(screen.getByText('Beta')).toBeTruthy();
    });
    expect(screen.queryByTitle('Pirate Holding')).toBeNull();
    expect(screen.queryByText('Holding')).toBeNull();
  });

  it('does not show a Holding badge when has_pirate_holding is omitted', async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: { sectors: [listSector({ name: 'Gamma' })], total: 1 },
    });

    render(<SectorsManager />);

    await waitFor(() => {
      expect(screen.getByText('Gamma')).toBeTruthy();
    });
    expect(screen.queryByTitle('Pirate Holding')).toBeNull();
    expect(screen.queryByText('Holding')).toBeNull();
  });
});


