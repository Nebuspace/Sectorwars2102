import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import PlayerAnalytics from './PlayerAnalytics';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

vi.mock('./RankingLeaderboardPanel', () => ({
  default: () => <div data-testid="ranking-stub" />,
}));

vi.mock('./components/PlayerSearchAndFilter', () => ({
  default: () => <div data-testid="filters-stub" />,
}));

vi.mock('../admin/PlayerDetailEditor', () => ({
  default: () => null,
}));

vi.mock('../admin/BulkOperationPanel', () => ({
  default: () => null,
}));

vi.mock('../admin/PlayerAssetManager', () => ({
  default: () => null,
}));

vi.mock('../admin/EmergencyOperationsPanel', () => ({
  default: () => null,
}));

function mockApis({
  retention,
  sessionTime,
  analyticsFail = false,
}: {
  retention?: number | null;
  sessionTime?: number | null;
  analyticsFail?: boolean;
}) {
  vi.mocked(api.get).mockImplementation((url: string) => {
    if (typeof url === 'string' && url.startsWith('/api/v1/admin/players/comprehensive')) {
      return Promise.resolve({
        data: {
          players: [],
          total_count: 0,
        },
      });
    }
    if (url === '/api/v1/admin/analytics/real-time') {
      if (analyticsFail) {
        return Promise.reject(new Error('analytics down'));
      }
      const payload: Record<string, unknown> = {
        total_active_players: 10,
        total_credits_circulation: 1000,
        new_players_today: 2,
        players_online_now: 3,
        total_players: 50,
        suspicious_activity_alerts: 0,
      };
      if (retention !== undefined && retention !== null) {
        payload.player_retention_rate_7d = retention;
      }
      // omit field entirely when retention === null → asCount → null
      if (sessionTime !== undefined && sessionTime !== null) {
        payload.average_session_time = sessionTime;
      }
      // omit field entirely when sessionTime === null → asCount → null
      return Promise.resolve({ data: { data: payload } });
    }
    if (url === '/api/v1/admin/regions') {
      return Promise.resolve({ data: { regions: [] } });
    }
    // LEG-880 mounts ReEngagementQueuePanel under PlayerAnalytics
    if (typeof url === 'string' && url.startsWith('/api/v1/admin/re-engagement/summary')) {
      return Promise.resolve({
        data: { open: 0, contacted: 0, resolved: 0, total: 0, open_share: null },
      });
    }
    if (typeof url === 'string' && url.startsWith('/api/v1/admin/re-engagement')) {
      return Promise.resolve({ data: { items: [], total: 0 } });
    }
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
}

describe('PlayerAnalytics retention rate card (LEG-376)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('shows 7d retention rate when analytics returns player_retention_rate_7d', async () => {
    mockApis({ retention: 72.5 });

    render(<PlayerAnalytics />);

    const card = await waitFor(() => screen.getByTestId('player-metrics-retention-rate'));
    expect(card.textContent).toContain('72.5%');
    expect(card.textContent).toMatch(/7-day retention/i);
    expect(card.className).not.toContain('stat-not-tracked');
    expect(card.textContent).not.toMatch(/No retention telemetry surfaced yet/);
  });

  it('demotes to em-dash when retention field is absent', async () => {
    mockApis({ retention: null });

    render(<PlayerAnalytics />);

    const card = await waitFor(() => screen.getByTestId('player-metrics-retention-rate'));
    expect(card.querySelector('.dashboard-stat-value')?.textContent).toMatch(/—|–|-/);
    expect(card.className).toContain('stat-not-tracked');
    expect(card.textContent).toMatch(/Retention rate unavailable/i);
  });

  it('demotes when analytics endpoint is unavailable', async () => {
    mockApis({ retention: 72.5, analyticsFail: true });

    render(<PlayerAnalytics />);

    const card = await waitFor(() => screen.getByTestId('player-metrics-retention-rate'));
    expect(card.querySelector('.dashboard-stat-value')?.textContent).toMatch(/—|–|-/);
    expect(card.className).toContain('stat-not-tracked');
    expect(card.textContent).toMatch(/Analytics endpoint unavailable/i);
  });
});

