import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import SystemHealthStatus from './SystemHealthStatus';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

function mockSuccessfulProbes() {
  return (url: string) => {
    if (url === '/api/v1/status/') {
      return Promise.resolve({
        data: { active_connections: 1, admin_connections: 0 },
      });
    }
    if (url === '/api/v1/status/ai/providers') {
      return Promise.resolve({
        data: {
          status: 'healthy',
          summary: { healthy: 1, total: 1 },
          providers: {},
          response_time: 5,
          last_check: new Date().toISOString(),
        },
      });
    }
    if (url === '/api/v1/status/database/detailed') {
      return Promise.resolve({
        data: {
          status: 'healthy',
          connected: true,
          response_time: 10,
          last_check: new Date().toISOString(),
        },
      });
    }
    return Promise.reject(new Error(`unexpected GET ${url}`));
  };
}

/**
 * LEG-3700 Soft-ORDER — SystemHealthStatus probe TypeError/Network Error densify.
 */
describe('SystemHealthStatus typeErrorHonesty densify (LEG-3700)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on server-status probe without leaking raw transport text', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url === '/api/v1/status/') {
        return Promise.reject(new Error('Network Error'));
      }
      return mockSuccessfulProbes()(url);
    });

    render(<SystemHealthStatus />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Gameserver unreachable|server status/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on server-status probe without leaking transport text', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url === '/api/v1/status/') {
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      return mockSuccessfulProbes()(url);
    });

    render(<SystemHealthStatus />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Gameserver unreachable|server status/i);
    expect(alert).not.toMatch(/Failed to fetch/i);
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('collapses axios Network Error on database probe without leaking raw transport text', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url === '/api/v1/status/database/detailed') {
        return Promise.reject(new Error('Network Error'));
      }
      return mockSuccessfulProbes()(url);
    });

    render(<SystemHealthStatus />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Gameserver unreachable|database health/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on database probe without leaking transport text', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url === '/api/v1/status/database/detailed') {
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      return mockSuccessfulProbes()(url);
    });

    render(<SystemHealthStatus />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Gameserver unreachable|database health/i);
    expect(alert).not.toMatch(/Failed to fetch/i);
    expect(alert).not.toMatch(/TypeError/i);
  });
});
