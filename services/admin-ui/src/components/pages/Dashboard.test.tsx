import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Dashboard from './Dashboard';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

const byUrl = (url: string) => {
  if (url.includes('/status/database/detailed')) {
    return { data: { status: 'healthy', connected: true, response_time: 12 } };
  }
  if (url.includes('/status/ai/providers')) {
    return { data: { status: 'healthy', summary: { healthy: 2, total: 2 } } };
  }
  if (url.includes('/admin/stats')) {
    return {
      data: {
        total_players: 10,
        active_sessions: 3,
        new_players_today: 1,
        new_players_week: 2,
        total_sectors: 100,
        total_planets: 40,
        total_ports: 8,
        total_ships: 20,
        total_warp_tunnels: 5,
      },
    };
  }
  if (url.includes('/admin/audit/logs')) {
    return { data: { logs: [] } };
  }
  if (url.includes('/status/')) {
    return { data: { status: 'healthy' } };
  }
  return { data: {} };
};

describe('Dashboard (LEG-233)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.get).mockImplementation(async (url: string) => byUrl(url));
  });

  it('loads aggregate stats via shared api with no hand-rolled Bearer headers', async () => {
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Galaxy Statistics')).toBeTruthy();
    });

    const urls = vi.mocked(api.get).mock.calls.map(([u]) => String(u));
    expect(urls).toEqual(
      expect.arrayContaining([
        '/api/v1/status/database/detailed',
        '/api/v1/status/ai/providers',
        '/api/v1/status/',
        '/api/v1/admin/stats',
        '/api/v1/admin/audit/logs',
      ])
    );
    expect(screen.getByText('10')).toBeTruthy();
    expect(screen.queryByText(/Unable to load dashboard data/)).toBeNull();
  });
});