describe('PlayerAnalytics session time card (LEG-386)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('shows average session time in hours when analytics returns average_session_time', async () => {
    mockApis({ sessionTime: 2.5 });

    render(<PlayerAnalytics />);

    const card = await waitFor(() => screen.getByTestId('player-metrics-session-time'));
    expect(card.textContent).toContain('2.5h');
    expect(card.textContent).toMatch(/Average \(hours\)/i);
    expect(card.className).not.toContain('stat-not-tracked');
    expect(card.textContent).not.toMatch(/No session tracking yet/);
  });

  it('demotes to em-dash when session time field is absent', async () => {
    mockApis({ sessionTime: null });

    render(<PlayerAnalytics />);

    const card = await waitFor(() => screen.getByTestId('player-metrics-session-time'));
    expect(card.querySelector('.dashboard-stat-value')?.textContent).toMatch(/—|–|-/);
    expect(card.className).toContain('stat-not-tracked');
    expect(card.textContent).toMatch(/Session time unavailable/i);
  });

  it('demotes when analytics endpoint is unavailable', async () => {
    mockApis({ sessionTime: 2.5, analyticsFail: true });

    render(<PlayerAnalytics />);

    const card = await waitFor(() => screen.getByTestId('player-metrics-session-time'));
    expect(card.querySelector('.dashboard-stat-value')?.textContent).toMatch(/—|–|-/);
    expect(card.className).toContain('stat-not-tracked');
    expect(card.textContent).toMatch(/Analytics endpoint unavailable/i);
  });

  it('surfaces comprehensive 403 as PLAYERS_VIEW denial (LEG-1255)', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (typeof url === 'string' && url.startsWith('/api/v1/admin/players/comprehensive')) {
        return Promise.reject(
          Object.assign(new Error('HTTP 403'), { response: { status: 403 } }),
        );
      }
      return Promise.resolve({ data: {} });
    });

    render(<PlayerAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/PLAYERS_VIEW/);
    });
  });

  it('surfaces comprehensive 429 as admin rate-limit (LEG-1255)', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (typeof url === 'string' && url.startsWith('/api/v1/admin/players/comprehensive')) {
        return Promise.reject(
          Object.assign(new Error('HTTP 429'), { response: { status: 429 } }),
        );
      }
      return Promise.resolve({ data: {} });
    });

    render(<PlayerAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
  });

  it('surfaces honest fallback on comprehensive TypeError/network collapse (LEG-3064)', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (typeof url === 'string' && url.startsWith('/api/v1/admin/players/comprehensive')) {
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      return Promise.resolve({ data: {} });
    });

    render(<PlayerAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Failed to load player data/i);
    });

    const msg = screen.getByRole('alert').textContent ?? '';
    expect(msg).not.toMatch(/Failed to fetch/i);
    expect(msg).not.toMatch(/TypeError/i);
  });

  it('surfaces honest fallback on comprehensive axios Network Error (LEG-3580)', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (typeof url === 'string' && url.startsWith('/api/v1/admin/players/comprehensive')) {
        return Promise.reject(new Error('Network Error'));
      }
      return Promise.resolve({ data: {} });
    });

    render(<PlayerAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Failed to load player data/i);
    });

    const msg = screen.getByRole('alert').textContent ?? '';
    expect(msg).not.toBe('Network Error');
    expect(msg).not.toContain('Network Error');
  });
});

