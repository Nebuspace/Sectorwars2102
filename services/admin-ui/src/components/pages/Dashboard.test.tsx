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

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: null, logout: vi.fn() }),
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

  it('surfaces audit 403 as scope denial, not gameserver-down', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/admin/audit/logs')) {
        throw Object.assign(new Error('HTTP 403'), { response: { status: 403 } });
      }
      return byUrl(url);
    });

    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText(/Access denied/i)).toBeTruthy();
    });

    const msg = screen.getByText(/Access denied/i).textContent ?? '';
    expect(msg).toMatch(/admin\.audit\.view|AUDIT_VIEW/i);
    expect(msg).not.toMatch(/Unable to load recent audit events/i);
  });

  it('surfaces audit 429 as admin rate-limit copy', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/admin/audit/logs')) {
        throw Object.assign(new Error('HTTP 429'), { response: { status: 429 } });
      }
      return byUrl(url);
    });

    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });

    expect(screen.getByText(/rate limit/i).textContent).not.toMatch(/Audit log request failed \(429\)/);
  });

  it('surfaces stats 403 as PLAYERS_VIEW denial, not silent unavailable', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/admin/stats')) {
        throw Object.assign(new Error('HTTP 403'), { response: { status: 403 } });
      }
      return byUrl(url);
    });

    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const msg = screen.getByRole('alert').textContent ?? '';
    expect(msg).toMatch(/PLAYERS_VIEW|players view/i);
    expect(msg).not.toMatch(/Unable to load dashboard data/i);
  });

  it('surfaces stats 429 as admin rate-limit copy', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/admin/stats')) {
        throw Object.assign(new Error('HTTP 429'), { response: { status: 429 } });
      }
      return byUrl(url);
    });

    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const msg = screen.getByRole('alert').textContent ?? '';
    expect(msg).toMatch(/rate limit/i);
    expect(msg).not.toMatch(/Unable to load dashboard data/i);
  });

  it('surfaces honest fallback on audit TypeError/network collapse (LEG-3028)', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/admin/audit/logs')) {
        throw new TypeError('Failed to fetch');
      }
      return byUrl(url);
    });

    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText(/Unable to load recent audit events/i)).toBeTruthy();
    });

    const msg = screen.getByText(/Unable to load recent audit events/i).textContent ?? '';
    expect(msg).not.toMatch(/Failed to fetch/i);
    expect(msg).not.toMatch(/TypeError/i);
  });
});
