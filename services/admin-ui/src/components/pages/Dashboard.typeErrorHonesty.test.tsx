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

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

function mockSuccessfulDashboardGets(url: string) {
  if (url === '/api/v1/status/database/detailed') {
    return Promise.resolve({
      data: { status: 'healthy', connected: true, response_time: 12 },
    });
  }
  if (url === '/api/v1/status/ai/providers') {
    return Promise.resolve({
      data: { status: 'healthy', summary: { healthy: 1, total: 2 } },
    });
  }
  if (url === '/api/v1/status/') {
    return Promise.resolve({ data: { status: 'healthy' } });
  }
  if (url === '/api/v1/admin/stats') {
    return Promise.resolve({
      data: {
        total_players: 100,
        active_sessions: 10,
        new_players_today: 2,
        new_players_week: 5,
        total_sectors: 50,
        total_planets: 200,
        total_ports: 30,
        total_ships: 500,
        total_warp_tunnels: 15,
      },
    });
  }
  if (url === '/api/v1/admin/audit/logs') {
    return Promise.resolve({ data: { logs: [] } });
  }
  return Promise.reject(new Error(`unexpected GET ${url}`));
}

function renderDashboard() {
  return render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>,
  );
}

/**
 * LEG-3660 Soft-ORDER — Dashboard TypeError/Network Error densify.
 */
describe('Dashboard typeErrorHonesty densify (LEG-3660)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on admin stats load without leaking transport text', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url === '/api/v1/admin/stats') {
        return Promise.reject(new Error('Network Error'));
      }
      return mockSuccessfulDashboardGets(url);
    });

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/dashboard stats/i);
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Gameserver unreachable|Unable to load dashboard stats/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on admin stats load without leaking transport text', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url === '/api/v1/admin/stats') {
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      return mockSuccessfulDashboardGets(url);
    });

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/dashboard stats/i);
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Gameserver unreachable|Unable to load dashboard stats/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses axios Network Error on audit logs load without leaking transport text', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url === '/api/v1/admin/audit/logs') {
        return Promise.reject(new Error('Network Error'));
      }
      return mockSuccessfulDashboardGets(url);
    });

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByText(/Unable to load recent audit events/i)).toBeTruthy();
    });
    const text = screen.getByText(/Unable to load recent audit events/i).textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on audit logs load without leaking transport text', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url === '/api/v1/admin/audit/logs') {
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      return mockSuccessfulDashboardGets(url);
    });

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByText(/Unable to load recent audit events/i)).toBeTruthy();
    });
    const text = screen.getByText(/Unable to load recent audit events/i).textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});