describe('PlayerAnalytics regions fetch errors (LEG-2750)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces regions 403 as admin.galaxy.manage scope denial', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url === '/api/v1/admin/regions') {
        return Promise.reject(
          Object.assign(new Error('HTTP 403'), { response: { status: 403 } }),
        );
      }
      if (typeof url === 'string' && url.startsWith('/api/v1/admin/players/comprehensive')) {
        return Promise.resolve({
          data: { players: [], total_count: 0 },
        });
      }
      if (url === '/api/v1/admin/analytics/real-time') {
        return Promise.resolve({
          data: {
            data: {
              total_active_players: 10,
              total_credits_circulation: 1000,
              new_players_today: 2,
              players_online_now: 3,
              total_players: 50,
              suspicious_activity_alerts: 0,
            },
          },
        });
      }
      if (typeof url === 'string' && url.startsWith('/api/v1/admin/re-engagement/summary')) {
        return Promise.resolve({
          data: { open: 0, contacted: 0, resolved: 0, total: 0, open_share: null },
        });
      }
      if (typeof url === 'string' && url.startsWith('/api/v1/admin/re-engagement')) {
        return Promise.resolve({ data: { items: [], total: 0 } });
      }
      return Promise.reject(new Error(`unexpected GET ${url}`));
    });

    render(<PlayerAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/regions:\s*.*admin\.galaxy\.manage/);
    });
  });

  it('surfaces regions 429 as admin rate-limit', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url === '/api/v1/admin/regions') {
        return Promise.reject(
          Object.assign(new Error('HTTP 429'), { response: { status: 429 } }),
        );
      }
      if (typeof url === 'string' && url.startsWith('/api/v1/admin/players/comprehensive')) {
        return Promise.resolve({
          data: { players: [], total_count: 0 },
        });
      }
      if (url === '/api/v1/admin/analytics/real-time') {
        return Promise.resolve({
          data: {
            data: {
              total_active_players: 10,
              total_credits_circulation: 1000,
              new_players_today: 2,
              players_online_now: 3,
              total_players: 50,
              suspicious_activity_alerts: 0,
            },
          },
        });
      }
      if (typeof url === 'string' && url.startsWith('/api/v1/admin/re-engagement/summary')) {
        return Promise.resolve({
          data: { open: 0, contacted: 0, resolved: 0, total: 0, open_share: null },
        });
      }
      if (typeof url === 'string' && url.startsWith('/api/v1/admin/re-engagement')) {
        return Promise.resolve({ data: { items: [], total: 0 } });
      }
      return Promise.reject(new Error(`unexpected GET ${url}`));
    });

    render(<PlayerAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/regions:\s*.*rate limit/i);
    });
  });

  it('surfaces honest fallback on regions TypeError/network collapse (LEG-3064)', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url === '/api/v1/admin/regions') {
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      if (typeof url === 'string' && url.startsWith('/api/v1/admin/players/comprehensive')) {
        return Promise.resolve({
          data: { players: [], total_count: 0 },
        });
      }
      if (url === '/api/v1/admin/analytics/real-time') {
        return Promise.resolve({
          data: {
            data: {
              total_active_players: 10,
              total_credits_circulation: 1000,
              new_players_today: 2,
              players_online_now: 3,
              total_players: 50,
              suspicious_activity_alerts: 0,
            },
          },
        });
      }
      if (typeof url === 'string' && url.startsWith('/api/v1/admin/re-engagement/summary')) {
        return Promise.resolve({
          data: { open: 0, contacted: 0, resolved: 0, total: 0, open_share: null },
        });
      }
      if (typeof url === 'string' && url.startsWith('/api/v1/admin/re-engagement')) {
        return Promise.resolve({ data: { items: [], total: 0 } });
      }
      return Promise.reject(new Error(`unexpected GET ${url}`));
    });

    render(<PlayerAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Failed to load regions/i);
    });

    const msg = screen.getByRole('alert').textContent ?? '';
    expect(msg).not.toMatch(/Failed to fetch/i);
    expect(msg).not.toMatch(/TypeError/i);
  });

  it('surfaces honest fallback on regions axios Network Error (LEG-3580)', async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url === '/api/v1/admin/regions') {
        return Promise.reject(new Error('Network Error'));
      }
      if (typeof url === 'string' && url.startsWith('/api/v1/admin/players/comprehensive')) {
        return Promise.resolve({
          data: { players: [], total_count: 0 },
        });
      }
      if (url === '/api/v1/admin/analytics/real-time') {
        return Promise.resolve({
          data: {
            data: {
              total_active_players: 10,
              total_credits_circulation: 1000,
              new_players_today: 2,
              players_online_now: 3,
              total_players: 50,
              suspicious_activity_alerts: 0,
            },
          },
        });
      }
      if (typeof url === 'string' && url.startsWith('/api/v1/admin/re-engagement/summary')) {
        return Promise.resolve({
          data: { open: 0, contacted: 0, resolved: 0, total: 0, open_share: null },
        });
      }
      if (typeof url === 'string' && url.startsWith('/api/v1/admin/re-engagement')) {
        return Promise.resolve({ data: { items: [], total: 0 } });
      }
      return Promise.reject(new Error(`unexpected GET ${url}`));
    });

    render(<PlayerAnalytics />);

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Failed to load regions/i);
    });

    const msg = screen.getByRole('alert').textContent ?? '';
    expect(msg).not.toBe('Network Error');
    expect(msg).not.toContain('Network Error');
  });
});
